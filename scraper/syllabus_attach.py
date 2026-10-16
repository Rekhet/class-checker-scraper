#!/usr/bin/env python3
"""Syllabus attachment links: collected on a GitHub runner, copied locally.

sugang's syllabus page (cc103) lists a class's 강의계획서 files (Korean and
English). Only the attachment entries that have a file name are kept; the
syllabus text itself is never stored. Downloads are plain links to shine.snu.ac.kr.

* ``collect`` (runner, .github/workflows/collect-syllabus.yml): per term, one
  ``POST cc103ajax.action`` per class (10 requests/s, no session, ``Referer``
  only), packed into ONE row of the cloud table ``syllabus_attach_snapshots``
  (zlib JSON). An unchanged term only refreshes ``checked_at``.
  - A class is retried once after 1 s. As soon as the failed classes exceed 2%
    of the term's roster the pass stops; after 60 s the whole term is collected
    again once. If that pass also exceeds 2%, the term is not pushed, the run
    exits 1, and the remaining terms still run.
  - Failures within the 2% keep the class's entry from the term's previous
    snapshot; only classes absent from it are listed as ``missing``.
  - A term with fewer than 90% of the previous snapshot's classes is refused;
    ``--force`` bypasses this guard (and, with ``--past``, also re-collects
    terms that already have a row).
  - ``--past`` walks every cloud term since 2020, pushing each term as it
    completes.
* ``pull`` (local, scripts/update.sh): copies every row whose digest differs
  from the local one. A target term (current and upcoming) not checked within
  --max-age-hours, or with no cloud row at all, is reported (exit 3). Exit 4 means something was applied,
  0 nothing changed. It never contacts sugang.

The roster comes from the cloud ``classes`` table, or for an upcoming term the
``catalog_snapshots`` payload. The remote connection is opened per read/push and
never held across a fetch loop (Hrana streams expire).

Timestamps are ISO 8601 with +09:00.

    python -m scraper.syllabus_attach collect [--past] [--force] [--dry-run]
    python -m scraper.syllabus_attach pull --dest data/turso.db
    python -m scraper.syllabus_attach init-remote        # once, schema
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

try:
    from . import db
    from .catalog_snapshot import TERM_CODES, _remote, open_local, parse_terms
except ImportError:  # pragma: no cover - direct script execution
    import db  # type: ignore[no-redef]
    from catalog_snapshot import (  # type: ignore[no-redef]
        TERM_CODES, _remote, open_local, parse_terms)

URL = "https://sugang.snu.ac.kr/sugang/cc/cc103ajax.action"
HEADERS = {"Referer": "https://sugang.snu.ac.kr/"}
INTERVAL = 0.1            # sugang tolerates 10 requests/s
RETRY_WAIT = 1.0
RESTART_WAIT = 60.0   # before the one whole-term restart
MAX_FAIL = 0.02
MIN_KEEP = 0.9
MAX_AGE_HOURS = 48
PAST_FROM = "2020"


class SnapshotError(RuntimeError):
    """A term that must not be pushed."""


class TooManyFailures(RuntimeError):
    """A fetch pass stopped early: failed classes exceed the tolerance."""


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def key(sbjt: str, lt: str, subh: str | None) -> str:
    return f"{sbjt}|{lt}|{subh or '000'}"


def parse_attach(resp: dict) -> dict[str, list[list]]:
    """{"ko": [[name, no, deta], ...], "en": [...]}; languages without a file
    and entries with an empty name are left out."""
    out: dict[str, list[list]] = {}
    for row in resp.get("LISTTAB03_LIST_ATTACH") or []:
        for lang, p in (("ko", "kor"), ("en", "eng")):
            name, no = (row.get(f"{p}FileNm") or "").strip(), row.get(f"{p}AttachNo")
            if name and no:
                out.setdefault(lang, []).append([name, no, row.get(f"{p}AttachDetaNo") or 0])
    return out


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def build_snapshot(year: str, term: str, attach: dict[str, dict], missing: list[str],
                   *, collected_at: str) -> dict:
    body = {"year": year, "term": term,
            "classes": {k: attach[k] for k in sorted(attach)},
            "missing": sorted(missing)}
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    payload = zlib.compress(_canonical({**body, "collected_at": collected_at}), 9)
    return {"year": year, "term": term, "digest": digest, "classes": len(attach),
            "missing": len(missing), "collected_at": collected_at, "payload": payload}


def init_remote(remote) -> None:
    remote.execute(db.SYLLABUS_ATTACH_DDL)
    remote.commit()


def check_snapshot(remote, snap: dict, *, total: int, max_fail: float = MAX_FAIL,
                   min_keep: float = MIN_KEEP) -> None:
    where = f"{snap['year']}/{snap['term']}"
    if snap["missing"] > total * max_fail:
        raise SnapshotError(f"{where}: {snap['missing']} of {total} classes failed "
                            f"(over {max_fail:.0%}); not pushed")
    row = remote.execute("SELECT classes, missing FROM syllabus_attach_snapshots"
                         " WHERE year=? AND term=?", (snap["year"], snap["term"])).fetchone()
    now = snap["classes"] + snap["missing"]
    if row and now < (row[0] + row[1]) * min_keep:
        raise SnapshotError(f"{where}: {now} classes, previous snapshot {row[0] + row[1]} "
                            f"(below {min_keep:.0%}); not pushed")


def push(remote, snap: dict, *, now: str) -> str:
    """Store one term's snapshot; 'new', 'changed' or 'unchanged'."""
    row = remote.execute("SELECT digest FROM syllabus_attach_snapshots"
                         " WHERE year=? AND term=?", (snap["year"], snap["term"])).fetchone()
    if row and row[0] == snap["digest"]:
        remote.execute("UPDATE syllabus_attach_snapshots SET checked_at=?"
                       " WHERE year=? AND term=?", (now, snap["year"], snap["term"]))
        status = "unchanged"
    else:
        remote.execute(
            "INSERT OR REPLACE INTO syllabus_attach_snapshots"
            "(year, term, digest, classes, missing, collected_at, checked_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (snap["year"], snap["term"], snap["digest"], snap["classes"], snap["missing"],
             snap["collected_at"], now, snap["payload"]))
        status = "changed" if row else "new"
    remote.commit()
    return status


# --- roster and fetching ----------------------------------------------------

def target_terms(env: dict) -> list[tuple[str, str]]:
    """The counted term plus UPCOMING_TERMS, as (year, term code)."""
    year, sem = env.get("COUNT_YEAR", ""), env.get("COUNT_SEM", "")
    if not (len(year) == 4 and year.isdigit() and sem in TERM_CODES):
        raise ValueError(f"COUNT_YEAR/COUNT_SEM not set to YEAR and "
                         f"spring|summer|fall|winter: {year!r} {sem!r}")
    return [(year, TERM_CODES[sem]),
            *parse_terms(env.get("UPCOMING_TERMS", ""), count_year=year, count_sem=sem)]


def past_terms(remote, targets) -> list[tuple[str, str]]:
    rows = remote.execute("SELECT DISTINCT year, term FROM classes WHERE year >= ?"
                          " ORDER BY year, term", (PAST_FROM,)).fetchall()
    skip = set(targets)
    return [(r[0], r[1]) for r in rows if (r[0], r[1]) not in skip]


def roster(remote, year: str, term: str) -> list[tuple[str, str, str]]:
    rows = remote.execute("SELECT DISTINCT sbjt_cd, lt_no, subh_cd FROM classes"
                          " WHERE year=? AND term=?", (year, term)).fetchall()
    if not rows:
        snap = remote.execute("SELECT payload FROM catalog_snapshots WHERE year=? AND term=?",
                              (year, term)).fetchone()
        recs = json.loads(zlib.decompress(bytes(snap[0])))["classes"] if snap else []
        rows = [(r["sbjt_cd"], r["lt_no"], r["subh_cd"]) for r in recs]
    return sorted({(s, lt, subh or "000") for s, lt, subh in rows})


class Http:
    """POST form data over one reused connection, return the parsed JSON body.
    Cookies are dropped after every response, so none are sent back."""

    def __init__(self) -> None:
        self.session = requests.Session()

    def post(self, url: str, data: dict, headers: dict) -> dict:
        try:
            r = self.session.post(url, data=data, headers=headers, timeout=30)
            r.raise_for_status()
            return r.json()
        finally:
            self.session.cookies.clear()


def _fetch_one(http, year: str, term: str, k: tuple[str, str, str]) -> dict:
    resp = http.post(URL, {"openSchyy": year, "openShtmFg": term[:10],
                           "openDetaShtmFg": term[10:], "sbjtCd": k[0], "ltNo": k[1],
                           "sbjtSubhCd": k[2]}, HEADERS)
    if not isinstance(resp, dict) or "LISTTAB03_LIST_ATTACH" not in resp:
        raise ValueError("response has no LISTTAB03_LIST_ATTACH")
    return parse_attach(resp)


def fetch_term(http, year: str, term: str, keys, *, interval: float = INTERVAL,
               sleep=time.sleep, max_fail: float | None = None) -> tuple[dict, list]:
    """(attach, missing): every key is tried, then retried once after 1 s.

    With `max_fail`, raises TooManyFailures as soon as the failed classes
    exceed that share of `keys`."""
    attach: dict[str, dict] = {}
    missing: list[str] = []
    for k in keys:
        name = key(*k)
        for attempt in (1, 2):
            try:
                attach[name] = _fetch_one(http, year, term, k)
                break
            except Exception as e:  # noqa: BLE001 - counted, judged by the guard
                if attempt == 1:
                    sleep(RETRY_WAIT)
                else:
                    missing.append(name)
                    print(f"warn: {year}/{term} {name}: {e}", file=sys.stderr, flush=True)
                    if max_fail is not None and len(missing) > len(keys) * max_fail:
                        raise TooManyFailures(f"{len(missing)} of {len(keys)} classes failed")
            finally:
                sleep(interval)
    return attach, missing


def _with_remote(factory, fn):
    remote = factory()
    try:
        return fn(remote)
    finally:
        remote.close()


def collect(terms, *, dry_run: bool = False, force: bool = False,
            skip_existing: bool = False, http=None, remote_factory=_remote) -> int:
    http = http or Http()
    failed = []
    for year, term in terms:
        where = f"{year}/{term}"
        try:
            def read(remote):
                row = remote.execute(
                    "SELECT payload FROM syllabus_attach_snapshots WHERE year=? AND term=?",
                    (year, term)).fetchone()
                if row and skip_existing and not force:
                    return None
                prev = json.loads(zlib.decompress(bytes(row[0])))["classes"] if row else {}
                return roster(remote, year, term), prev
            got = _with_remote(remote_factory, read)
            if got is None:
                print(f"{where}: already stored, skipped", flush=True)
                continue
            keys, prev = got
            if not keys:
                raise SnapshotError(f"{where}: empty roster")
            for attempt in (1, 2):
                try:
                    attach, missing = fetch_term(http, year, term, keys, sleep=time.sleep,
                                                 max_fail=MAX_FAIL)
                    break
                except TooManyFailures as e:
                    if attempt == 2:
                        raise SnapshotError(f"{where}: {e} twice (over {MAX_FAIL:.0%}); "
                                            f"not pushed") from e
                    print(f"retry {where}: {e}; again in {RESTART_WAIT:g} s", flush=True)
                    time.sleep(RESTART_WAIT)
            carried = [k for k in missing if k in prev]
            for k in carried:   # a hiccup must not drop the last good link
                attach[k] = prev[k]
            missing = [k for k in missing if k not in prev]
            snap = build_snapshot(year, term, attach, missing, collected_at=_now())
            if dry_run:
                print(f"{where}: {snap['classes']} classes, roster {len(keys)}, "
                      f"missing {snap['missing']}, carried {len(carried)}; dry run, not pushed", flush=True)
                continue

            def store(remote):
                check_snapshot(remote, snap, total=len(keys),
                               min_keep=0.0 if force else MIN_KEEP)
                return push(remote, snap, now=_now())
            status = _with_remote(remote_factory, store)
            print(f"{where}: {snap['classes']} classes, roster {len(keys)}, "
                  f"missing {snap['missing']}, carried {len(carried)}, {len(snap['payload']) // 1024} KB, {status}",
                  flush=True)
        except Exception as e:  # noqa: BLE001 - try every term, then fail loudly
            failed.append(where)
            print(f"error: {where}: {e}", file=sys.stderr, flush=True)
    if failed:
        print(f"error: failed terms: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


# --- pull -------------------------------------------------------------------

def pull(remote, local, *, now: str, max_age_hours: float = MAX_AGE_HOURS,
         targets: list[tuple[str, str]]) -> dict:
    """Copy every cloud row whose digest differs from the local one. A target
    term with no cloud row is `uncollected`, one checked too long ago is `stale`;
    both make the CLI exit 3."""
    now_dt = datetime.fromisoformat(now)
    have = {(r[0], r[1]): r[2] for r in local.execute(
        "SELECT year, term, digest FROM syllabus_attach_snapshots").fetchall()}
    rows = remote.execute(
        "SELECT year, term, digest, classes, missing, collected_at, checked_at, payload"
        " FROM syllabus_attach_snapshots ORDER BY year, term").fetchall()
    applied, stale = [], []
    uncollected = [t for t in targets if t not in {(r[0], r[1]) for r in rows}]
    for row in rows:
        year, term, digest, checked_at = row[0], row[1], row[2], row[6]
        if (year, term) in set(targets) and \
                now_dt - datetime.fromisoformat(checked_at) > timedelta(hours=max_age_hours):
            stale.append((year, term))
        if have.get((year, term)) == digest:
            continue
        local.execute(
            "INSERT OR REPLACE INTO syllabus_attach_snapshots"
            "(year, term, digest, classes, missing, collected_at, checked_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?)", tuple(row))
        applied.append((year, term))
    local.commit()
    return {"terms": len(rows), "applied": applied, "stale": stale,
            "uncollected": uncollected}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="runner: read syllabus attachments into the cloud")
    c.add_argument("--past", action="store_true",
                   help="every cloud term since 2020 that has no row yet (not the targets)")
    c.add_argument("--force", action="store_true",
                   help="bypass the 90%% shrink guard; with --past also re-collect terms "
                        "that already have a row")
    c.add_argument("--dry-run", action="store_true", help="fetch, push nothing")
    c.add_argument("--terms", default=None,
                   help="override the targets, e.g. '2026:fall 2026:winter'")
    p = sub.add_parser("pull", help="local: copy changed snapshots into the local table")
    p.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    p.add_argument("--dest", default="data/turso.db", help="local catalog file")
    sub.add_parser("init-remote", help="create syllabus_attach_snapshots in the cloud")
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        if args.past and args.terms is not None:
            ap.error("--past and --terms contradict: --past collects every stored past term")
        if args.terms is not None and not args.terms.strip():
            ap.error("--terms is empty; give YEAR:spring|summer|fall|winter entries")
        if args.force and args.dry_run and not args.past:
            ap.error("--force does nothing with --dry-run unless --past: a dry run "
                     "never reaches the shrink guard")
    if args.cmd == "pull" and args.max_age_hours <= 0:
        ap.error("--max-age-hours must be positive")

    if args.cmd == "init-remote":
        remote = _remote()
        try:
            init_remote(remote)
        finally:
            remote.close()
        print("syllabus_attach_snapshots ready")
        return 0
    if args.cmd == "pull":
        local = open_local(args.dest)   # a local file, never the env's cloud URL
        try:
            try:
                targets = target_terms(os.environ)
            except ValueError as e:   # config only; other errors must surface
                print(f"error: {e}", file=sys.stderr)
                return 2
            local.execute(db.SYLLABUS_ATTACH_DDL)
            local.commit()
            remote = _remote()
            try:
                out = pull(remote, local, now=_now(), max_age_hours=args.max_age_hours,
                           targets=targets)
            finally:
                remote.close()
        finally:
            local.close()
        print(f"syllabus snapshots: {out['terms']} term(s), applied "
              f"{[f'{y}/{t}' for y, t in out['applied']]}", flush=True)
        if out["uncollected"]:
            print(f"error: no syllabus snapshot collected yet: "
                  f"{[f'{y}/{t}' for y, t in out['uncollected']]} (collect-syllabus never ran "
                  f"for it?)", file=sys.stderr)
        if out["stale"]:
            print(f"error: syllabus snapshot not refreshed for over "
                  f"{args.max_age_hours:g} h: {[f'{y}/{t}' for y, t in out['stale']]} "
                  f"(collect-syllabus stopped?)", file=sys.stderr)
        if out["stale"] or out["uncollected"]:
            return 3
        return 4 if out["applied"] else 0

    try:
        env = dict(os.environ)
        if args.terms is not None:
            env["UPCOMING_TERMS"] = args.terms
        targets = target_terms(env)
        if args.terms is not None:
            targets = targets[1:]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    terms = targets
    if not args.past and not terms:
        print("error: no terms to collect (--terms only named the counted term?)",
              file=sys.stderr)
        return 2
    if args.past:
        remote = _remote()
        try:
            terms = past_terms(remote, target_terms(os.environ))
        finally:
            remote.close()
    return collect(terms, dry_run=args.dry_run, force=args.force,
                   skip_existing=args.past, remote_factory=_remote)


if __name__ == "__main__":
    raise SystemExit(main())
