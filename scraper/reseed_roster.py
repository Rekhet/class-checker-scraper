#!/usr/bin/env python3
"""Replace ONE term's roster in the collector's cloud database with the local
catalog's (classes + class_slots), after a catalog crawl.

migrate_to_turso.py refills every table for every year (~110k classes); a
drift crawl changes one term, so this rewrites only that term's rows in one
transaction. count_samples / count_passes / count_latest are not touched: the
next collector pass records a new class's first sample and tombstones a class
that left the roster by itself.

    set -a; . ./turso-remote.env; set +a
    python -m scraper.reseed_roster --year 2026 --semester fall
"""
from __future__ import annotations

import os
import sys

try:
    from . import db
except ImportError:  # pragma: no cover - direct script execution
    import db  # type: ignore[no-redef]

TERM_CODES = {"spring": "U000200001U000300001", "summer": "U000200001U000300002",
              "fall": "U000200002U000300001", "winter": "U000200002U000300002"}
# A crawl that returns far fewer classes than the cloud already has is a broken
# crawl, not a smaller semester: refuse rather than shrink the roster.
MIN_RATIO = 0.9


def _cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def reseed(local, remote, *, year: str, term: str, force: bool = False) -> dict:
    classes = local.execute(
        "SELECT * FROM classes WHERE year=? AND term=?", (year, term)).fetchall()
    before = remote.execute(
        "SELECT COUNT(*) FROM classes WHERE year=? AND term=?", (year, term)).fetchone()[0]
    if not classes:
        raise SystemExit(f"error: the local catalog has no classes for {year} {term}")
    if before and len(classes) < MIN_RATIO * before and not force:
        raise SystemExit(f"error: local {len(classes)} classes vs {before} in the cloud "
                         f"(< {MIN_RATIO:.0%}); pass --force if that is really right")
    ccols = [c for c in _cols(local, "classes") if c in set(_cols(remote, "classes"))]
    ids = [r[0] for r in local.execute(
        "SELECT id FROM classes WHERE year=? AND term=?", (year, term)).fetchall()]
    slots = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        slots += local.execute(
            # DISTINCT drops the per-meeting room: the cloud keeps the
            # room-less slot table (its UNIQUE key has no room column).
            "SELECT DISTINCT class_id, day_index, period, start_time, end_time "
            "FROM class_slots "
            f"WHERE class_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    remote.execute(
        "DELETE FROM class_slots WHERE class_id IN "
        "(SELECT id FROM classes WHERE year=? AND term=?)", (year, term))
    remote.execute("DELETE FROM classes WHERE year=? AND term=?", (year, term))
    db.insert_chunked(remote, "classes", ccols,
                      [tuple(d[c] for c in ccols)
                       for d in _rows_as_dicts(local, classes, ccols)])
    db.insert_chunked(remote, "class_slots",
                      ["class_id", "day_index", "period", "start_time", "end_time"],
                      [tuple(r) for r in slots])
    remote.commit()
    return {"before": before, "classes": len(classes), "slots": len(slots)}


def _rows_as_dicts(local, rows, cols):
    names = _cols(local, "classes")
    for r in rows:
        values = dict(zip(names, tuple(r)))
        yield {c: values[c] for c in cols}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import libsql

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", required=True)
    ap.add_argument("--semester", required=True, choices=sorted(TERM_CODES))
    ap.add_argument("--src", default="data/turso.db", help="local catalog file")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    if not url.startswith(("libsql://", "https://", "wss://")):
        print("error: TURSO_DATABASE_URL must point at the collector's cloud database",
              file=sys.stderr)
        return 2
    local = libsql.connect(args.src)
    remote = libsql.connect(url, auth_token=os.environ.get("TURSO_AUTH_TOKEN", "").strip())
    out = reseed(local, remote, year=args.year, term=TERM_CODES[args.semester],
                 force=args.force)
    print(f"reseeded {args.year} {args.semester}: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
