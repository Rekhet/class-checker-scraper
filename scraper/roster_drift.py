#!/usr/bin/env python3
"""Catalog drift: classes the live search has that the catalog lacks, and
catalog classes the live search no longer has.

Only a local crawl can refresh the catalog, and after the registration and
change periods it rarely moves — so instead of crawling on a timer, the
GitHub collector compares every pass's live roster with the seeded catalog
and records a drift (cloud table ``roster_drift``) when they differ. The local
update pulls those rows and crawls only when a drift is

- real: the same difference seen on at least MIN_PASSES consecutive passes
  and still present in the newest pass,
- new: not already handled by an earlier crawl, and
- late: past every 장바구니/수강신청/변경 window (CART_WINDOWS, ENROLL_WINDOWS)
  of the term — during those periods the roster churns by design.

Identity is (sbjt_cd, lt_no), the key the count tables use.

    python -m scraper.roster_drift --year 2026 --semester fall --check
    python -m scraper.roster_drift --year 2026 --semester fall --mark-handled SIG
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

try:
    from . import windows
except ImportError:  # pragma: no cover - direct script execution
    import windows  # type: ignore[no-redef]

MIN_PASSES = int(os.environ.get("DRIFT_MIN_PASSES", "3") or 3)
COLS = ("year", "term", "signature", "added", "removed",
        "first_seen", "last_seen", "passes")


def _label(key) -> str:
    return f"{key[0]}({key[1]})"


def compare(roster_keys, live_keys) -> tuple[list[str], list[str]]:
    """(added, removed) as sorted "SBJT(LT)" labels."""
    roster = {tuple(k) for k in roster_keys}
    live = {tuple(k) for k in live_keys}
    return (sorted(_label(k) for k in live - roster),
            sorted(_label(k) for k in roster - live))


def signature(added: list[str], removed: list[str]) -> str:
    blob = json.dumps([added, removed], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def record(conn, *, year: str, term: str, added: list[str], removed: list[str],
           ts: str) -> str | None:
    """Upsert this pass's drift; a pass without drift writes nothing."""
    if not added and not removed:
        return None
    sig = signature(added, removed)
    conn.execute(
        "INSERT INTO roster_drift (year, term, signature, added, removed,"
        " first_seen, last_seen, passes) VALUES (?,?,?,?,?,?,?,1) "
        "ON CONFLICT(year, term, signature) DO UPDATE SET "
        " last_seen=excluded.last_seen, passes=roster_drift.passes + 1",
        (year, term, sig, json.dumps(added, ensure_ascii=False),
         json.dumps(removed, ensure_ascii=False), ts, ts))
    return sig


def sync(src, dst) -> int:
    """Copy the cloud's drift rows into the local catalog, keeping handled_at."""
    try:
        rows = src.execute(f"SELECT {', '.join(COLS)} FROM roster_drift").fetchall()
    except Exception as exc:  # noqa: BLE001 - an older cloud database has no table yet
        if "no such table" in str(exc).lower():
            return 0
        raise
    for row in rows:
        dst.execute(
            f"INSERT INTO roster_drift ({', '.join(COLS)}) "
            f"VALUES ({', '.join('?' * len(COLS))}) "
            "ON CONFLICT(year, term, signature) DO UPDATE SET "
            " added=excluded.added, removed=excluded.removed,"
            " first_seen=excluded.first_seen, last_seen=excluded.last_seen,"
            " passes=excluded.passes", tuple(row))
    dst.commit()
    return len(rows)


def _last_window_day(env: dict) -> str | None:
    days = []
    for name in (windows.CART_ENV, windows.ENROLL_ENV):
        for w in (env.get(name) or "").split(","):
            w = w.strip()
            if w:
                start, _, end = w.partition("..")
                days.append((end or start).strip())
    return max(days) if days else None


def decide(conn, *, year: str, term: str, today: str | None = None,
           env: dict | None = None) -> dict:
    """Should the local update crawl the catalog now? {"crawl": bool, ...}"""
    env = os.environ if env is None else env
    today = today or windows.today_iso()
    row = conn.execute(
        f"SELECT {', '.join(COLS)}, handled_at FROM roster_drift "
        "WHERE year=? AND term=? ORDER BY last_seen DESC LIMIT 1",
        (year, term)).fetchone()
    if row is None:
        return {"crawl": False, "reason": "no drift recorded"}
    rec = dict(zip(COLS + ("handled_at",), tuple(row)))
    rec["added"], rec["removed"] = json.loads(rec["added"]), json.loads(rec["removed"])
    newest = conn.execute(
        "SELECT MAX(ts) FROM count_passes WHERE year=? AND term=?",
        (year, term)).fetchone()[0]
    out = {"signature": rec["signature"], "added": rec["added"],
           "removed": rec["removed"], "passes": rec["passes"]}
    if newest and rec["last_seen"] < newest:
        return {**out, "crawl": False, "reason": "drift no longer present in the newest pass"}
    if rec["handled_at"]:
        return {**out, "crawl": False, "reason": f"already handled at {rec['handled_at']}"}
    if rec["passes"] < MIN_PASSES:
        return {**out, "crawl": False,
                "reason": f"seen on {rec['passes']} pass(es), need {MIN_PASSES}"}
    last_day = _last_window_day(env)
    if last_day is None or today <= last_day:
        return {**out, "crawl": False,
                "reason": f"registration/change periods not over (last window day {last_day})"}
    return {**out, "crawl": True,
            "reason": f"+{len(rec['added'])} / -{len(rec['removed'])} classes "
                      f"for {rec['passes']} passes since {rec['first_seen']}"}


def mark_handled(conn, *, year: str, term: str, sig: str, ts: str) -> None:
    conn.execute("UPDATE roster_drift SET handled_at=? "
                 "WHERE year=? AND term=? AND signature=?", (ts, year, term, sig))
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime

    try:
        from . import db
    except ImportError:  # pragma: no cover
        import db  # type: ignore[no-redef]
    TERM_CODES = {"spring": "U000200001U000300001", "summer": "U000200001U000300002",
                  "fall": "U000200002U000300001", "winter": "U000200002U000300002"}
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", required=True)
    ap.add_argument("--semester", required=True, choices=sorted(TERM_CODES))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true",
                   help="print 'crawl' or 'none' (reason on stderr)")
    g.add_argument("--mark-handled", metavar="SIGNATURE")
    args = ap.parse_args(argv)
    term = TERM_CODES[args.semester]
    conn = db.connect()
    try:
        if args.check:
            d = decide(conn, year=args.year, term=term)
            print("crawl" if d["crawl"] else "none")
            print(f"roster drift: {json.dumps(d, ensure_ascii=False)}", file=sys.stderr)
        else:
            mark_handled(conn, year=args.year, term=term, sig=args.mark_handled,
                         ts=datetime.now().isoformat(timespec="seconds"))
            print(f"marked {args.mark_handled} handled")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
