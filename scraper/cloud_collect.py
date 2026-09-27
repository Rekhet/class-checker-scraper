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

# Fraction of the seeded roster a pass must match before its samples are
# pushed. A full pass matches every class (8,652/8,652 on 2026-09-27); a few
# 폐강 classes dropping out of the live search cost well under 1%. Anything
# far below that is a broken fetch, not a quieter semester.
MIN_COVERAGE = float(os.environ.get("COUNT_MIN_COVERAGE", "0.95") or 0.95)

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
        # Only the NEWEST keyframe pass: the scratch DB has no history of its
        # own, and db.keyframe_due() needs just the time this term was last
        # re-stated in full (MAX(ts)), or every run would think one is
        # overdue. Copying all of them read one more row per day, forever.
        "keyframes": _copy_query(
            remote, local, "count_passes",
            "full=1 AND year=? AND term=? ORDER BY ts DESC LIMIT 1",
            (year, term)
        ),
    }
    local.commit()
    return counts


def overlay_latest(local, *, year: str, term: str) -> dict:
    """Give the scratch roster the cloud's CURRENT counts, not the seeded ones.

    The cloud `classes` table keeps whatever counts it was seeded with; only
    count_latest moves. The sampler compares `classes` against count_latest, so
    any class the live fetch does not reach (an empty page 1, a class that left
    the search) would otherwise be recorded as a "change" back to its seeded
    value — silently rewinding both the trend and the cloud baseline.
    """
    return db.apply_latest_samples(db._Conn(local, "sqlite"), year, term)


class CollectionError(RuntimeError):
    """A pass that must fail the runner loudly instead of pushing."""


def check_coverage(out: dict, roster: int, *,
                   minimum: float = MIN_COVERAGE) -> float:
    """Raise when the pass matched too little of the roster to be trusted."""
    updated = int(out.get("updated") or 0)
    coverage = updated / roster if roster else 0.0
    if coverage < minimum:
        raise CollectionError(
            f"live pass matched {updated}/{roster} classes "
            f"({coverage:.1%} < {minimum:.0%}); not pushing")
    return coverage


def push_samples(local, remote, *, skip_pass_ts=()) -> dict:
    """Push this run's deltas, its pass rows, and the moved classes' new values.

    Only classes whose numbers changed produce a sample, so a pass writes about
    1% of the roster instead of all of it; the count_passes row keeps the trend
    axis complete even when nothing moved at all.
    """
    # The scratch DB was bootstrapped with the cloud's keyframe passes so the
    # sampler knows when the term was last re-stated; those are already in the
    # cloud, and pushing their count_latest rows again would rewrite the whole
    # roster every run - exactly the write amplification deltas exist to avoid.
    skip = set(skip_pass_ts)
    rows = local.execute(
        f"SELECT {', '.join(SAMPLE_COLS)} FROM count_samples").fetchall()
    passes = [r for r in local.execute(
        f"SELECT {', '.join(PASS_COLS)} FROM count_passes").fetchall()
        if r[2] not in skip]
    own_ts = {r[2] for r in passes}
    latest = [r for r in local.execute(
        "SELECT year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota,"
        " cancel_vacancy FROM count_latest").fetchall() if r[4] in own_ts]
    # OR IGNORE on the (class, pass) unique key: re-running a push whose
    # commit was lost must not duplicate the samples that did land.
    db.insert_chunked(remote, "count_samples", SAMPLE_COLS,
                      [tuple(r) for r in rows], ignore=True)
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
    ap.add_argument("--dry-run", action="store_true",
                    help="bootstrap, crawl and sample into the scratch DB, but "
                         "push nothing to the cloud (for verifying a change)")
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
    print(f"bootstrap: {counts}", flush=True)
    if not counts["classes"]:
        # Every live row would go unmatched and each pass would record an empty
        # axis point: the state a new semester is in until the cloud roster is
        # re-seeded from the local catalog (see systemd/README.md).
        raise CollectionError(
            f"cloud roster for {args.year} {args.semester} ({term}) is empty; "
            "re-seed it from the local catalog before collecting")
    print(f"overlay: {overlay_latest(local, year=args.year, term=term)}",
          flush=True)

    # Captured BEFORE sampling: everything already here came from the cloud.
    bootstrapped_passes = [r[0] for r in local.execute(
        "SELECT ts FROM count_passes").fetchall()]

    conn = db._Conn(local, "sqlite")
    out = crawl.refresh_counts_all(
        conn, [args.year], terms=[term],
        collect_cart=False, collect_enrollment=True, windowed=True,
        strict_sampling=True,
    )
    print(f"collect: {out}", flush=True)
    if out.get("skipped"):
        # the window closed between the gate above and the pass itself
        print(f"skipped: {out['skipped']}")
        return 0
    print(f"coverage: {check_coverage(out, counts['classes']):.2%}", flush=True)
    if args.dry_run:
        n = local.execute("SELECT COUNT(*) FROM count_samples").fetchone()[0]
        local.close()
        print(f"dry run: {n} sample rows left in {args.scratch}; nothing pushed")
        return 0

    remote = _remote_connect()
    pushed = push_samples(local, remote, skip_pass_ts=bootstrapped_passes)
    remote.close()
    local.close()
    print(f"push: {pushed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
