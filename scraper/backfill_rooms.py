#!/usr/bin/env python3
"""Backfill per-meeting rooms (class_slots.room) and classes.room for terms
already in the catalog, from each term's Excel export.

The room column was added after most terms were crawled, so every term but the
newest has none. A full catalog rebuild (crawl.py) would fix that but also
replace each old term's roster with whatever sugang serves today. This touches
rooms only:

* classes.room is set when the Excel lists a room for the class;
* a class's slots are rewritten with rooms only when the Excel's meeting times
  equal the catalog's exactly. A timing difference is counted and left alone,
  and an Excel row with no catalog class is counted, never inserted.

Idempotent: re-running a term rewrites the same rooms.

    DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db \\
        uv run python -m scraper.backfill_rooms [--years 2025,2026] [--terms CODE] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from contextlib import nullcontext

try:
    from . import db, excel, process_lock
except ImportError:  # pragma: no cover - direct script execution
    import db  # type: ignore[no-redef]
    import excel  # type: ignore[no-redef]
    import process_lock  # type: ignore[no-redef]


def apply_term(conn, recs: list[dict], *, year: str, term: str) -> dict:
    """Write one term's parsed Excel rooms onto its existing catalog rows."""
    ids: dict[tuple, list[int]] = {}
    for cid, code, lt_no in conn.execute(
            "SELECT id, sbjt_cd, lt_no FROM classes WHERE year=? AND term=?",
            (year, term)).fetchall():
        ids.setdefault((code, lt_no), []).append(cid)
    stats = {"excel_rows": len(recs), "matched": 0, "unmatched": 0, "ambiguous": 0,
             "rooms_set": 0, "slots_roomed": 0, "timing_differs": 0}
    differs: list[str] = []
    for rec in recs:
        cids = ids.get((rec["sbjt_cd"], rec["lt_no"]), [])
        if len(cids) != 1:
            stats["unmatched" if not cids else "ambiguous"] += 1
            continue
        cid = cids[0]
        stats["matched"] += 1
        if rec["room"]:
            conn.execute("UPDATE classes SET room=? WHERE id=?", (rec["room"], cid))
            stats["rooms_set"] += 1
        if not rec["slots"] or not any(s["room"] for s in rec["slots"]):
            continue
        have = {tuple(r) for r in conn.execute(
            "SELECT day_index, start_time, end_time FROM class_slots WHERE class_id=?",
            (cid,)).fetchall()}
        want = {(s["day_index"], s["start_time"], s["end_time"]) for s in rec["slots"]}
        if have != want:
            stats["timing_differs"] += 1
            differs.append(f"{rec['sbjt_cd']}({rec['lt_no']})")
            continue
        conn.execute("DELETE FROM class_slots WHERE class_id=?", (cid,))
        for s in rec["slots"]:
            db.add_slot(conn, cid, s)
        stats["slots_roomed"] += 1
    stats["differs_sample"] = differs[:10]
    return stats


def _plan(conn, years: list[str], terms: list[str]) -> list[tuple]:
    rows = conn.execute(
        "SELECT year, term, label FROM terms ORDER BY year, term").fetchall()
    return [tuple(r) for r in rows
            if (not years or r[0] in years) and (not terms or r[1] in terms)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", default="", help="comma list; blank = every year in terms")
    ap.add_argument("--terms", default="", help="comma cmmnCd subset; blank = all")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args(argv)
    years = [y for y in args.years.split(",") if y]
    terms = [t for t in args.terms.split(",") if t]

    try:
        from .crawl import SnuClient
    except ImportError:  # pragma: no cover
        from crawl import SnuClient  # type: ignore[no-redef]

    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    failed = []
    with lock:
        conn = db.connect()
        try:
            db.init_schema(conn)
            plan = _plan(conn, years, terms)
            client = SnuClient()
            for year, term, label in plan:
                try:
                    content = excel.fetch_excel(client, year, term)
                    recs = excel.parse_excel(content, year, term) if content else []
                    stats = apply_term(conn, recs, year=year, term=term)
                except Exception as e:  # noqa: BLE001 - one bad term must not stop the rest
                    conn.rollback()
                    failed.append(label)
                    print(f"{label}: FAILED {e}", flush=True)
                    continue
                if args.dry_run:
                    conn.rollback()
                else:
                    conn.commit()
                print(f"{label}: {stats}", flush=True)
                time.sleep(1)
        finally:
            conn.close()
    if failed:
        print(f"failed terms: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
