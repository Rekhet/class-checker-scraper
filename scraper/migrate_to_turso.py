"""Copy the local SQLite catalog into the configured backend (Turso/libSQL).

Source is always the local SQLite file (db.DB_PATH). Destination is whatever
db.connect() resolves from the environment, so run with the backend env set:

    DB_BACKEND=turso \\
    TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io \\
    TURSO_AUTH_TOKEN=<token> \\
    python scraper/migrate_to_turso.py

It is idempotent: each table on the destination is cleared, then refilled with
the source rows (ids preserved so class_slots FKs stay valid).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import nullcontext

import db
import process_lock

COPY_ORDER = ["terms", "classes", "class_slots", "crawl_runs",
              "count_latest"]   # FK-safe insert
# count_latest is derived state, but seeding it means a freshly created
# collector database compares its first pass against real numbers instead
# of writing the whole roster. Sample history is deliberately NOT copied:
# the export reads it locally, and the cloud only needs the baseline.

_insert_chunked = db.insert_chunked


def migrate(src_path=db.DB_PATH) -> dict:
    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row
    dst = db.connect()
    if dst.backend == "sqlite":
        raise SystemExit("DB_BACKEND is not Turso/libsql; refusing to migrate onto SQLite. "
                         "Set DB_BACKEND=turso and TURSO_* first.")
    db.init_schema(dst)

    # clear destination in reverse FK order
    for t in reversed(COPY_ORDER):
        dst.execute(f"DELETE FROM {t}")
    dst.commit()

    counts = {}
    for t in COPY_ORDER:
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        counts[t] = len(rows)
        if not rows:
            continue
        cols = list(rows[0].keys())
        _insert_chunked(dst, t, cols, [tuple(r[c] for c in cols) for r in rows])
        dst.commit()
    dst.sync()
    src.close()
    dst.close()
    return counts


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Copy a local SQLite catalog into the configured Turso/libSQL "
                    "backend (set DB_BACKEND=turso + TURSO_* in the environment).")
    ap.add_argument("--src", default=str(db.DB_PATH),
                    help="source SQLite file (default: the local sqlite catalog; "
                         "pass data/turso.db for the full libsql catalog)")
    args = ap.parse_args()
    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    try:
        with lock:
            out = migrate(src_path=args.src)
            print("migrated rows:", out)
    except process_lock.LockTimeout as exc:
        print(f"process lock busy: {exc}", file=sys.stderr)
        raise SystemExit(process_lock.LOCK_TIMEOUT_EXIT)
