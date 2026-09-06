"""SQLite storage for SNU classes and their stacked time slots.

A class is unique per (year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd).
Each time cell the class occupies lives in class_slots; cells are discovered
one slot-query at a time and stacked here (INSERT OR IGNORE on the unique
(class_id, slot_code)). The temporary timetable is client-side only and is
intentionally NOT stored here.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "classes.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS terms (
    term   TEXT NOT NULL,            -- 20-char cmmnCd (year-independent code!)
    year   TEXT NOT NULL,
    label  TEXT NOT NULL,            -- "2026 2학기"
    PRIMARY KEY (year, term)         -- composite: the same code recurs every year
);

CREATE TABLE IF NOT EXISTS classes (
    id             INTEGER PRIMARY KEY,
    term           TEXT NOT NULL,    -- shtm_fg + deta_shtm_fg (joins terms.term)
    year           TEXT NOT NULL,
    shtm_fg        TEXT NOT NULL,
    deta_shtm_fg   TEXT NOT NULL,
    sbjt_cd        TEXT NOT NULL,
    lt_no          TEXT NOT NULL,
    subh_cd        TEXT NOT NULL,
    name           TEXT NOT NULL,
    professor      TEXT,
    college        TEXT,             -- 개설대학 (단과대학)
    department     TEXT,
    classification TEXT,             -- JSON array, e.g. ["학사","전선"]
    grade          TEXT,             -- 학년: '1학년'..'4학년', '0'=전학년/무관
    credits        INTEGER,
    quota          INTEGER,             -- 총정원 (the number outside the parens)
    quota_returning INTEGER,            -- 재학생 정원 (inside); 신입생 = quota - this
    applied        INTEGER,
    enrolled       INTEGER,
    cart           INTEGER,             -- 장바구니신청 인원 (volatile)
    room           TEXT,                -- 강의실 동-호 (e.g. '38-422'); '' when time-less
    language       TEXT,                -- 강의언어: '영어' / '한국어' / ...
    status         TEXT,                -- 개설상태: '설강' / '폐강대상'
    grading        TEXT,                -- 평가방식 (성적부여형태): 'A~F' / 'S/U' / 'S+/S/U'; NULL=미수집
    grading_switch TEXT,                -- 평가방식 전환가능여부: 'Y' / 'N'; NULL=미수집
    cancel_vacancy INTEGER,             -- 취소여석 배지: 1/0; NULL=미수집 (지정 시간대 신청 대상)
    UNIQUE(year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd)
);

CREATE TABLE IF NOT EXISTS class_slots (
    class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    day_index  INTEGER,             -- 0=Mon .. 6=Sun
    period     INTEGER,             -- derived: start hour - 8 (08:00 -> 0)
    start_time TEXT,                -- "09:00"
    end_time   TEXT,                -- "10:15" (exact, from the Excel 수업교시 column)
    UNIQUE(class_id, day_index, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,      -- running | done | error
    message     TEXT,
    terms       TEXT,               -- JSON list of term codes crawled
    classes     INTEGER DEFAULT 0,
    slots       INTEGER DEFAULT 0
);

-- Enrollment time-series for the 인원 추이 page. Keyed by the STABLE class identity
-- (year, term, sbjt_cd, lt_no) — NOT classes.id — so the history survives the
-- wipe+rebuild in refresh_all (clear_terms must NOT touch this table).
CREATE TABLE IF NOT EXISTS count_samples (
    year     TEXT NOT NULL,
    term     TEXT NOT NULL,         -- shtm_fg + deta_shtm_fg (matches classes.term)
    sbjt_cd  TEXT NOT NULL,
    lt_no    TEXT NOT NULL,
    ts       TEXT NOT NULL,         -- ISO-8601 sample time (one value per refresh)
    applied  INTEGER,
    cart     INTEGER,
    enrolled INTEGER,
    quota    INTEGER,
    cancel_vacancy INTEGER              -- 취소여석 배지 at sample time (1/0; NULL=미수집)
);

-- One row per collection pass, so the trend's time axis survives delta storage:
-- a pass where nothing changed writes no samples at all, and the flags record
-- WHICH metrics that pass looked at (a metric not collected stays a gap in the
-- chart rather than being forward-filled over).
CREATE TABLE IF NOT EXISTS count_passes (
    year     TEXT NOT NULL,
    term     TEXT NOT NULL,
    ts       TEXT NOT NULL,
    applied  INTEGER NOT NULL DEFAULT 0,   -- 1 = 신청 인원 collected this pass
    cart     INTEGER NOT NULL DEFAULT 0,   -- 1 = 장바구니 collected this pass
    enrolled INTEGER NOT NULL DEFAULT 0,   -- 1 = 수강 인원 (+취소여석) collected
    full     INTEGER NOT NULL DEFAULT 0,   -- 1 = keyframe: every class written
    PRIMARY KEY (year, term, ts)
);

-- Materialised current value per class: the baseline a delta pass compares
-- against, and what the export/catalog overlay reads. Only classes whose
-- numbers actually moved are rewritten, so a pass costs ~1% of the roster in
-- writes instead of the whole thing.
CREATE TABLE IF NOT EXISTS count_latest (
    year     TEXT NOT NULL,
    term     TEXT NOT NULL,
    sbjt_cd  TEXT NOT NULL,
    lt_no    TEXT NOT NULL,
    ts       TEXT NOT NULL,              -- pass that last changed this class
    applied  INTEGER,
    cart     INTEGER,
    enrolled INTEGER,
    quota    INTEGER,
    cancel_vacancy INTEGER,
    PRIMARY KEY (year, term, sbjt_cd, lt_no)
);

-- Per-term collection status. A forced (past-semester) update sets closed=1, so
-- the trend export can mark the term 마감 and a re-force can ask before re-running.
CREATE TABLE IF NOT EXISTS count_state (
    year      TEXT NOT NULL,
    term      TEXT NOT NULL,
    closed    INTEGER DEFAULT 0,    -- 1 = semester ended, captured via a forced run
    forced_at TEXT,
    PRIMARY KEY (year, term)
);

CREATE INDEX IF NOT EXISTS idx_classes_name ON classes(name);
CREATE INDEX IF NOT EXISTS idx_classes_prof ON classes(professor);
CREATE INDEX IF NOT EXISTS idx_classes_dept ON classes(department);
CREATE INDEX IF NOT EXISTS idx_slots_cell   ON class_slots(day_index, period);
CREATE INDEX IF NOT EXISTS idx_samples_key  ON count_samples(year, term, sbjt_cd, lt_no, ts);
-- the cloud->local pull walks the tail by timestamp alone (ts > cursor);
-- idx_samples_key leads with the class identity, so without this index
-- every pull full-scans the whole sample table.
CREATE INDEX IF NOT EXISTS idx_samples_ts   ON count_samples(ts);
CREATE INDEX IF NOT EXISTS idx_passes_ts    ON count_passes(ts);
"""


# ---------------------------------------------------------------------------
# Backend adapter. THIS is the only place that knows about SQLite vs Turso.
# Switch with env DB_BACKEND=sqlite|turso (+ TURSO_* for the cloud). Every other
# module talks to the connection returned by connect(); rows are normalised to
# behave like sqlite3.Row (index AND name access, dict()-able) for both drivers,
# because libsql returns plain tuples with no row_factory.
# ---------------------------------------------------------------------------
class _Row:
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: dict, vals):
        self._cols = cols
        self._vals = vals

    def __getitem__(self, k):
        return self._vals[self._cols[k]] if isinstance(k, str) else self._vals[k]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):           # enables dict(row)
        return list(self._cols)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self._cols = None

    def _colmap(self) -> dict:
        if self._cols is None:
            desc = self._cur.description
            self._cols = {d[0]: i for i, d in enumerate(desc)} if desc else {}
        return self._cols

    def fetchone(self):
        v = self._cur.fetchone()
        return _Row(self._colmap(), v) if v is not None else None

    def fetchall(self):
        cols = self._colmap()
        return [_Row(cols, v) for v in self._cur.fetchall()]

    def __iter__(self):
        cols = self._colmap()
        for v in self._cur.fetchall():   # raw libsql Cursor isn't directly iterable
            yield _Row(cols, v)

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount


class _Conn:
    """Uniform connection over sqlite3 or libsql (Turso)."""

    def __init__(self, raw, backend: str):
        self._raw = raw
        self.backend = backend

    def execute(self, sql: str, params=()):
        return _Cursor(self._raw.execute(sql, tuple(params)))

    def executemany(self, sql: str, seq):
        return _Cursor(self._raw.executemany(sql, seq))

    def executescript(self, sql: str):
        self._raw.executescript(sql)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._raw.close()
        return False

    def sync(self):           # no-op for sqlite/local; syncs Turso embedded replicas
        fn = getattr(self._raw, "sync", None)
        if fn:
            try:
                fn()
            except Exception:
                pass          # not a replica / nothing to sync


def _connect_sqlite(path: str | Path) -> _Conn:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.execute("PRAGMA foreign_keys = ON")
    mode = raw.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if mode.lower() != "wal":
        # WAL silently falls back to rollback-journal on filesystems without
        # shared-memory support; there writers take an EXCLUSIVE lock and block
        # every reader, which surfaces as "database is locked". Fail loud instead.
        raise RuntimeError(
            f"SQLite refused WAL mode (got {mode!r}) for {path}; readers will "
            "block on the crawl writer. Move the DB to a local filesystem.")
    return _Conn(raw, "sqlite")


_REMOTE_SCHEMES = ("libsql://", "http://", "https://", "ws://", "wss://")


def _connect_libsql(path: str | Path) -> _Conn:
    import libsql
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    replica = os.environ.get("TURSO_SYNC_PATH", "").strip()
    remote = url.startswith(_REMOTE_SCHEMES)
    if remote and replica:     # embedded replica: local file kept in sync with remote Turso
        raw = libsql.connect(replica, sync_url=url, auth_token=token)
        raw.sync()
    elif remote:               # remote Turso (cloud) OR a local `turso dev` server URL
        raw = libsql.connect(url, auth_token=token)
    else:                      # LOCAL Turso: a libSQL database file, no daemon, no cloud
        local = url[5:] if url.startswith("file:") else (url or str(path))
        p = Path(local)
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = libsql.connect(str(p))
    return _Conn(raw, "libsql")


def connect(path: str | Path = DB_PATH) -> _Conn:
    backend = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
    if backend in ("turso", "libsql"):
        return _connect_libsql(path)
    return _connect_sqlite(path)


_TERM_LABEL_CASE = (
    "CASE term "
    "WHEN 'U000200001U000300001' THEN '1학기' "
    "WHEN 'U000200002U000300001' THEN '2학기' "
    "WHEN 'U000200001U000300002' THEN '여름학기' "
    "WHEN 'U000200002U000300002' THEN '겨울학기' "
    "ELSE term END"
)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # migrate old cell-crawl class_slots (slot_code, no end_time) -> exact-time
    # shape. Data is rebuilt by the next Excel refresh, so a drop is safe.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(class_slots)").fetchall()]
    if cols and "end_time" not in cols:
        conn.execute("DROP TABLE class_slots")
        conn.executescript(SCHEMA)
    # add newer columns to older catalogs; NULL until the next Excel refresh
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(classes)").fetchall()]
    if ccols and "grade" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN grade TEXT")
    if ccols and "college" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN college TEXT")
    if ccols and "quota_returning" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN quota_returning INTEGER")
    if ccols and "cart" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN cart INTEGER")
    if ccols and "room" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN room TEXT")
    if ccols and "language" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN language TEXT")
    if ccols and "status" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN status TEXT")
    if ccols and "grading" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN grading TEXT")
    if ccols and "grading_switch" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN grading_switch TEXT")
    if ccols and "cancel_vacancy" not in ccols:
        conn.execute("ALTER TABLE classes ADD COLUMN cancel_vacancy INTEGER")
    scols = [r[1] for r in conn.execute("PRAGMA table_info(count_samples)").fetchall()]
    if scols and "cancel_vacancy" not in scols:
        conn.execute("ALTER TABLE count_samples ADD COLUMN cancel_vacancy INTEGER")
    pcols = [r[1] for r in conn.execute("PRAGMA table_info(count_passes)").fetchall()]
    if pcols and "full" not in pcols:
        conn.execute("ALTER TABLE count_passes ADD COLUMN full INTEGER NOT NULL "
                     "DEFAULT 0")
    # migrate old single-PK terms (term-only) -> composite (year, term) so the
    # same code can coexist across years. Backfill labels from existing classes.
    pk = [r[1] for r in conn.execute("PRAGMA table_info(terms)").fetchall() if r[5]]
    if pk and len(pk) < 2:
        conn.execute("DROP TABLE terms")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO terms(term, year, label) "
            f"SELECT DISTINCT term, year, year || ' ' || ({_TERM_LABEL_CASE}) "
            "FROM classes")
    backfill_delta_tables(conn)
    conn.commit()


SAMPLE_METRICS = ("applied", "cart", "enrolled", "quota", "cancel_vacancy")

# How often a pass re-states every class instead of only the ones that moved.
# Overridable so a bounded window worker can be told to keyframe more often.
KEYFRAME_HOURS = int(os.environ.get("COUNT_KEYFRAME_HOURS", "24") or 24)


def backfill_delta_tables(conn: sqlite3.Connection) -> dict:
    """Derive count_passes / count_latest from a pre-delta sample history.

    Samples used to be dense — one row per class per pass — so an existing
    catalog carries the whole axis and every current value inside
    count_samples. Reconstruct both tables once, on the first connection that
    finds them empty; a fresh (cloud) database has nothing to reconstruct and
    this is a no-op. Historical rows are never rewritten or deleted.
    """
    have_samples = conn.execute("SELECT 1 FROM count_samples LIMIT 1").fetchone()
    if not have_samples:
        return {"passes": 0, "latest": 0}
    if conn.execute("SELECT 1 FROM count_passes LIMIT 1").fetchone():
        return {"passes": 0, "latest": 0}

    conn.execute(
        "INSERT OR IGNORE INTO count_passes (year, term, ts, applied, cart, enrolled) "
        "SELECT year, term, ts, MAX(applied IS NOT NULL), MAX(cart IS NOT NULL), "
        "       MAX(enrolled IS NOT NULL) "
        "FROM count_samples GROUP BY year, term, ts")
    passes = conn.execute("SELECT COUNT(*) FROM count_passes").fetchone()[0]

    # Forward-fill per metric in timestamp order: the newest non-NULL value of
    # each column is the class's current value, which is exactly what a delta
    # pass compares against.
    state: dict[tuple, dict] = {}
    cur = conn.execute(
        "SELECT year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota, "
        "cancel_vacancy FROM count_samples ORDER BY ts")
    for row in cur:
        key = (row[0], row[1], row[2], row[3])
        entry = state.setdefault(key, {"ts": row[4]})
        entry["ts"] = row[4]
        for i, name in enumerate(SAMPLE_METRICS, start=5):
            if row[i] is not None:
                entry[name] = row[i]
    # In dense history every live class was sampled every pass, so a class
    # whose newest sample predates its term's newest pass had already left the
    # roster. Close it at the pass right after it was last seen, otherwise the
    # forward fill would resurrect it as a flat line to this day.
    axis: dict[tuple, list] = {}
    for year, term, ts in conn.execute(
            "SELECT year, term, ts FROM count_passes ORDER BY ts").fetchall():
        axis.setdefault((year, term), []).append(ts)
    tombstones, live = [], {}
    for key, value in state.items():
        term_axis = axis.get((key[0], key[1]), [])
        after = next((ts for ts in term_axis if ts > value["ts"]), None)
        if after is None:
            live[key] = value
        else:
            tombstones.append((key[0], key[1], key[2], key[3], after,
                               *(None for _ in SAMPLE_METRICS)))
    if live:
        insert_chunked(
            conn, "count_latest",
            ["year", "term", "sbjt_cd", "lt_no", "ts", *SAMPLE_METRICS],
            [(k[0], k[1], k[2], k[3], v["ts"],
              *(v.get(name) for name in SAMPLE_METRICS))
             for k, v in live.items()])
    if tombstones:
        insert_chunked(
            conn, "count_samples",
            ["year", "term", "sbjt_cd", "lt_no", "ts", *SAMPLE_METRICS],
            tombstones)
    conn.commit()
    return {"passes": passes, "latest": len(live), "retired": len(tombstones)}


def clear_all(conn: sqlite3.Connection) -> None:
    """Wipe the entire catalog (every term). For a scoped wipe use clear_terms."""
    conn.execute("DELETE FROM class_slots")
    conn.execute("DELETE FROM classes")
    conn.execute("DELETE FROM terms")
    conn.commit()


def clear_terms(conn: sqlite3.Connection,
                year_terms: list[tuple[str, str]]) -> None:
    """Wipe only the given (year, term) catalogs before re-crawling them, so a
    scoped refresh rebuilds just those semesters and leaves the rest intact."""
    for year, term in year_terms:
        conn.execute(
            "DELETE FROM class_slots WHERE class_id IN "
            "(SELECT id FROM classes WHERE year=? AND term=?)", (year, term))
        conn.execute("DELETE FROM classes WHERE year=? AND term=?", (year, term))
        conn.execute("DELETE FROM terms WHERE year=? AND term=?", (year, term))
    conn.commit()


def upsert_term(conn: sqlite3.Connection, term: str, year: str, label: str) -> None:
    conn.execute(
        "INSERT INTO terms(term, year, label) VALUES(?,?,?) "
        "ON CONFLICT(year, term) DO UPDATE SET label=excluded.label",
        (term, year, label),
    )


def upsert_class(conn: sqlite3.Connection, rec: dict) -> int:
    """Insert/update one class; return its id. Updates volatile enrollment."""
    cls = (rec["shtm_fg"] or "") + (rec["deta_shtm_fg"] or "")
    cur = conn.execute(
        """INSERT INTO classes
           (term, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd,
            name, professor, college, department, classification, grade,
            credits, quota, quota_returning, applied, enrolled, cart,
            room, language, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd)
           DO UPDATE SET
             name=excluded.name, professor=excluded.professor,
             college=excluded.college, department=excluded.department,
             classification=excluded.classification, grade=excluded.grade,
             credits=excluded.credits, quota=excluded.quota,
             quota_returning=excluded.quota_returning,
             applied=excluded.applied, enrolled=excluded.enrolled,
             cart=excluded.cart, room=excluded.room,
             language=excluded.language, status=excluded.status
        """,
        (
            cls, rec["year"], rec["shtm_fg"], rec["deta_shtm_fg"],
            rec["sbjt_cd"], rec["lt_no"], rec["subh_cd"],
            rec["name"], rec.get("professor", ""),
            rec.get("college", ""), rec.get("department", ""),
            json.dumps(rec.get("classification", []), ensure_ascii=False),
            rec.get("grade", ""),
            rec.get("credits"), rec.get("quota"), rec.get("quota_returning"),
            rec.get("applied"), rec.get("enrolled"), rec.get("cart"),
            rec.get("room", ""), rec.get("language", ""), rec.get("status", ""),
        ),
    )
    if cur.lastrowid:
        row = conn.execute(
            "SELECT id FROM classes WHERE year=? AND shtm_fg=? AND deta_shtm_fg=? "
            "AND sbjt_cd=? AND lt_no=? AND subh_cd=?",
            (rec["year"], rec["shtm_fg"], rec["deta_shtm_fg"],
             rec["sbjt_cd"], rec["lt_no"], rec["subh_cd"]),
        ).fetchone()
        return row["id"]
    return -1


def add_slot(conn: sqlite3.Connection, class_id: int, slot: dict) -> bool:
    """Attach one meeting block (day/start/end) to a class. True if newly added."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO class_slots"
        "(class_id, day_index, period, start_time, end_time)"
        " VALUES (?,?,?,?,?)",
        (class_id, slot.get("day_index"), slot.get("period"),
         slot.get("start_time"), slot.get("end_time")),
    )
    return cur.rowcount > 0


def _identity_text(value: str | None) -> str:
    """Normalize catalog/live text used only to disambiguate one code/lecture."""
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    ).casefold()


def resolve_live_candidates(
    candidates,
    subh_cd: str,
    *,
    name: str | None = None,
    professor: str | None = None,
    department: str | None = None,
) -> tuple[int | None, str]:
    """Resolve one live record against already-loaded catalog candidates."""
    for row in candidates:
        if row["subh_cd"] == subh_cd:
            return row["id"], "exact"

    if not candidates:
        return None, "missing"
    if len(candidates) == 1:
        return candidates[0]["id"], "code+lecture"

    live_fields = {
        "name": _identity_text(name),
        "professor": _identity_text(professor),
        "department": _identity_text(department),
    }

    def metadata_matches(row) -> bool:
        compared = 0
        for field, expected in live_fields.items():
            actual = _identity_text(row[field])
            # A missing catalog field is not evidence against a candidate, but
            # at least one populated comparable field must agree.
            if not expected or not actual:
                continue
            compared += 1
            if expected != actual:
                return False
        return compared > 0

    matches = [row for row in candidates if metadata_matches(row)]
    if len(matches) == 1:
        return matches[0]["id"], "metadata"
    return None, "ambiguous"


def resolve_live_class(
    conn: sqlite3.Connection,
    year: str,
    shtm_fg: str,
    deta_shtm_fg: str,
    sbjt_cd: str,
    lt_no: str,
    subh_cd: str,
    *,
    name: str | None = None,
    professor: str | None = None,
    department: str | None = None,
) -> tuple[int | None, str]:
    """Resolve a live search record to one catalog row.

    The Excel catalog does not carry SNU's live ``subh_cd`` and currently writes
    the placeholder ``000``. Prefer the complete live identity when it exists;
    otherwise use the already-documented stable code/lecture identity, but only
    when it identifies one catalog row. If that pair is duplicated, matching
    non-empty name/professor/department fields may select one row; unresolved
    ambiguity is deliberately rejected so counts cannot be written to the wrong
    class.
    """
    candidates = conn.execute(
        "SELECT id, subh_cd, name, professor, department FROM classes "
        "WHERE year=? AND shtm_fg=? AND deta_shtm_fg=? AND sbjt_cd=? AND lt_no=?",
        (year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no),
    ).fetchall()
    return resolve_live_candidates(
        candidates, subh_cd, name=name, professor=professor,
        department=department,
    )


def update_counts(conn: sqlite3.Connection, year: str, shtm_fg: str,
                  deta_shtm_fg: str, sbjt_cd: str, lt_no: str, subh_cd: str, *,
                  name: str | None = None, professor: str | None = None,
                  department: str | None = None, applied: int | None = None,
                  quota: int | None = None, enrolled: int | None = None,
                  cart: int | None = None,
                  cancel_vacancy: int | None = None) -> bool:
    """Overlay live enrollment numbers onto the resolved catalog row.

    Only volatile columns move; a None leaves the old value (COALESCE), and a
    real value (including 0) overrides the stale Excel count. Resolution first
    uses the full live identity, then a safe stable-code fallback for catalogs
    whose Excel row has a placeholder ``subh_cd``.
    """
    class_id, _method = resolve_live_class(
        conn, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd,
        name=name, professor=professor, department=department,
    )
    if class_id is None:
        return False
    cur = conn.execute(
        "UPDATE classes SET applied=COALESCE(?, applied), "
        "quota=COALESCE(?, quota), enrolled=COALESCE(?, enrolled), "
        "cart=COALESCE(?, cart), "
        "cancel_vacancy=COALESCE(?, cancel_vacancy) WHERE id=?",
        (applied, quota, enrolled, cart, cancel_vacancy, class_id),
    )
    return cur.rowcount > 0


def apply_latest_samples(conn: sqlite3.Connection, year: str, term: str) -> dict:
    """Overlay each class's current collected counts onto the catalog rows.

    The 10-minute 인원 pass runs on GitHub-hosted runners and lands in
    count_samples/count_latest (scraper/pull_counts.py merges it into the local
    catalog). The static export reads the volatile columns off `classes`, so
    without this overlay the published search rows would freeze at the last
    local crawl while the trend kept moving.

    Reads count_latest, which is already forward-filled per metric, so a class
    that has not moved for days still gets its real numbers. NULL leaves the
    stored value alone, the same COALESCE semantics as `update_counts`.
    Returns {"ts": <newest pass in the overlay or None>, "updated": n}.
    """
    ts = conn.execute(
        "SELECT MAX(ts) FROM count_latest WHERE year=? AND term=?",
        (year, term)).fetchone()[0]
    if ts is None:
        return {"ts": None, "updated": 0}
    # One UPDATE ... FROM rather than a statement per class: on a libSQL
    # connection every statement is a round trip, and the roster is ~8,600 rows.
    cur = conn.execute(
        "UPDATE classes SET applied=COALESCE(l.applied, classes.applied), "
        "cart=COALESCE(l.cart, classes.cart), "
        "enrolled=COALESCE(l.enrolled, classes.enrolled), "
        "quota=COALESCE(l.quota, classes.quota), "
        "cancel_vacancy=COALESCE(l.cancel_vacancy, classes.cancel_vacancy) "
        "FROM count_latest l "
        "WHERE l.year=classes.year AND l.term=classes.term "
        "  AND l.sbjt_cd=classes.sbjt_cd AND l.lt_no=classes.lt_no "
        "  AND classes.year=? AND classes.term=?", (year, term))
    updated = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return {"ts": ts, "updated": updated}


def snapshot_cart_counts(conn: sqlite3.Connection, year_terms) -> dict[tuple, int]:
    """Capture cart values by stable class identity before a catalog rebuild."""
    saved = {}
    for year, term in year_terms:
        rows = conn.execute(
            "SELECT year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd, cart "
            "FROM classes WHERE year=? AND term=?",
            (year, term),
        ).fetchall()
        for row in rows:
            if row["cart"] is not None:
                saved[(row["year"], row["shtm_fg"], row["deta_shtm_fg"],
                      row["sbjt_cd"], row["lt_no"], row["subh_cd"])] = row["cart"]
    return saved


def restore_cart_counts(conn: sqlite3.Connection, saved: dict[tuple, int]) -> int:
    """Restore captured cart values onto matching rows recreated by a rebuild."""
    if not saved:
        return 0
    cur = conn.executemany(
        "UPDATE classes SET cart=? WHERE year=? AND shtm_fg=? AND deta_shtm_fg=? "
        "AND sbjt_cd=? AND lt_no=? AND subh_cd=?",
        [(cart, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd)
         for (year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd), cart
         in saved.items()],
    )
    conn.commit()
    return cur.rowcount


def apply_grading(conn: sqlite3.Connection, year: str, term: str,
                  methods: dict[tuple, str], switchable: set[tuple]) -> int:
    """Overlay the 평가방식 sweep results onto one term's rows. `methods` maps
    (sbjt_cd, lt_no, subh_cd) -> 'A~F'/'S/U'/'S+/S/U'; `switchable` is the set of
    keys the srchMrksApprMthdChgPosbYn=Y sweep returned. Every row in the term
    gets grading_switch reset to 'N' first, so a class that LOST its 전환가능
    flag doesn't keep a stale 'Y'. Returns rows tagged with a grading method."""
    conn.execute("UPDATE classes SET grading_switch='N' WHERE year=? AND term=?",
                 (year, term))
    n = 0
    for grading in sorted(set(methods.values())):
        keys = [k for k, g in methods.items() if g == grading]
        conn.executemany(
            "UPDATE classes SET grading=? WHERE year=? AND term=? "
            "AND sbjt_cd=? AND lt_no=? AND subh_cd=?",
            [(grading, year, term, *k) for k in keys])
        n += len(keys)
    conn.executemany(
        "UPDATE classes SET grading_switch='Y' WHERE year=? AND term=? "
        "AND sbjt_cd=? AND lt_no=? AND subh_cd=?",
        [(year, term, *k) for k in sorted(switchable)])
    conn.commit()
    return n


# SQLite's default positional-parameter cap is 999; stay under it per statement.
_MAX_PARAMS_PER_STATEMENT = 900


def insert_chunked(conn, table: str, cols, rows, *, chunk_rows: int | None = None,
                   replace: bool = False) -> int:
    """Insert rows with multi-VALUES statements; returns statements executed.

    A remote libSQL connection pays one network round trip per execute(), so
    row-at-a-time executemany makes a large insert take minutes to hours.
    Packing many rows into each INSERT keeps the round-trip count proportional
    to row_count / chunk_rows instead. ``replace=True`` upserts on the target's
    primary key (count_latest), which is how a delta pass rewrites only the
    classes whose numbers moved.
    """
    rows = list(rows)
    if not rows:
        return 0
    if chunk_rows is None:
        chunk_rows = max(1, _MAX_PARAMS_PER_STATEMENT // len(cols))
    one = "(" + ",".join("?" * len(cols)) + ")"
    verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    prefix = f"{verb} {table} ({','.join(cols)}) VALUES "
    statements = 0
    for i in range(0, len(rows), chunk_rows):
        chunk = rows[i:i + chunk_rows]
        params = [v for row in chunk for v in row]
        conn.execute(prefix + ",".join([one] * len(chunk)), params)
        statements += 1
    return statements


def _collection_now():
    """Naive wall-clock now in COLLECTION_TIMEZONE (host-local when unset).

    Sample timestamps are stored as naive local strings; a runner whose
    process timezone differs (e.g. a UTC CI host) must not mix offsets into
    the 인원 추이 series."""
    from datetime import datetime

    name = (os.environ.get("COLLECTION_TIMEZONE") or "").strip()
    if not name:
        return datetime.now()
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(name)).replace(tzinfo=None)


def sample_counts(conn: sqlite3.Connection, year_terms, ts: str | None = None,
                  keep_days: int | None = None, collect_cart: bool = True,
                  collect_enrolled: bool = True,
                  collect_applied: bool = True) -> int:
    """Record one collection pass for the given (year, term) scope, stamped `ts`.

    Reads the current classes table, so call it right AFTER a counts refresh.
    Every pass writes one count_passes row (the trend's time axis, plus which
    metrics this pass looked at); count_samples receives a row only for the
    classes whose collected numbers actually CHANGED against count_latest,
    which is then updated for exactly those classes. Roughly 1% of a roster
    moves between two 10-minute passes, so this is ~100x less data than storing
    every class every pass — the difference between fitting a semester in a
    small database and running out of write quota mid-term. Returns the number
    of sample rows written.

    collect_cart / collect_enrolled gate those two metrics: outside their
    collection window the caller passes False and the column is stored as NULL
    (장바구니 only matters during the cart period, 수강 인원 during 수강신청).
    ``collect_applied=False`` is used by the cart-only sampler so an applied
    count from the same live response cannot be mistaken for a cart sample.
    When BOTH are False (outside every window) nothing is recorded at all, not
    even a pass, so the 인원 추이 trend stays frozen off-season.

    Every `KEYFRAME_HOURS` (default 24) a pass instead re-states EVERY class.
    A delta only means anything against the baseline its writer held, so two
    databases whose baselines diverge stay diverged in silence; the keyframe is
    what makes that self-healing.

    `keep_days` is accepted for compatibility and ignored: deleting old rows
    would erase the baseline that later samples are deltas against, and the
    history is the product here.
    """
    ts = ts or _collection_now().isoformat(timespec="seconds")
    if not collect_cart and not collect_enrolled:
        return 0   # outside every collection window: record nothing (인원 추이 frozen)
    written = 0
    for year, term in year_terms:
        keyframe = keyframe_due(conn, year, term, ts)
        rows = conn.execute(
            "SELECT sbjt_cd, lt_no, applied, cart, enrolled, quota, "
            "cancel_vacancy FROM classes WHERE year=? AND term=?",
            (year, term)).fetchall()
        latest = {
            (r["sbjt_cd"], r["lt_no"]): r
            for r in conn.execute(
                "SELECT sbjt_cd, lt_no, applied, cart, enrolled, quota, "
                "cancel_vacancy FROM count_latest WHERE year=? AND term=?",
                (year, term)).fetchall()
        }
        samples, current = [], []
        for r in rows:
            sampled = {
                "applied": r["applied"] if collect_applied else None,
                "cart": r["cart"] if collect_cart else None,
                "enrolled": r["enrolled"] if collect_enrolled else None,
                "quota": r["quota"],
                "cancel_vacancy": r["cancel_vacancy"] if collect_enrolled else None,
            }
            if all(value is None for value in sampled.values()):
                continue      # nothing observed: an all-NULL row means tombstone
            previous = latest.get((r["sbjt_cd"], r["lt_no"]))
            # Compare only what this pass actually looked at: a metric left
            # NULL because its window is closed must not read as a change, and
            # must not overwrite the value the metric last had.
            changed = keyframe or previous is None or any(
                value != previous[name]
                for name, value in sampled.items() if value is not None)
            if not changed:
                continue
            samples.append((year, term, r["sbjt_cd"], r["lt_no"], ts,
                            *(sampled[name] for name in SAMPLE_METRICS)))
            merged = {
                name: (sampled[name] if sampled[name] is not None
                       else (previous[name] if previous is not None else None))
                for name in SAMPLE_METRICS
            }
            current.append((year, term, r["sbjt_cd"], r["lt_no"], ts,
                            *(merged[name] for name in SAMPLE_METRICS)))
        # A class that left the roster stops producing samples, which under
        # delta storage is indistinguishable from "unchanged" — it would be
        # forward-filled forever. Close its series with an all-NULL tombstone
        # (a shape no real sample has: quota is always collected) and drop its
        # baseline so later passes ignore it.
        present = {(r["sbjt_cd"], r["lt_no"]) for r in rows}
        gone = [key for key in latest if key not in present]
        for sbjt_cd, lt_no in gone:
            samples.append((year, term, sbjt_cd, lt_no, ts,
                            *(None for _ in SAMPLE_METRICS)))
            conn.execute(
                "DELETE FROM count_latest WHERE year=? AND term=? AND "
                "sbjt_cd=? AND lt_no=?", (year, term, sbjt_cd, lt_no))
        insert_chunked(
            conn, "count_samples",
            ["year", "term", "sbjt_cd", "lt_no", "ts", *SAMPLE_METRICS], samples)
        insert_chunked(
            conn, "count_latest",
            ["year", "term", "sbjt_cd", "lt_no", "ts", *SAMPLE_METRICS], current,
            replace=True)
        record_pass(conn, year, term, ts, applied=collect_applied,
                    cart=collect_cart, enrolled=collect_enrolled,
                    full=keyframe)
        written += len(samples)
    conn.commit()
    return written


def record_pass(conn: sqlite3.Connection, year: str, term: str, ts: str, *,
                applied: bool, cart: bool, enrolled: bool,
                full: bool = False) -> None:
    """Register one collection pass on the trend's time axis."""
    conn.execute(
        "INSERT OR REPLACE INTO count_passes (year, term, ts, applied, cart, "
        "enrolled, full) VALUES (?,?,?,?,?,?,?)",
        (year, term, ts, int(applied), int(cart), int(enrolled), int(full)))


def keyframe_due(conn: sqlite3.Connection, year: str, term: str,
                 now: str, hours: int = KEYFRAME_HOURS) -> bool:
    """True when this pass should record every class, not just the changes.

    A delta is only meaningful against the baseline the writer held, so two
    databases whose baselines ever diverge stay diverged silently: the writer
    sees "no change" and never emits the value the reader is missing. That is
    not hypothetical — seeding a new collector database while a local worker
    was still sampling left seven classes permanently stale on 2026-09-04.
    A periodic keyframe re-states every class and heals any such split within
    `hours`, for the price of one full pass a day.
    """
    if hours <= 0:
        return False
    last = conn.execute(
        "SELECT MAX(ts) FROM count_passes WHERE year=? AND term=? AND full=1",
        (year, term)).fetchone()[0]
    if last is None:
        return True
    from datetime import datetime, timedelta
    try:
        return datetime.fromisoformat(now) - datetime.fromisoformat(last) >= timedelta(hours=hours)
    except ValueError:
        return True


def fold_pass_into_latest(conn: sqlite3.Connection, ts: str) -> int:
    """Apply one pass's samples to count_latest, forward-filling NULL metrics.

    count_latest is derived state, so it is never shipped between databases —
    the puller merges deltas and then rebuilds the current value here. Call it
    with passes in ascending ts order; an out-of-order (older) delta is ignored
    rather than allowed to undo newer state.
    """
    cur = conn.execute(
        "INSERT OR REPLACE INTO count_latest "
        "(year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota, "
        " cancel_vacancy) "
        "SELECT s.year, s.term, s.sbjt_cd, s.lt_no, s.ts, "
        "       COALESCE(s.applied, l.applied), COALESCE(s.cart, l.cart), "
        "       COALESCE(s.enrolled, l.enrolled), COALESCE(s.quota, l.quota), "
        "       COALESCE(s.cancel_vacancy, l.cancel_vacancy) "
        "FROM count_samples s LEFT JOIN count_latest l "
        "  ON l.year=s.year AND l.term=s.term AND l.sbjt_cd=s.sbjt_cd "
        " AND l.lt_no=s.lt_no "
        "WHERE s.ts=? AND (l.ts IS NULL OR s.ts >= l.ts) "
        "  AND NOT (s.applied IS NULL AND s.cart IS NULL AND s.enrolled IS NULL "
        "           AND s.quota IS NULL AND s.cancel_vacancy IS NULL)", (ts,))
    folded = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    # Tombstones (all metrics NULL) retire a class instead of updating it.
    conn.execute(
        "DELETE FROM count_latest WHERE EXISTS ("
        "  SELECT 1 FROM count_samples s WHERE s.ts=? "
        "   AND s.year=count_latest.year AND s.term=count_latest.term "
        "   AND s.sbjt_cd=count_latest.sbjt_cd AND s.lt_no=count_latest.lt_no "
        "   AND s.applied IS NULL AND s.cart IS NULL AND s.enrolled IS NULL "
        "   AND s.quota IS NULL AND s.cancel_vacancy IS NULL)", (ts,))
    return folded


def mark_closed(conn: sqlite3.Connection, year: str, term: str, ts: str | None = None) -> None:
    """Mark a term's enrollment collection closed (a forced past-semester update)."""
    from datetime import datetime
    ts = ts or datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO count_state(year, term, closed, forced_at) VALUES(?,?,1,?) "
        "ON CONFLICT(year, term) DO UPDATE SET closed=1, forced_at=excluded.forced_at",
        (year, term, ts))
    conn.commit()


def is_closed(conn: sqlite3.Connection, year: str, term: str) -> bool:
    r = conn.execute("SELECT closed FROM count_state WHERE year=? AND term=?",
                     (year, term)).fetchone()
    return bool(r and r[0])


def closed_map(conn: sqlite3.Connection) -> dict:
    """{(year, term): forced_at} for every closed term (used by the trend export)."""
    return {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT year, term, forced_at FROM count_state WHERE closed=1").fetchall()}


def list_terms(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT term, year, label FROM terms ORDER BY year DESC, term"
    ).fetchall()
    return [dict(r) for r in rows]


def list_departments(conn: sqlite3.Connection) -> list[str]:
    """Distinct non-empty department names, for the search autocomplete."""
    rows = conn.execute(
        "SELECT DISTINCT department FROM classes "
        "WHERE department IS NOT NULL AND department != '' ORDER BY department"
    ).fetchall()
    return [r["department"] for r in rows]


def list_classifications(conn: sqlite3.Connection) -> list[str]:
    """Distinct 이수구분 tokens (전필/전선/교양/학사/…), most common first."""
    rows = conn.execute(
        "SELECT je.value AS v, COUNT(*) AS n "
        "FROM classes, json_each(classes.classification) je "
        "GROUP BY je.value ORDER BY n DESC"
    ).fetchall()
    return [r["v"] for r in rows]


def list_grades(conn: sqlite3.Connection) -> list[str]:
    """Distinct 학년 values present ('0'=전학년, '1학년'..), blanks excluded."""
    rows = conn.execute(
        "SELECT DISTINCT grade FROM classes "
        "WHERE grade IS NOT NULL AND grade != '' ORDER BY grade"
    ).fetchall()
    return [r["grade"] for r in rows]


def list_gradings(conn: sqlite3.Connection) -> list[str]:
    """Distinct 평가방식 values present ('A~F'/'S/U'/'S+/S/U'), most common first."""
    rows = conn.execute(
        "SELECT grading, COUNT(*) AS n FROM classes "
        "WHERE grading IS NOT NULL AND grading != '' "
        "GROUP BY grading ORDER BY n DESC"
    ).fetchall()
    return [r["grading"] for r in rows]


def _search_where(*, term=None, year=None, name="", professor="", department="",
                  classifications=None, levels=None, grades=None,
                  gradings=None, switchable=None,
                  day_index=None, period=None):
    """Shared WHERE clause for search()/search_count(). classifications (이수구분)
    and levels (과정) are independent groups: OR within a group, AND between."""
    where = []
    params: list = []
    if year:
        where.append("c.year = ?"); params.append(year)
    if term:
        where.append("c.term = ?"); params.append(term)
    if name:
        where.append("c.name LIKE ?"); params.append(f"%{name}%")
    if professor:
        where.append("c.professor LIKE ?"); params.append(f"%{professor}%")
    if department:
        where.append("c.department LIKE ?"); params.append(f"%{department}%")
    if classifications:
        qmarks = ",".join("?" * len(classifications))
        where.append("EXISTS (SELECT 1 FROM json_each(c.classification) je "
                     f"WHERE je.value IN ({qmarks}))")
        params.extend(classifications)
    if levels:
        qmarks = ",".join("?" * len(levels))
        where.append("EXISTS (SELECT 1 FROM json_each(c.classification) je "
                     f"WHERE je.value IN ({qmarks}))")
        params.extend(levels)
    if grades:
        qmarks = ",".join("?" * len(grades))
        where.append(f"c.grade IN ({qmarks})")
        params.extend(grades)
    if gradings:
        qmarks = ",".join("?" * len(gradings))
        where.append(f"c.grading IN ({qmarks})")
        params.extend(gradings)
    if switchable:
        where.append("c.grading_switch = 'Y'")
    if day_index is not None or period is not None:
        sub = ["s.class_id = c.id"]
        if day_index is not None:
            sub.append("s.day_index = ?"); params.append(day_index)
        if period is not None:
            sub.append("s.period = ?"); params.append(period)
        where.append("EXISTS (SELECT 1 FROM class_slots s WHERE "
                     + " AND ".join(sub) + ")")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def search_count(conn: sqlite3.Connection, **filters) -> int:
    """Total classes matching the same filters as search() (ignores limit/offset)."""
    clause, params = _search_where(**filters)
    return conn.execute("SELECT COUNT(*) FROM classes c" + clause,
                        params).fetchone()[0]


def _slots_by_id(conn: sqlite3.Connection, ids: list[int],
                 batch: int = 900) -> dict[int, list]:
    """class_id -> [slot dicts], fetched in batches. SQLite caps bound variables
    (SQLITE_MAX_VARIABLE_NUMBER: 999 pre-3.32, 32766 after), so an unbounded
    `IN (?,?,...)` over a full-catalog export blows that limit — chunk to stay under."""
    by_id: dict[int, list] = {i: [] for i in ids}
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        qs = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT class_id, day_index, period, start_time, end_time "
            f"FROM class_slots WHERE class_id IN ({qs}) "
            f"ORDER BY day_index, start_time", chunk).fetchall()
        for s in rows:
            by_id[s["class_id"]].append(dict(s))
    return by_id


def search(conn: sqlite3.Connection, *, limit: int | None = 500,
           offset: int = 0, **filters) -> list[dict]:
    """Filter classes (term+year pin one semester). limit=None returns every match
    (export); offset paginates. Accepts the same filter kwargs as _search_where."""
    clause, params = _search_where(**filters)
    sql = "SELECT * FROM classes c" + clause + " ORDER BY c.name, c.lt_no"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
    elif offset:
        sql += " LIMIT -1 OFFSET ?"; params.append(offset)

    classes = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not classes:
        return []
    by_id = _slots_by_id(conn, [c["id"] for c in classes])
    for c in classes:
        c["classification"] = json.loads(c["classification"] or "[]")
        c["slots"] = by_id.get(c["id"], [])
    return classes


def iter_classes(conn: sqlite3.Connection, *, page: int = 2000, **filters):
    """Yield matching classes a page at a time (each page also chunks its slot
    fetch). Lets exports stream chunk-by-chunk instead of materialising the whole
    catalog — and never blows the SQL-variable cap, however large the result."""
    offset = 0
    while True:
        rows = search(conn, limit=page, offset=offset, **filters)
        if not rows:
            break
        yield from rows
        if len(rows) < page:
            break
        offset += page


def time_stats(conn: sqlite3.Connection) -> list[dict]:
    """Per (year, term) counts of classes with vs without a scheduled time."""
    rows = conn.execute(
        "SELECT c.year AS year, c.term AS term, "
        "COALESCE(t.label, c.year || ' ' || c.term) AS label, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN sc.class_id IS NULL THEN 0 ELSE 1 END) AS timed "
        "FROM classes c "
        "LEFT JOIN terms t ON t.term = c.term AND t.year = c.year "
        "LEFT JOIN (SELECT DISTINCT class_id FROM class_slots) sc "
        "  ON sc.class_id = c.id "
        "GROUP BY c.year, c.term ORDER BY label"
    ).fetchall()
    out = []
    for r in rows:
        total, timed = r["total"], r["timed"] or 0
        out.append({"year": r["year"], "term": r["term"], "label": r["label"],
                    "total": total, "timed": timed, "timeless": total - timed})
    return out


def lookup(conn: sqlite3.Connection,
           keys: list[tuple[str, str, str, str]]) -> list[dict]:
    """Current records (with slots) for (year, term, sbjt_cd, lt_no) keys. Used to
    reconcile a saved timetable against the latest catalog (year-qualified so the
    same course code in different years doesn't collide)."""
    if not keys:
        return []
    conds = " OR ".join(["(year=? AND term=? AND sbjt_cd=? AND lt_no=?)"] * len(keys))
    params = [x for k in keys for x in k]
    classes = [dict(r) for r in conn.execute(
        f"SELECT * FROM classes WHERE {conds}", params).fetchall()]
    if not classes:
        return []
    ids = [c["id"] for c in classes]
    qs = ",".join("?" * len(ids))
    slots = conn.execute(
        f"SELECT class_id, day_index, period, start_time, end_time "
        f"FROM class_slots WHERE class_id IN ({qs}) ORDER BY day_index, start_time", ids,
    ).fetchall()
    by_id: dict[int, list] = {i: [] for i in ids}
    for s in slots:
        by_id[s["class_id"]].append(dict(s))
    for c in classes:
        c["classification"] = json.loads(c["classification"] or "[]")
        c["slots"] = by_id.get(c["id"], [])
    return classes


def start_run(conn: sqlite3.Connection, terms: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO crawl_runs(started_at, status, terms) "
        "VALUES (datetime('now'), 'running', ?)",
        (json.dumps(terms),),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str,
               message: str = "", classes: int = 0, slots: int = 0) -> None:
    conn.execute(
        "UPDATE crawl_runs SET finished_at=datetime('now'), status=?, "
        "message=?, classes=?, slots=? WHERE id=?",
        (status, message, classes, slots, run_id),
    )
    conn.commit()


def latest_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
