"""Flush the catalog — fully or by (year, term). Honors the DB_BACKEND env so it
flushes whichever backend the server uses. Use flush.sh for a friendly,
confirmation-gated wrapper; this is the bare CLI it calls."""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from contextlib import nullcontext

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db
import process_lock


def main() -> None:
    ap = argparse.ArgumentParser(description="Flush the class-checker catalog")
    ap.add_argument("--year", default="2026", help="year for scoped flushes")
    ap.add_argument("--all", action="store_true", help="wipe every term")
    ap.add_argument("terms", nargs="*", help="cmmnCd term codes to flush")
    args = ap.parse_args()

    if not args.all and not args.terms:
        ap.error("specify --all or one or more term codes")

    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    with lock:
        conn = db.connect()
        try:
            db.init_schema(conn)
            if args.all:
                db.clear_all(conn)
                print("flushed: ALL terms")
            else:
                db.clear_terms(conn, [(args.year, t) for t in args.terms])
                print(f"flushed: {args.year} {', '.join(args.terms)}")

            print("classes remaining:",
                  conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0])
        finally:
            conn.close()


if __name__ == "__main__":
    main()
