#!/usr/bin/env python3
"""One windowed counts pass for a GitHub-hosted runner.

Crawling directly against the remote Turso database does not work: the crawl
holds a connection for minutes between HTTP fetches and Turso expires the
interactive stream ("Hrana: stream not found"), and every per-row statement
pays a network round trip. Instead each run:

1. bootstraps a LOCAL scratch libsql file from the cloud catalog (one short
   remote connection, a couple of SELECTs),
2. runs the ordinary windowed counts pass against that local file, and
3. pushes the run's count_samples deltas, its count_passes row, and the moved
   classes' count_latest values back to the cloud over a second short remote
   connection.

Samples are DELTAS: a class is written only when one of its collected numbers
changed (about 1% of the roster between two 10-minute passes), with
count_passes carrying the time axis and count_latest the current value each
pass compares against. Storing every class every pass exhausted the database's
write quota mid-semester on 2026-09-04.

The cloud classes table keeps its seeded counts; only the counts tables matter
for the 인원 추이 trend, and the local machine's own refresh keeps the local
catalog current. Usage (TURSO_DATABASE_URL/TURSO_AUTH_TOKEN in the env):

    python -m scraper.cloud_collect --year 2026 --semester fall
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from . import crawl, db
except ImportError:  # pragma: no cover - direct script execution
    import crawl  # type: ignore[no-redef]
    import db  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRATCH = PROJECT_ROOT / "data" / "cloud-collect.db"

TERM_CODES = {
    "spring": "U000200001U000300001",
    "summer": "U000200001U000300002",
    "fall": "U000200002U000300001",
    "winter": "U000200002U000300002",
}


def _copy_query(remote, local, table: str, where: str = "", params=()) -> int:
    cur = remote.execute(
        f"SELECT * FROM {table}" + (f" WHERE {where}" if where else ""), params
    )
    # column names via the DB-API cursor description: works for both sqlite3
    # and libsql connections (libsql rows have no .keys())
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return 0
    db.insert_chunked(local, table, cols, [tuple(r) for r in rows])
    return len(rows)


SAMPLE_COLS = ["year", "term", "sbjt_cd", "lt_no", "ts",
               "applied", "cart", "enrolled", "quota", "cancel_vacancy"]
PASS_COLS = ["year", "term", "ts", "applied", "cart", "enrolled", "full"]


def bootstrap_local(remote, local, *, year: str, term: str) -> dict:
    """Copy the terms list, the scoped classes, and the current counts baseline.

    count_latest is what makes a delta pass possible: the collector has to know
    each class's last recorded numbers to tell which ones actually moved. It is
    one row per class (like the roster itself), so the read stays flat as the
    sample history grows.
    """
    db.init_schema(db._Conn(local, "sqlite") if not hasattr(local, "backend") else local)
    counts = {
        "terms": _copy_query(remote, local, "terms"),
        "classes": _copy_query(
            remote, local, "classes", "year=? AND term=?", (year, term)
        ),
        "latest": _copy_query(
            remote, local, "count_latest", "year=? AND term=?", (year, term)
        ),
        # Only the keyframe passes: the scratch DB has no history of its own,
        # and db.keyframe_due() has to know when this term was last re-stated
        # in full or every run would think a keyframe is overdue.
        "keyframes": _copy_query(
            remote, local, "count_passes", "full=1 AND year=? AND term=?",
            (year, term)
        ),
    }
    local.commit()
    return counts


def push_samples(local, remote) -> dict:
    """Push this run's deltas, its pass rows, and the moved classes' new values.

    Only classes whose numbers changed produce a sample, so a pass writes about
    1% of the roster instead of all of it; the count_passes row keeps the trend
    axis complete even when nothing moved at all.
    """
    rows = local.execute(
        f"SELECT {', '.join(SAMPLE_COLS)} FROM count_samples").fetchall()
    passes = local.execute(
        f"SELECT {', '.join(PASS_COLS)} FROM count_passes").fetchall()
    latest = local.execute(
        "SELECT year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota,"
        " cancel_vacancy FROM count_latest WHERE ts IN "
        "(SELECT ts FROM count_passes)").fetchall()
    db.insert_chunked(remote, "count_samples", SAMPLE_COLS,
                      [tuple(r) for r in rows])
    for pass_row in passes:
        remote.execute(
            f"INSERT OR REPLACE INTO count_passes ({', '.join(PASS_COLS)}) "
            f"VALUES ({', '.join('?' * len(PASS_COLS))})", tuple(pass_row))
    db.insert_chunked(
        remote, "count_latest",
        ["year", "term", "sbjt_cd", "lt_no", "ts",
         "applied", "cart", "enrolled", "quota", "cancel_vacancy"],
        [tuple(r) for r in latest], replace=True)
    remote.commit()
    return {"pushed": len(rows), "passes": len(passes), "latest": len(latest)}


def _remote_connect():
    import libsql

    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not url.startswith(("libsql://", "https://", "wss://")):
        raise SystemExit(
            "error: TURSO_DATABASE_URL must point at the remote Turso database"
        )
    return libsql.connect(url, auth_token=token)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sqlite3

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", required=True)
    ap.add_argument("--semester", required=True, choices=sorted(TERM_CODES))
    ap.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)
    args = ap.parse_args(argv)
    term = TERM_CODES[args.semester]

    # cheap gate first: outside every collection window, do not touch the cloud
    if not crawl._window_active(collect_cart=False, collect_enrollment=True):
        print("outside collection window; nothing to collect")
        return 0

    args.scratch.parent.mkdir(parents=True, exist_ok=True)
    if args.scratch.exists():
        args.scratch.unlink()   # every run starts from a fresh cloud snapshot
    local = sqlite3.connect(str(args.scratch))
    local.row_factory = sqlite3.Row

    remote = _remote_connect()
    counts = bootstrap_local(remote, local, year=args.year, term=term)
    remote.close()   # crawl takes minutes; never hold a remote stream across it
    print(f"bootstrap: {counts}")

    conn = db._Conn(local, "sqlite")
    out = crawl.refresh_counts_all(
        conn, [args.year], terms=[term],
        collect_cart=False, collect_enrollment=True, windowed=True,
    )
    print(f"collect: {out}")

    remote = _remote_connect()
    pushed = push_samples(local, remote)
    remote.close()
    local.close()
    print(f"push: {pushed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
