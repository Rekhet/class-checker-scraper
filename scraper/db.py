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
    quota    INTEGER
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
    conn.commit()


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


def update_counts(conn: sqlite3.Connection, year: str, shtm_fg: str,
                  deta_shtm_fg: str, sbjt_cd: str, lt_no: str, subh_cd: str, *,
                  applied: int | None = None, quota: int | None = None,
                  enrolled: int | None = None, cart: int | None = None) -> bool:
    """Overlay live enrollment numbers from the search endpoint onto an existing
    Excel-built row. Only the volatile columns move; a None leaves the old value
    (COALESCE), a real value (incl. 0) overrides the stale Excel count."""
    cur = conn.execute(
        "UPDATE classes SET applied=COALESCE(?, applied), "
        "quota=COALESCE(?, quota), enrolled=COALESCE(?, enrolled), "
        "cart=COALESCE(?, cart) "
        "WHERE year=? AND shtm_fg=? AND deta_shtm_fg=? AND sbjt_cd=? "
        "AND lt_no=? AND subh_cd=?",
        (applied, quota, enrolled, cart, year, shtm_fg, deta_shtm_fg,
         sbjt_cd, lt_no, subh_cd),
    )
    return cur.rowcount > 0


def snapshot_cart_counts(conn: sqlite3.Connection, year_terms) -> dict[tuple, int | None]:
    """Capture cart values by stable class identity before a catalog rebuild."""
    saved = {}
    for year, term in year_terms:
        rows = conn.execute(
            "SELECT year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd, cart "
            "FROM classes WHERE year=? AND term=?",
            (year, term),
        ).fetchall()
        for row in rows:
            saved[(row["year"], row["shtm_fg"], row["deta_shtm_fg"],
                  row["sbjt_cd"], row["lt_no"], row["subh_cd"])] = row["cart"]
    return saved


def restore_cart_counts(conn: sqlite3.Connection, saved: dict[tuple, int | None]) -> int:
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


def sample_counts(conn: sqlite3.Connection, year_terms, ts: str | None = None,
                  keep_days: int = 120, collect_cart: bool = True,
                  collect_enrolled: bool = True,
                  collect_applied: bool = True) -> int:
    """Append one enrollment sample per class (for the given (year, term) scope)
    to count_samples, stamped `ts`. Reads the current classes table, so call it
    right AFTER a counts refresh. Returns rows inserted. Old rows beyond
    keep_days are pruned to bound the table.

    collect_cart / collect_enrolled gate those two metrics: outside their
    collection window the caller passes False and the column is stored as NULL
    (장바구니 only matters during the cart period, 수강 인원 during 수강신청).
    ``collect_applied=False`` is used by the cart-only sampler so an applied
    count from the same live response cannot be mistaken for a cart sample.
    When BOTH are False (outside every window) nothing is inserted at all, so the
    인원 추이 trend stays frozen off-season instead of accruing flat samples."""
    from datetime import datetime, timedelta
    ts = ts or datetime.now().isoformat(timespec="seconds")
    if not collect_cart and not collect_enrolled:
        return 0   # outside every collection window: record nothing (인원 추이 frozen)
    n = 0
    for year, term in year_terms:
        rows = conn.execute(
            "SELECT year, term, sbjt_cd, lt_no, applied, cart, enrolled, quota "
            "FROM classes WHERE year=? AND term=?", (year, term)).fetchall()
        conn.executemany(
            "INSERT INTO count_samples"
            "(year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["year"], r["term"], r["sbjt_cd"], r["lt_no"], ts,
              r["applied"] if collect_applied else None,
              r["cart"] if collect_cart else None,
              r["enrolled"] if collect_enrolled else None,
              r["quota"]) for r in rows])
        n += len(rows)
    if keep_days:
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM count_samples WHERE ts < ?", (cutoff,))
    conn.commit()
    return n


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
