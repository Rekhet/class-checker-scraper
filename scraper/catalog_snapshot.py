#!/usr/bin/env python3
"""Upcoming terms' catalog: collected on a GitHub runner, applied locally.

sugang lists a coming term's catalog and 평가방식 weeks before its timetable.
Those terms (collect.env UPCOMING_TERMS, minus the term being counted) are read
remotely and only deployed from this machine:

* ``collect`` (runner, .github/workflows/collect-catalog.yml): per term, one
  Excel plus the four 평가방식 sweeps, packed into ONE row of the cloud table
  ``catalog_snapshots`` (zlib JSON, ~100 KB for 4,000 classes). An unchanged
  term only refreshes ``checked_at``, so the cloud write quota sees one row per
  term per run. A term with no classes, no 평가방식 tags, or fewer than
  MIN_KEEP x the classes of its previous snapshot is refused and fails the run.
* ``pull`` (local, scripts/update.sh): rebuilds each term whose digest differs
  from the one last applied, exactly as a local crawl would (classes, slots
  with rooms, 평가방식, change log). A snapshot older than --max-age-hours is
  reported (exit 3) so a stopped schedule raises the ops alert. Exit 4 means
  a term was rebuilt (update.sh then exports every term), 0 nothing changed.

The upcoming classes never enter the cloud ``classes`` table: reseed_roster
copies local class ids there, and rows inserted by the runner would collide.

Timestamps are naive UTC ISO strings.

    python -m scraper.catalog_snapshot collect            # runner
    python -m scraper.catalog_snapshot pull               # local
    python -m scraper.catalog_snapshot init-remote        # once, schema
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import zlib
from datetime import datetime, timedelta, timezone

try:
    from . import changelog, crawl, db, excel
except ImportError:  # pragma: no cover - direct script execution
    import changelog  # type: ignore[no-redef]
    import crawl  # type: ignore[no-redef]
    import db  # type: ignore[no-redef]
    import excel  # type: ignore[no-redef]

TERM_CODES = {"spring": "U000200001U000300001", "summer": "U000200001U000300002",
              "fall": "U000200002U000300001", "winter": "U000200002U000300002"}
# Same bar as reseed_roster: a much smaller term is a broken read, not a
# smaller semester.
MIN_KEEP = 0.9
MAX_AGE_HOURS = 30   # three runs a day; a whole missed day is a stopped schedule


class SnapshotError(RuntimeError):
    """A term that must not be pushed."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


def parse_terms(spec: str, *, count_year: str, count_sem: str) -> list[tuple[str, str]]:
    """'2026:winter 2027:spring' -> [(year, term code)], minus the counted term."""
    out, bad = [], []
    for entry in (spec or "").split():
        year, _, sem = entry.partition(":")
        if not (len(year) == 4 and year.isdigit() and sem in TERM_CODES):
            bad.append(entry)
            continue
        if (year, sem) != (count_year, count_sem):
            out.append((year, TERM_CODES[sem]))
    if bad:
        raise ValueError(f"UPCOMING_TERMS entries not YEAR:spring|summer|fall|winter: "
                         f"{' '.join(bad)}")
    return out


def _key(k: tuple) -> str:
    return "|".join(k)


def build_snapshot(year: str, term: str, label: str, recs: list[dict],
                   methods: dict[tuple, str], switchable: set[tuple]) -> dict:
    data = {
        "year": year, "term": term, "label": label,
        "classes": sorted(recs, key=lambda r: (r["sbjt_cd"], r["lt_no"], r["subh_cd"])),
        "grading": {_key(k): v for k, v in methods.items()},
        "switchable": sorted(_key(k) for k in switchable),
    }
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return {"year": year, "term": term, "label": label,
            "digest": hashlib.sha256(raw).hexdigest(), "classes": len(recs),
            "graded": len(methods), "payload": zlib.compress(raw, 9)}


def init_remote(remote) -> None:
    remote.execute(db.CATALOG_SNAPSHOTS_DDL)
    remote.commit()


def check_snapshot(remote, snap: dict, *, min_keep: float = MIN_KEEP) -> None:
    where = f"{snap['year']}/{snap['term']}"
    if not snap["classes"]:
        raise SnapshotError(f"{where}: Excel has no classes")
    if not snap["graded"]:
        raise SnapshotError(f"{where}: 평가방식 sweeps returned nothing")
    row = remote.execute("SELECT classes FROM catalog_snapshots WHERE year=? AND term=?",
                         (snap["year"], snap["term"])).fetchone()
    if row and snap["classes"] < row[0] * min_keep:
        raise SnapshotError(f"{where}: {snap['classes']} classes, previous snapshot "
                            f"{row[0]} (below {min_keep:.0%}); not pushed")


def push(remote, snap: dict, *, now: str | None = None) -> str:
    """Store one term's snapshot; 'new', 'changed' or 'unchanged'."""
    now = now or _now()
    row = remote.execute("SELECT digest FROM catalog_snapshots WHERE year=? AND term=?",
                         (snap["year"], snap["term"])).fetchone()
    if row and row[0] == snap["digest"]:
        remote.execute("UPDATE catalog_snapshots SET checked_at=? WHERE year=? AND term=?",
                       (now, snap["year"], snap["term"]))
        status = "unchanged"
    else:
        remote.execute(
            "INSERT OR REPLACE INTO catalog_snapshots"
            "(year, term, label, digest, classes, fetched_at, checked_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (snap["year"], snap["term"], snap["label"], snap["digest"],
             snap["classes"], now, now, snap["payload"]))
        status = "changed" if row else "new"
    remote.commit()
    return status


def apply_snapshot(local, row) -> dict:
    """Rebuild one term from a snapshot row, as crawl.refresh_all would."""
    year, term, label, digest, classes, fetched_at, checked_at, payload = row
    data = json.loads(zlib.decompress(bytes(payload)).decode("utf-8"))
    year_terms = [(year, term)]
    run_id = db.start_run(local, [term])
    try:
        old = changelog.snapshot(local, year_terms)
        db.clear_terms(local, year_terms)
        db.upsert_term(local, term, year, label)
        slots = 0
        for rec in data["classes"]:
            cid = db.upsert_class(local, rec)
            for s in rec["slots"]:
                slots += bool(db.add_slot(local, cid, s))
        methods = {tuple(k.split("|")): v for k, v in data["grading"].items()}
        switchable = {tuple(k.split("|")) for k in data["switchable"]}
        db.apply_grading(local, year, term, methods, switchable)
        local.execute(
            "INSERT OR REPLACE INTO catalog_snapshots"
            "(year, term, label, digest, classes, fetched_at, checked_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (year, term, label, digest, classes, fetched_at, checked_at, payload))
        local.commit()
        db.finish_run(local, run_id, "done", "catalog snapshot", classes, slots)
    except Exception as e:
        db.finish_run(local, run_id, "error", str(e))
        raise
    try:   # the change log is a side effect; never fail a good rebuild over it
        rows, changes = changelog.diff(old, changelog.snapshot(local, year_terms))
        changelog.write_run_log(run_id, rows, changes)
        print(f"{label}: {classes} classes · "
              f"{changelog.summary_line(changes, None)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"warn: change log for {year}/{term} failed: {e}", file=sys.stderr)
    return {"classes": classes, "slots": slots}


def pull(remote, local, *, now: str | None = None,
         max_age_hours: float = MAX_AGE_HOURS,
         terms: list[tuple[str, str]] | None = None) -> dict:
    """Apply every cloud snapshot whose digest differs from the local one.

    `terms` limits it to the configured upcoming terms: once a term becomes
    the counted one, its leftover snapshot must not overwrite the crawled
    catalog or raise a stale alert."""
    now_dt = datetime.fromisoformat(now or _now())
    have = {(r[0], r[1]): r[2] for r in local.execute(
        "SELECT year, term, digest FROM catalog_snapshots").fetchall()}
    cloud = remote.execute(
        "SELECT year, term, digest, checked_at FROM catalog_snapshots ORDER BY year, term"
    ).fetchall()
    if terms is not None:
        cloud = [r for r in cloud if (r[0], r[1]) in set(terms)]
    applied, stale = [], []
    for year, term, digest, checked_at in cloud:
        if now_dt - datetime.fromisoformat(checked_at) > timedelta(hours=max_age_hours):
            stale.append((year, term))
        if have.get((year, term)) == digest:
            continue
        row = remote.execute(
            "SELECT year, term, label, digest, classes, fetched_at, checked_at, payload"
            " FROM catalog_snapshots WHERE year=? AND term=?", (year, term)).fetchone()
        apply_snapshot(local, tuple(row))
        applied.append((year, term))
    return {"terms": len(cloud), "applied": applied, "stale": stale}


def open_local(dest: str):
    """The local catalog FILE. Never db.connect(): pull runs with the cloud
    credentials in its environment, and db.connect() follows
    TURSO_DATABASE_URL — on 2026-10-08 that pointed init_schema at the cloud
    and rebuilt its class_slots."""
    import libsql

    if "://" in dest:
        raise SystemExit(f"error: --dest must be a local file, not {dest}")
    if not os.path.isfile(dest):
        raise SystemExit(f"error: local catalog not found: {dest}")
    return db._Conn(libsql.connect(dest), "libsql")


def _remote():
    import libsql

    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    if not url.startswith(("libsql://", "https://", "wss://")):
        raise SystemExit("error: TURSO_DATABASE_URL must point at the cloud database")
    return libsql.connect(url, auth_token=os.environ.get("TURSO_AUTH_TOKEN", "").strip())


def _collect_one(client, year: str, term: str, label: str) -> dict:
    content = excel.fetch_excel(client, year, term)
    recs = excel.parse_excel(content, year, term) if content else []
    methods = crawl.grading_methods(client, year, term, label=label)
    switchable = crawl.grading_switchable(client, year, term, label=label)
    return build_snapshot(year, term, label, recs, methods, switchable)


def collect(terms: list[tuple[str, str]], *, dry_run: bool = False) -> int:
    client = crawl.SnuClient()
    labels: dict[str, dict[str, str]] = {}   # year -> term code -> label
    failed = []
    for year, term in terms:
        try:
            if year not in labels:
                labels[year] = {t["term"]: t["label"] for t in client.fetch_terms(year)}
            label = labels[year].get(term) or f"{year} {term}"
            for attempt in (1, 2):   # one retry: a lost session or a slow Excel
                try:
                    snap = _collect_one(client, year, term, label)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(f"retry {year}/{term}: {e}", flush=True)
                    client.refresh()
            if dry_run:
                print(f"{label}: {snap['classes']} classes, {snap['graded']} graded, "
                      f"digest {snap['digest'][:12]}; dry run, not pushed", flush=True)
                continue
            remote = _remote()   # short connection: never held across a crawl
            try:
                check_snapshot(remote, snap)
                status = push(remote, snap)
            finally:
                remote.close()
            print(f"{label}: {snap['classes']} classes, {snap['graded']} graded, "
                  f"{len(snap['payload']) // 1024} KB, {status}", flush=True)
        except Exception as e:  # noqa: BLE001 - try every term, then fail loudly
            failed.append(f"{year}/{term}")
            print(f"error: {year}/{term}: {e}", file=sys.stderr, flush=True)
    if failed:
        print(f"error: failed terms: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="runner: read upcoming terms into the cloud")
    c.add_argument("--terms", default=None,
                   help="override UPCOMING_TERMS, e.g. '2026:winter 2027:spring'")
    c.add_argument("--dry-run", action="store_true", help="crawl, push nothing")
    p = sub.add_parser("pull", help="local: apply changed snapshots to the catalog")
    p.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    p.add_argument("--dest", default="data/turso.db", help="local catalog file")
    sub.add_parser("init-remote", help="create catalog_snapshots in the cloud")
    args = ap.parse_args(argv)

    if args.cmd == "init-remote":
        remote = _remote()
        init_remote(remote)
        print("catalog_snapshots ready")
        return 0
    spec = getattr(args, "terms", None)
    try:
        terms = parse_terms(spec if spec is not None else os.environ.get("UPCOMING_TERMS", ""),
                            count_year=os.environ.get("COUNT_YEAR", ""),
                            count_sem=os.environ.get("COUNT_SEM", ""))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.cmd == "collect":
        if not terms:
            print("no upcoming terms configured; nothing to collect")
            return 0
        return collect(terms, dry_run=args.dry_run)

    local = open_local(args.dest)
    remote = _remote()
    try:
        db.init_schema(local)
        out = pull(remote, local, max_age_hours=args.max_age_hours, terms=terms)
    finally:
        local.close()
        remote.close()
    print(f"catalog snapshots: {out['terms']} term(s), applied "
          f"{[f'{y}/{t}' for y, t in out['applied']]}", flush=True)
    if out["stale"]:
        print(f"error: snapshot not refreshed for over {args.max_age_hours:g} h: "
              f"{[f'{y}/{t}' for y, t in out['stale']]} (collect-catalog stopped?)",
              file=sys.stderr)
        return 3
    return 4 if out["applied"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
