"""Dump the catalog from the configured backend to static JSON for a no-backend
(GitHub Pages / any static host) deploy.

Source = db.connect() (so it honours DB_BACKEND + TURSO_* in the env — point it at
the remote read-only DB with prod.env). Output = web/data/classes/<year>_<term>.json
(one per term) + web/data/classes/index.json (term list + filter vocabularies); enrollment
trend goes to web/data/trend/<year>_<term>.json. The frontend lazy-loads per term.

    # local backend:
    python scraper/export_json.py
    # remote (read-only) Turso:
    set -a; . ./prod.env; set +a; python scraper/export_json.py

Writes under web/data/ by default (override the base with JSON_OUT=...): the class
catalog -> web/data/classes/, enrollment trend -> web/data/trend/. Use
``--trend-only --years 2026 --terms fall`` for the small, frequent publisher;
it leaves the catalog and explore index untouched. These files are safe to commit
and serve publicly — the catalog is public data, no secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import db
import process_lock

OUT = Path(os.environ.get("JSON_OUT", "web/data"))
# how many recent samples (refresh runs) to keep per term in the trend export
TREND_WINDOW = int(os.environ.get("TREND_WINDOW", "240"))

TERM_CODES = {
    "spring": "U000200001U000300001",
    "fall": "U000200002U000300001",
    "summer": "U000200001U000300002",
    "winter": "U000200002U000300002",
}


# Trend metric -> (payload key, count_passes flag column). quota has no flag:
# every pass records it, so it is simply forward-filled.
_TREND_METRICS = (("applied", "a", "applied"), ("cart", "c", "cart"),
                  ("enrolled", "e", "enrolled"), ("quota", "q", None))


def _load_axis(conn, t) -> list[tuple]:
    """The term's collection passes: (ts, applied?, cart?, enrolled?), oldest first.

    count_passes is the axis because samples are deltas — a pass where nothing
    moved writes no sample at all, yet it is still a point in time the chart
    must show. A catalog written before delta storage, whose passes were never
    recorded, falls back to deriving the axis from the dense samples.
    """
    rows = conn.execute(
        "SELECT ts, applied, cart, enrolled FROM count_passes "
        "WHERE year=? AND term=? ORDER BY ts",
        (t["year"], t["term"])).fetchall()
    if rows:
        return [(r[0], bool(r[1]), bool(r[2]), bool(r[3])) for r in rows]
    derived = conn.execute(
        "SELECT ts, MAX(applied IS NOT NULL), MAX(cart IS NOT NULL), "
        "       MAX(enrolled IS NOT NULL) FROM count_samples "
        "WHERE year=? AND term=? GROUP BY ts ORDER BY ts",
        (t["year"], t["term"])).fetchall()
    return [(r[0], bool(r[1]), bool(r[2]), bool(r[3])) for r in derived]


def _walk_samples(conn, t, axis: list[tuple], wanted: dict) -> dict:
    """Replay the term's samples over `axis`, materialising the wanted windows.

    Samples are deltas: a class's row appears only when one of its collected
    numbers changed, so the value at any pass is the newest sample at or before
    it (forward fill). A metric the pass did not collect stays None instead —
    otherwise a closed 장바구니 window would draw a flat line through the whole
    수강신청 period. `wanted` maps a window name to a (start, end) half-open
    slice of `axis`; each becomes its own payload with its own local ts axis.
    """
    slots = {name: {ts: i for i, (ts, *_flags) in enumerate(axis[lo:hi])}
             for name, (lo, hi) in wanted.items()}
    lengths = {name: hi - lo for name, (lo, hi) in wanted.items()}
    series = {name: {} for name in wanted}
    state: dict[str, dict] = {}

    rows = conn.execute(
        "SELECT sbjt_cd, lt_no, ts, applied, cart, enrolled, quota "
        "FROM count_samples WHERE year=? AND term=? ORDER BY ts",
        (t["year"], t["term"])).fetchall()
    pending = 0
    for ts, applied_on, cart_on, enrolled_on in axis:
        while pending < len(rows) and rows[pending][2] <= ts:
            r = rows[pending]
            pending += 1
            cls = f"{r[0]}({r[1]})"
            if all(value is None for value in r[3:7]):
                state.pop(cls, None)   # tombstone: the class left the roster
                continue
            entry = state.setdefault(cls,
                                     {"a": None, "c": None, "e": None, "q": None})
            for value, key in zip(r[3:7], ("a", "c", "e", "q")):
                if value is not None:
                    entry[key] = value
        collected = {"a": applied_on, "c": cart_on, "e": enrolled_on, "q": True}
        for name, index in slots.items():
            i = index.get(ts)
            if i is None:
                continue
            n = lengths[name]
            target = series[name]
            for cls, entry in state.items():
                arrays = target.get(cls)
                if arrays is None:
                    arrays = target[cls] = {"a": [None] * n, "c": [None] * n,
                                            "e": [None] * n, "q": [None] * n}
                for key in ("a", "c", "e", "q"):
                    if collected[key]:
                        arrays[key][i] = entry[key]
    return series


def _payload(axis: list[tuple], window: tuple[int, int], series: dict) -> dict:
    """Wrap one window's series with its ts axis.

    `updated` is the last sample time (= the real data refresh moment); using
    export-time now() would change the file on every export even outside the
    collection windows."""
    ts_keep = [ts for ts, *_flags in axis[window[0]:window[1]]]
    return {"updated": ts_keep[-1], "ts": ts_keep, "series": series}


def export_trend_archives(conn, t, out_dir: Path,
                          window: int = TREND_WINDOW) -> int:
    """Freeze the trend history that scrolled out of the live window.

    The live trend file publishes only the newest `window` timestamps, so at a
    10-minute cadence it covers barely two days; everything older is split
    into fixed `window`-sized chunks (trend_<year>_<term>_w000.json, …) that
    the web UI can page through. A chunk is written once and then never
    rewritten — its file existing means it is frozen — so the hourly publish
    does not churn git history. The trailing partial chunk stays live-only.
    Returns the number of complete chunks (written or already present)."""
    axis = _load_axis(conn, t)
    complete = len(axis) // window
    if not complete:
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for w in range(complete):
        path = out_dir / f"trend_{t['year']}_{t['term']}_w{w:03d}.json"
        if not path.exists():
            paths[f"w{w:03d}"] = (path, (w * window, (w + 1) * window))
    if paths:
        series = _walk_samples(conn, t, axis,
                               {name: bounds for name, (_p, bounds) in paths.items()})
        for name, (path, bounds) in paths.items():
            _write(path, _payload(axis, bounds, series[name]))
    return complete


def export_trend(conn, t) -> dict | None:
    """Compact enrollment time-series for one term: a shared `ts` axis plus per-class
    aligned arrays (a=applied, c=cart, e=enrolled, q=quota; null for a metric the
    pass did not collect). Returns None when the term has no passes yet."""
    axis = _load_axis(conn, t)
    if not axis:
        return None
    bounds = (max(0, len(axis) - TREND_WINDOW), len(axis))
    series = _walk_samples(conn, t, axis, {"live": bounds})["live"]
    out = _payload(axis, bounds, series)
    st = conn.execute("SELECT closed, forced_at FROM count_state WHERE year=? AND term=?",
                      (t["year"], t["term"])).fetchone()
    if st and st[0]:                       # 마감된 학기 (forced past-term capture)
        out["closed"] = True
        out["closedAt"] = st[1]
    return out


def _write(path: Path, obj) -> int:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
            tmp.flush()
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return len(text.encode("utf-8"))


def select_term_codes(spec: str | None) -> list[str]:
    """Expand semester aliases/codes into a de-duplicated term-code list."""
    if not spec:
        return []
    out: list[str] = []
    for raw in spec.replace(" ", ",").split(","):
        value = raw.strip()
        if not value:
            continue
        if value.lower() in ("all", "전체"):
            values = list(TERM_CODES.values())
        else:
            values = [TERM_CODES.get(value.lower(), value)]
        for code in values:
            if code not in out:
                out.append(code)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Co-professor join characters (Korean typography): slash / middot / CJK comma /
# semicolons. The ASCII comma is deliberately EXCLUDED — the source `주담당교수`
# column is single-professor, and its only commas are name-internal in romanized
# "Last, First" English names (e.g. "Kim, Joon Kium"); splitting on comma would
# shatter every English-named professor into two phantom identities. See the Task 1
# Investigation Findings in docs/superpowers/plans/2026-07-01-professor-identity.md.
_PROF_DELIMS = re.compile(r"\s*[/·、；;]\s*")


def _split_profs(s: str) -> list[str]:
    """Split a professor field into individual names, trimmed; drop empties. A single
    name (the norm for this data) returns a one-element list. Order preserved,
    duplicates removed. Never splits on the ASCII comma (see `_PROF_DELIMS`)."""
    parts = [p.strip() for p in _PROF_DELIMS.split(s or "") if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p); out.append(p)
    return out


# Deterministic professor identity: uuid5 over "<normalized-name>|<department>".
# Content-addressed so two exports produce byte-identical ids (a random scheme would
# break every #prof/<id> link on each rebuild).
PROF_NS = uuid.uuid5(uuid.NAMESPACE_URL, "snu-class-checker/prof-identity")


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _prof_key(name: str, dept: str) -> str:
    return f"{_norm_name(name)}|{(dept or '').strip()}"


def _synth_id(name: str, dept: str) -> str:
    return str(uuid.uuid5(PROF_NS, _prof_key(name, dept)))


def _email_id(email: str) -> str:
    """Real, unique professor identity from the representative professor's email
    (cc103ajax LISTTAB03.profEmail). Case-folded so one person always hashes the
    same. The raw email is NEVER emitted — only this uuid5 hash leaves the build,
    so publishing the static JSON leaks no personal address. Prefixed to never
    collide with a `name|dept` synthetic key."""
    return str(uuid.uuid5(PROF_NS, "email:" + email.strip().lower()))


def _load_two_bucket(path: Path, buckets: tuple[str, ...]) -> dict:
    """Load a curated working-tree JSON override map with fixed list buckets.

    `buckets` names the top-level keys (e.g. ("merges", "splits") for
    prof_identity, ("links", "suppress") for code_links). Always returns a dict
    with every named bucket present as a list. Missing, empty, corrupt, or
    wrong-shaped files degrade to all-empty no-op rules rather than raising —
    a hand-edited curated file must never be able to crash a rebuild. Any
    top-level keys not in `buckets` are ignored."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {b: (raw[b] if isinstance(raw.get(b), list) else []) for b in buckets}


def _term_key(term_pair) -> tuple:
    # [year, "U0002...U0003..."] — lexicographic term code is chronological within a year
    return (term_pair[0], term_pair[1])


def _build_resolver(identity: dict, synth_to_email: dict | None = None):
    """Return resolve(name, dept, term_pair, rep_email) -> canonical id.

    Base identity is the representative professor's EMAIL (a real, unique ID) when
    the offering has one — this merges dept-renames + cross-dept teaching (same
    person, one email) and splits homonym reps (same name|dept, two emails). With
    no email it falls back to uuid5(name|dept), and curated splits apply on that
    synthetic path only (email already disambiguates homonyms).

    Curated merges (prof_identity.json) reference synthetic ids. `synth_to_email`
    maps each synthetic id to its email id when that name|dept has exactly one, so
    a merge's endpoints are translated into the email era — otherwise a curated
    merge would silently regress once the base flips to email."""
    s2e = synth_to_email or {}

    def translate(i):
        eids = s2e.get(i)
        return next(iter(eids)) if eids and len(eids) == 1 else i

    # merges: member id -> canonical id (both synthetic and email-translated forms)
    merge_map = {}
    for m in identity["merges"]:
        canon = translate(m["canonical"])
        for mem in m.get("members", []):
            merge_map[mem] = canon
            merge_map[translate(mem)] = canon
    # splits: (norm_name, dept) -> [(cutoff_key, before_id, after_id)]
    split_map = {}
    for s in identity["splits"]:
        key = (_norm_name(s["name"]), (s.get("dept") or "").strip())
        split_map.setdefault(key, []).append(
            (_term_key(s["cutoffTerm"]), s["before"], s["after"]))

    def resolve(name, dept, term_pair, rep_email=None):
        if rep_email:
            base = _email_id(rep_email)                # real ID: merges renames, splits homonyms
        else:
            base = _synth_id(name, dept)
            rules = split_map.get((_norm_name(name), (dept or "").strip()))
            if rules:
                tk = _term_key(term_pair)
                for cutoff, before, after in rules:
                    base = before if tk <= cutoff else after   # <=cutoff = older era
                    break
        return merge_map.get(base, base)   # remap merged members to canonical
    return resolve


def _load_rep_emails(conn) -> dict:
    """{(year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd): rep_email} from the
    backfilled class_rep_email cache. School email (profEmail) preferred, personal
    (profEmail2) as fallback. Returns {} if the table is absent (e.g. exporting from
    a DB that was never backfilled) so identity cleanly degrades to uuid5(name|dept)."""
    try:
        rows = conn.execute(
            "SELECT year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd, "
            "rep_email, rep_email2 FROM class_rep_email WHERE ok=1").fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        email = (r["rep_email"] or r["rep_email2"] or "").strip()
        if email:
            out[(r["year"], r["shtm_fg"], r["deta_shtm_fg"],
                 r["sbjt_cd"], r["lt_no"], r["subh_cd"])] = email
    return out


def _build_synth_to_email(conn, rep_email_by_key: dict) -> dict:
    """synthetic-id -> {email_id, ...} for every representative whose name|dept has a
    backfilled email. Lets curated merges (authored against synthetic ids) survive
    the switch to email-based identity. A synthetic id mapping to 2+ email ids is a
    genuine homonym split — left multi-valued so translate() declines to collapse it.
    Lightweight scan (no slot join)."""
    s2e: dict[str, set] = {}
    if not rep_email_by_key:
        return s2e
    rows = conn.execute(
        "SELECT year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd, "
        "professor, department FROM classes").fetchall()
    for r in rows:
        email = rep_email_by_key.get(
            (r["year"], r["shtm_fg"], r["deta_shtm_fg"],
             r["sbjt_cd"], r["lt_no"], r["subh_cd"]))
        if not email:
            continue
        names = _split_profs(r["professor"] or "")
        if not names:
            continue
        sid = _synth_id(names[0], r["department"] or "")   # names[0] = representative
        s2e.setdefault(sid, set()).add(_email_id(email))
    return s2e


# ---- Code-change linking (Spec 3) --------------------------------------------
# Manufacture predecessor<->successor links between sbjt_cd codes. There is NO
# source pointer for a renumbering, so links are inferred (shared normalized name +
# department + adjacent term ranges) and/or curated (scraper/code_links.json).
# Links are additive + display-only: this NEVER hides/merges/moves an offering.
# Reuses Spec 2's module-level _norm_name (do not duplicate it).

CODE_LINK_ADJ_WINDOW = 4    # max termIdx gap between prev's newest and next's oldest
CODE_LINK_OVERLAP_MAX = 1   # allow <=this many shared terms (a transition term); more => concurrent, not a rename


def _code_span(code_obj):
    """(newest_termIdx, oldest_termIdx) for a code. termIdx 0 = newest, so
    newest = min, oldest = max."""
    tis = [o[0] for o in code_obj["o"]]
    return min(tis), max(tis)


def _infer_code_links(code_objs, names_tbl):
    """Infer prev->next candidates from name+dept adjacency. Returns
    [(prev_c, next_c, conf)] with conf in {'high','low'}. Conservative:
    name+dept alone never exceeds 'low' (different courses share names)."""
    groups: dict[tuple, list] = {}
    for c in code_objs:
        cur_name = names_tbl[c["names"][0]] if c["names"] else ""
        norm = _norm_name(cur_name)
        if not norm:
            continue
        primary_dept = c["o"][0][3]                 # newest offering's deptId (home dept)
        groups.setdefault((norm, primary_dept), []).append(c)

    out = []
    for (_norm_key, _dept_id), members in groups.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                a_new, _a_old = _code_span(a)
                b_new, _b_old = _code_span(b)
                if a_new == b_new:
                    continue                        # both still current => not a succession
                prev, nxt = (a, b) if a_new > b_new else (b, a)   # larger newest-termIdx = older = prev
                p_new, _p_old = _code_span(prev)
                _n_new, n_old = _code_span(nxt)
                gap = p_new - n_old                 # +1 == consecutive dataset terms; <=0 == overlap
                if gap > CODE_LINK_ADJ_WINDOW:
                    continue                        # too far apart => likely unrelated name reuse
                overlap = len({o[0] for o in prev["o"]} & {o[0] for o in nxt["o"]})
                if overlap > CODE_LINK_OVERLAP_MAX:
                    continue                        # heavily concurrent => distinct courses
                credits_match = prev["o"][0][4] == nxt["o"][0][4]
                conf = "high" if (credits_match and gap == 1 and overlap == 0) else "low"
                out.append((prev["c"], nxt["c"], conf))
    return out


def _apply_code_links(code_objs, names_tbl, path: Path) -> None:
    """Merge inferred candidates with the curated map and write prev/next arrays
    onto each code object in place: inferred - suppressed + confirmed. Empty
    arrays are omitted. Deterministic (stable input order + sorted output)."""
    curated = _load_two_bucket(path, ("links", "suppress"))
    suppressed = set()
    for s in curated["suppress"]:
        a, b = s.get("a"), s.get("b")
        if a and b:
            suppressed.add(frozenset((a, b)))

    pairs: dict[tuple, str] = {}                    # (prev_c, next_c) -> conf
    for prev_c, next_c, conf in _infer_code_links(code_objs, names_tbl):
        if frozenset((prev_c, next_c)) in suppressed:
            continue
        key = (prev_c, next_c)
        if key not in pairs or (pairs[key] == "low" and conf == "high"):
            pairs[key] = conf
    for l in curated["links"]:                      # confirmed pairs override/add (never 추정)
        prev_c, next_c = l.get("prev"), l.get("next")
        if prev_c and next_c:
            pairs[(prev_c, next_c)] = "confirmed"

    by_code = {c["c"]: c for c in code_objs}
    for c in code_objs:
        c["prev"] = []
        c["next"] = []
    for (prev_c, next_c), conf in pairs.items():
        if prev_c not in by_code or next_c not in by_code:
            continue                                # only link codes that exist
        by_code[next_c]["prev"].append({"c": prev_c, "conf": conf})   # older code shown on newer's page
        by_code[prev_c]["next"].append({"c": next_c, "conf": conf})   # newer code shown on older's page
    for c in code_objs:
        c["prev"].sort(key=lambda x: x["c"])
        c["next"].sort(key=lambda x: x["c"])
        if not c["prev"]:
            del c["prev"]
        if not c["next"]:
            del c["next"]


def _export_explore(conn, terms, classes_dir_writer) -> None:
    """Build the cross-term Explore index: interned string tables + integer-coded
    offering rows grouped by sbjt_cd. Newest-first term order (matches list_terms)."""
    name_intern: dict[str, int] = {}
    dept_intern: dict[str, int] = {}

    def intern(tbl: dict[str, int], s: str) -> int:
        s = (s or "").strip()
        i = tbl.get(s)
        if i is None:
            i = tbl[s] = len(tbl)
        return i

    def keys_in_order(tbl: dict[str, int]) -> list[str]:
        out = [""] * len(tbl)
        for s, i in tbl.items():
            out[i] = s
        return out

    # Professor identity table (replaces the raw prof-string intern). Each distinct
    # canonical id (post merge/split) gets one profId = its index into prof_rows.
    identity = _load_two_bucket(Path("scraper/prof_identity.json"), ("merges", "splits"))
    rep_email_by_key = _load_rep_emails(conn)               # {} if never backfilled
    synth_to_email = _build_synth_to_email(conn, rep_email_by_key)
    resolve_prof = _build_resolver(identity, synth_to_email)
    prof_by_id: dict[str, int] = {}     # canonical id -> profId (index into prof table)
    prof_rows: list[dict] = []          # [{id, name, depts:set}] parallel to prof_by_id

    def prof_index(pid, name, dept_id):
        i = prof_by_id.get(pid)
        if i is None:
            i = prof_by_id[pid] = len(prof_rows)
            prof_rows.append({"id": pid, "name": _norm_name(name), "depts": set()})
        prof_rows[i]["depts"].add(dept_id)
        return i

    term_list: list[list] = []          # [[year, term-code], ...] newest-first
    codes: dict[str, dict] = {}         # sbjt_cd -> {"seen": {(termIdx, ltNo): rowIdx}, "o": [rows]}

    for term_idx, t in enumerate(terms):
        term_list.append([t["year"], t["term"]])
        rows = db.search(conn, year=t["year"], term=t["term"], limit=None)
        for r in rows:
            cd = r["sbjt_cd"]
            bucket = codes.get(cd)
            if bucket is None:
                bucket = codes[cd] = {"seen": {}, "o": []}
            dept_id = intern(dept_intern, r.get("department", ""))
            names = _split_profs(r.get("professor", ""))
            if not names:
                names = [""]                                  # keep the row; "정보 없음" client-side
            term_pair = term_list[term_idx]
            okey = (r["year"], r["shtm_fg"], r["deta_shtm_fg"],
                    r["sbjt_cd"], r["lt_no"], r["subh_cd"])
            rep_email = rep_email_by_key.get(okey)           # rep-only; co-profs have none
            pids = []
            for ni, nm in enumerate(names):
                cid = resolve_prof(nm, r.get("department", ""), term_pair,
                                   rep_email if ni == 0 else None)
                idx = prof_index(cid, nm, dept_id)
                if idx not in pids:
                    pids.append(idx)
            prof_field = pids[0] if len(pids) == 1 else pids   # int-or-array (Spec 1 schema)
            row = [term_idx,
                   intern(name_intern, r.get("name", "")),
                   prof_field,
                   dept_id,
                   r.get("credits") or 0,
                   r.get("lt_no", "")]
            key = (term_idx, row[5])                 # dedupe same (term, section) re-scrapes
            j = bucket["seen"].get(key)
            if j is None:
                bucket["seen"][key] = len(bucket["o"])
                bucket["o"].append(row)
            else:
                bucket["o"][j] = row                 # keep latest

    code_objs = []
    for cd, bucket in codes.items():
        offs = bucket["o"]
        offs.sort(key=lambda o: (o[0], o[5]))        # ascending termIdx (= newest-first), then section
        seen_names, ordered_names = set(), []
        for o in offs:
            if o[1] not in seen_names:
                seen_names.add(o[1]); ordered_names.append(o[1])
        code_objs.append({"c": cd, "names": ordered_names, "o": offs})

    _apply_code_links(code_objs, keys_in_order(name_intern), Path("scraper/code_links.json"))

    explore = {
        "version": 1,
        "generated": _now_iso(),
        "strings": {"names": keys_in_order(name_intern),
                    "profs": [{"id": p["id"], "name": p["name"], "depts": sorted(p["depts"])}
                              for p in prof_rows],
                    "depts": keys_in_order(dept_intern)},
        "terms": term_list,
        "codes": code_objs,
    }
    size = classes_dir_writer(explore)
    print(f"  explore-index.json: {len(code_objs)} codes, "
          f"{sum(len(c['o']) for c in code_objs)} offerings ({size // 1024} KB)")


def export_trend_only(conn, *, years: list[str] | None = None,
                      terms: list[str] | None = None) -> int:
    """Publish only trend JSON for the requested scope.

    Counts-only runs do not need to rewrite the 60+ MB class catalog or the
    cross-semester explore index. The existing class index is updated only when
    a term gains its first trend file.
    """
    classes_dir = OUT / "classes"
    trend_dir = OUT / "trend"
    index_path = classes_dir / "index.json"
    if not index_path.exists():
        raise RuntimeError(
            f"missing {index_path}; run a full JSON export before trend-only publishing"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    wanted_years = set(years or [])
    wanted_terms = set(terms or [])
    selected = [
        t for t in db.list_terms(conn)
        if (not wanted_years or t["year"] in wanted_years)
        and (not wanted_terms or t["term"] in wanted_terms)
    ]
    entries = {(str(t["year"]), str(t["term"])): t for t in index.get("terms", [])}
    written = 0
    for t in selected:
        trend = export_trend(conn, t)
        if trend is None:
            continue
        tfn = f"trend_{t['year']}_{t['term']}.json"
        _write(trend_dir / tfn, trend)
        entry = entries.get((str(t["year"]), str(t["term"])))
        if entry is None:
            raise RuntimeError(
                f"class index has no entry for {t['year']}/{t['term']}; run a full JSON export"
            )
        entry["trend"] = tfn
        entry["trendArchives"] = export_trend_archives(conn, t, trend_dir)
        written += 1
        print(f"  {tfn}: {len(trend['series'])} classes × {len(trend['ts'])} samples"
              f" (+{entry['trendArchives']} archive windows)")
    _write(index_path, index)
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Export class-checker data to static JSON")
    ap.add_argument("--trend-only", action="store_true",
                    help="write only trend JSON and the class index pointers")
    ap.add_argument("--years", default="",
                    help="comma-separated year scope for --trend-only")
    ap.add_argument("--terms", default="",
                    help="comma-separated semester aliases or cmmnCd values for --trend-only")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    conn = db.connect()
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        if args.trend_only:
            years = [x for x in args.years.replace(" ", ",").split(",") if x]
            written = export_trend_only(
                conn, years=years, terms=select_term_codes(args.terms)
            )
            print(f"wrote {written} trend file(s) under {OUT / 'trend'}")
            return

        classes_dir = OUT / "classes"; trend_dir = OUT / "trend"   # class catalog + trend live in subdirs
        classes_dir.mkdir(exist_ok=True); trend_dir.mkdir(exist_ok=True)

        terms = db.list_terms(conn)            # [{term, year, label}], newest first
        index_terms = []
        total = 0
        for t in terms:
            rows = db.search(conn, year=t["year"], term=t["term"], limit=None)
            fn = f"{t['year']}_{t['term']}.json"
            size = _write(classes_dir / fn, rows)
            entry = {"year": t["year"], "term": t["term"],
                     "label": t["label"], "count": len(rows), "file": fn}   # client prefixes data/classes/
            trend = export_trend(conn, t)   # None until the counts timer has run
            if trend:
                tfn = f"trend_{t['year']}_{t['term']}.json"
                tsize = _write(trend_dir / tfn, trend)
                entry["trend"] = tfn   # client prefixes data/trend/
                entry["trendArchives"] = export_trend_archives(conn, t, trend_dir)
                print(f"  {tfn}: {len(trend['series'])} classes × {len(trend['ts'])} samples ({tsize // 1024} KB,"
                      f" +{entry['trendArchives']} archive windows)")
            index_terms.append(entry)
            total += len(rows)
            print(f"  {fn}: {len(rows)} classes ({size // 1024} KB)")

        meta = {
            "terms": index_terms,
            "departments": db.list_departments(conn),
            "classifications": db.list_classifications(conn),
            "grades": db.list_grades(conn),
            "gradings": db.list_gradings(conn),
        }
        _write(classes_dir / "index.json", meta)
        print(f"wrote {len(index_terms)} term files + index.json "
              f"({total} classes) to {classes_dir}")
        _export_explore(conn, terms, lambda obj: _write(OUT / "explore-index.json", obj))
    finally:
        conn.close()


if __name__ == "__main__":
    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    try:
        with lock:
            main()
    except process_lock.LockTimeout as exc:
        print(f"process lock busy: {exc}", file=sys.stderr)
        raise SystemExit(process_lock.LOCK_TIMEOUT_EXIT)
