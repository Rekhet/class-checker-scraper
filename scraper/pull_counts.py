#!/usr/bin/env python3
"""Pull cloud-collected count samples into the local libsql catalog.

The GitHub Actions collector appends count_samples rows to the cloud Turso
database; this merges the new rows into the local catalog (data/turso.db) so
the export/trend pipeline sees them:

    TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io \\
    TURSO_AUTH_TOKEN=<token> \\
    python scraper/pull_counts.py

Idempotent: rows are matched on the (year, term, sbjt_cd, lt_no, ts) natural
key, and the last pulled ts is kept in data/pull_counts.state so repeated runs
only fetch the tail. Local rows collected by a concurrently running local
worker are left untouched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = PROJECT_ROOT / "data" / "turso.db"
STATE_PATH = PROJECT_ROOT / "data" / "pull_counts.state"

COLUMNS = ("year", "term", "sbjt_cd", "lt_no", "ts",
           "applied", "cart", "enrolled", "quota", "cancel_vacancy")
_COLS = ", ".join(COLUMNS)
_INSERT = (
    f"INSERT INTO count_samples ({_COLS}) "
    f"SELECT {', '.join('?' * len(COLUMNS))} "
    "WHERE NOT EXISTS (SELECT 1 FROM count_samples "
    "WHERE year=? AND term=? AND sbjt_cd=? AND lt_no=? AND ts=?)"
)


def merge_samples(src, dst, since_ts: str | None = None) -> dict:
    """Copy count_samples rows from src into dst, skipping existing keys.

    The source is read one ts group (~one collection pass) at a time: a single
    SELECT over a large remote backlog dies mid-stream ("unexpected EOF"), and
    per-ts pages keep each read small while the natural-key dedupe keeps a
    partially merged run safe to repeat.

    Returns {"inserted": n, "max_ts": ts} where max_ts is the newest source ts
    seen (or since_ts unchanged when the source tail is empty) — the caller's
    cursor for the next pull.
    """
    ts_query = "SELECT DISTINCT ts FROM count_samples"
    params: tuple = ()
    if since_ts is not None:
        ts_query += " WHERE ts > ?"
        params = (since_ts,)
    ts_values = sorted(t for (t,) in src.execute(ts_query, params).fetchall())

    inserted = 0
    total = 0
    max_ts = since_ts
    for ts in ts_values:
        rows = src.execute(
            f"SELECT {_COLS} FROM count_samples WHERE ts = ?", (ts,)
        ).fetchall()
        for row in rows:
            row = tuple(row)
            cur = dst.execute(_INSERT, row + row[:5])
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        dst.commit()
        total += len(rows)
        if max_ts is None or ts > max_ts:
            max_ts = ts
    dst.commit()
    return {"inserted": inserted, "rows": total, "max_ts": max_ts}


def _read_state() -> str | None:
    try:
        value = STATE_PATH.read_text(encoding="utf-8").strip()
        return value or None
    except FileNotFoundError:
        return None


def _write_state(ts: str | None) -> None:
    if ts:
        STATE_PATH.write_text(ts + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    import libsql

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help="local libsql catalog file (default: data/turso.db)")
    ap.add_argument("--full", action="store_true",
                    help="ignore the saved cursor and rescan every source row")
    args = ap.parse_args(argv)

    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not url.startswith(("libsql://", "https://", "wss://")):
        print("error: TURSO_DATABASE_URL must point at the remote Turso "
              "database (libsql://…)", file=sys.stderr)
        return 2
    if not args.dest.is_file():
        print(f"error: local catalog not found: {args.dest}", file=sys.stderr)
        return 2

    src = libsql.connect(url, auth_token=token)
    dst = libsql.connect(str(args.dest))
    since = None if args.full else _read_state()
    out = merge_samples(src, dst, since_ts=since)
    _write_state(out["max_ts"])
    print(f"pulled {out['rows']} rows, inserted {out['inserted']}, "
          f"cursor {out['max_ts'] or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
