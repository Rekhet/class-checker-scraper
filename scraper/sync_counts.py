#!/usr/bin/env python3
"""Overlay the newest collected count sample onto the local catalog rows.

The 인원 (counts) pass no longer runs on this machine: cron-job.org fires the
GitHub Actions collector every 10 minutes, `scraper/cloud_collect.py` writes
count_samples to the cloud Turso database, and `scraper/pull_counts.py` merges
those rows into the local catalog. Only count_samples moves that way, but the
static export reads the volatile columns (applied/cart/enrolled/quota/
cancel_vacancy) off the `classes` table, so the published search rows need the
newest sample copied back onto the catalog:

    DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db \\
    python -m scraper.sync_counts --year 2026 --semester fall

Idempotent and local-only: it opens no network session. NULL sample columns (a
metric outside its collection window) leave the stored value untouched.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from . import db, export_json, windows
except ImportError:  # pragma: no cover - direct script execution
    import db  # type: ignore[no-redef]
    import export_json  # type: ignore[no-redef]
    import windows  # type: ignore[no-redef]


# How old the newest sample may be before the publisher says so, per cadence.
# Fast windows collect every ~10 minutes, slow ones (수강취소) hourly; both get
# generous slack because GitHub's cron dispatch routinely runs late.
FAST_STALE_MINUTES = 45
SLOW_STALE_MINUTES = 180

# Distinct from argparse's 2 and a crash's 1, so a caller can tell "published,
# but the data behind it is stale" from "could not run".
STALE_EXIT = 3


def staleness_warning(ts: str | None, now: datetime | None = None) -> str | None:
    """A warning line when counts should be moving but the newest sample is old.

    The collector runs off-machine (cron-job.org -> GitHub Actions), so a
    stalled schedule produces no failure here at all: the pull simply returns
    no rows and the publish is a no-op. This is deliberately a warning, not an
    error — outside every collection window nothing is expected to arrive, and
    failing the run then would be noise rather than signal.
    """
    active = windows.collection_active(windows.today_iso(now))
    if not (active["cart"] or active["enroll"] or active["slow"]):
        return None
    limit = (FAST_STALE_MINUTES if (active["cart"] or active["enroll"])
             else SLOW_STALE_MINUTES)
    if ts is None:
        return (f"warn: inside a collection window but no samples exist yet "
                f"(expected one every {limit} minutes at worst)")
    try:
        sampled = datetime.fromisoformat(ts)
    except ValueError:
        return f"warn: unparseable newest sample timestamp {ts!r}"
    reference = windows.now_local(now).replace(tzinfo=None)
    age = (reference - sampled.replace(tzinfo=None)).total_seconds() / 60
    if age <= limit:
        return None
    return (f"warn: newest sample is {age:.0f} minutes old ({ts}) inside a "
            f"collection window; check the cron-job.org dispatch and the "
            f"collect-counts workflow")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Sync catalog counts from samples")
    ap.add_argument("--year", default=os.environ.get("COUNT_YEAR", ""),
                    help="catalog year (default: COUNT_YEAR)")
    ap.add_argument("--semester", default=os.environ.get("COUNT_SEM", ""),
                    help="semester alias or term code (default: COUNT_SEM)")
    ap.add_argument("--fail-if-stale", action="store_true",
                    help="exit 3 (instead of only warning) when a collection "
                         "window is open and the newest sample is old")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    year = args.year.strip()
    terms = export_json.select_term_codes(args.semester)
    if not year or not terms:
        print("error: set --year/--semester (or COUNT_YEAR/COUNT_SEM)",
              file=sys.stderr)
        return 2

    conn = db.connect()
    newest = None
    try:
        for term in terms:
            out = db.apply_latest_samples(conn, year, term)
            if out["ts"] is None:
                print(f"{year} {term}: no samples collected yet")
            else:
                print(f"{year} {term}: {out['updated']} classes synced "
                      f"from sample {out['ts']}")
                if newest is None or out["ts"] > newest:
                    newest = out["ts"]
    finally:
        conn.close()

    warning = staleness_warning(newest)
    if warning:
        print(warning, file=sys.stderr)
        if args.fail_if_stale:
            return STALE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
