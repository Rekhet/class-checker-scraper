"""Diff a catalog refresh and write a coded, grep-friendly change log.

refresh_all() wipes + rebuilds a term, so we snapshot the OLD rows before the wipe
and the NEW rows after, then diff. Each change is one line prefixed by a short code
(NEW, TADD, TCHG, PROF, QUOTA, ...) so the file stays compact and searchable
(`grep TCHG update_*.log`). A one-line summary (counts per code) goes to the main
log; the per-class detail goes to data/logs/update_<run>.log.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import db

log = logging.getLogger("class-checker")
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
_DAYS = "월화수목금토일"

# code -> meaning (legend printed at the top of every run file)
CODES = {
    "NEW": "new class added",
    "DEL": "class removed (no longer offered)",
    "TADD": "time registered (was 시간미정)",
    "TCHG": "time changed",
    "TDEL": "time removed (now 시간미정)",
    "NAME": "name changed",
    "PROF": "professor changed",
    "DEPT": "department changed",
    "COLL": "college changed",
    "GRADE": "grade changed",
    "CLS": "classification changed",
    "CRED": "credits changed",
    "QUOTA": "quota changed",
    "QRET": "재학생 quota changed",
    "APPL": "applied count changed",
    "ENRL": "enrolled count changed",
    "GRD": "평가방식 (성적부여형태) changed",
    "GRDSW": "평가방식 전환가능여부 changed",
}
# plain scalar fields compared old vs new -> (column, code)
_FIELDS = [("name", "NAME"), ("professor", "PROF"), ("department", "DEPT"),
           ("college", "COLL"), ("grade", "GRADE"), ("credits", "CRED"),
           ("quota", "QUOTA"), ("quota_returning", "QRET"),
           ("applied", "APPL"), ("enrolled", "ENRL"),
           ("grading", "GRD"), ("grading_switch", "GRDSW")]


def _fmt(v):
    if v is None or v == "":
        return "(none)"
    if isinstance(v, list):
        return ",".join(map(str, v)) or "(none)"
    return str(v)


def _slot_str(slots):
    parts = []
    for s in sorted(slots or [], key=lambda x: (x.get("day_index") if x.get("day_index") is not None
                                                 else 99, x.get("start_time") or "")):
        di, st, en = s.get("day_index"), s.get("start_time"), s.get("end_time")
        if di is None or not st:
            continue
        d = _DAYS[di] if isinstance(di, int) and 0 <= di < 7 else "?"
        parts.append(f"{d}{st}~{en or ''}")
    return "/".join(parts) or "시간미정"


def _slot_sig(slots):
    return tuple(sorted((s.get("day_index"), s.get("start_time"), s.get("end_time"))
                        for s in (slots or []) if s.get("start_time")))


def snapshot(conn, year_terms):
    """{(year, term, sbjt_cd, lt_no): class dict (with slots)} for the given terms."""
    snap = {}
    for y, t in year_terms:
        for c in db.search(conn, year=y, term=t, limit=None):
            snap[(y, t, c["sbjt_cd"], c["lt_no"])] = c
    return snap


def _changes(old, new):
    """[(code, detail)] describing how one class changed old -> new."""
    out = []
    for f, code in _FIELDS:
        ov, nv = old.get(f), new.get(f)
        if (ov if ov not in ("", None) else None) != (nv if nv not in ("", None) else None):
            out.append((code, f"{_fmt(ov)} → {_fmt(nv)}"))
    if sorted(old.get("classification") or []) != sorted(new.get("classification") or []):
        out.append(("CLS", f"{_fmt(old.get('classification'))} → {_fmt(new.get('classification'))}"))
    os_, ns_ = _slot_sig(old.get("slots")), _slot_sig(new.get("slots"))
    if os_ != ns_:
        if not os_:
            out.append(("TADD", _slot_str(new.get("slots"))))
        elif not ns_:
            out.append(("TDEL", _slot_str(old.get("slots"))))
        else:
            out.append(("TCHG", f"{_slot_str(old.get('slots'))} → {_slot_str(new.get('slots'))}"))
    return out


def diff(old, new):
    """(rows, counts). rows: [(code, key, name, detail)]; counts: per-code tally."""
    rows, code_counts, changed = [], {}, set()
    new_n = removed_n = 0
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        if o is None:
            rows.append(("NEW", k, n["name"], _slot_str(n.get("slots"))))
            code_counts["NEW"] = code_counts.get("NEW", 0) + 1
            new_n += 1
        elif n is None:
            rows.append(("DEL", k, o["name"], _slot_str(o.get("slots"))))
            code_counts["DEL"] = code_counts.get("DEL", 0) + 1
            removed_n += 1
        else:
            for code, detail in _changes(o, n):
                rows.append((code, k, n["name"], detail))
                code_counts[code] = code_counts.get(code, 0) + 1
                changed.add(k)
    return rows, {"new": new_n, "removed": removed_n,
                  "changed": len(changed), "codes": code_counts}


def write_run_log(run_id, rows, counts):
    """Write the per-run detail file; return its Path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = LOG_DIR / f"update_{run_id}_{now:%Y%m%d_%H%M%S}.log"
    head = [
        f"# class-checker update run {run_id} @ {now.isoformat(timespec='seconds')}",
        f"# summary: new={counts['new']} removed={counts['removed']} changed={counts['changed']}",
        "# codes: " + " | ".join(f"{c}={d}" for c, d in CODES.items()),
        "# columns: CODE <tab> year/term <tab> sbjt_cd(lt_no) <tab> name <tab> detail",
        "",
    ]
    body = [f"{code}\t{y}/{t}\t{sbjt}({lt})\t{name}\t{detail}"
            for code, (y, t, sbjt, lt), name, detail in rows]
    path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    return path


def summary_line(counts, path=None):
    codes = " ".join(f"{c}={n}" for c, n in sorted(counts.get("codes", {}).items()))
    s = (f"new={counts['new']} removed={counts['removed']} "
         f"changed={counts['changed']} [{codes}]")
    return s + (f" -> {path.name}" if path else "")
