"""Audit catalog rows whose stored cart count is NULL.

The catalog intentionally stores NULL outside a cart collection window, so the
default report is a storage audit. With ``--live`` the script also fetches the
complete SNU roster without writing to the database and reports which stored
NULLs currently have a non-negative live cart value, which are genuinely NULL
on the site, and which cannot be resolved safely.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl  # noqa: E402
import db  # noqa: E402
import process_lock  # noqa: E402


TERM_ALIASES = {
    "spring": "U000200001U000300001",
    "fall": "U000200002U000300001",
    "summer": "U000200001U000300002",
    "winter": "U000200002U000300002",
}


def normalize_term(value: str | None) -> str | None:
    if not value:
        return None
    return TERM_ALIASES.get(value.strip().lower(), value.strip())


def find_null_cart_rows(conn, *, year: str | None = None,
                        term: str | None = None) -> list[dict]:
    """Return catalog rows whose stored cart value is NULL."""
    where = ["cart IS NULL"]
    params: list[str] = []
    if year:
        where.append("year=?")
        params.append(year)
    if term:
        where.append("term=?")
        params.append(normalize_term(term) or term)
    rows = conn.execute(
        "SELECT id, year, term, sbjt_cd, lt_no, subh_cd, name, professor, "
        "department, cart FROM classes WHERE " + " AND ".join(where) + " "
        "ORDER BY year, term, name, sbjt_cd, lt_no, subh_cd",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def load_catalog_rows(conn, scopes: list[tuple[str, str]]) -> list[dict]:
    """Snapshot all catalog candidates for the terms being audited."""
    rows: list[dict] = []
    for year, term in scopes:
        found = conn.execute(
            "SELECT id, year, term, sbjt_cd, lt_no, subh_cd, name, professor, "
            "department, cart FROM classes WHERE year=? AND term=?",
            (year, term),
        ).fetchall()
        rows.extend(dict(row) for row in found)
    return rows


def _row_summary(row: dict, *, live: dict | None = None,
                 method: str | None = None) -> dict:
    result = {
        "year": row["year"],
        "term": row["term"],
        "sbjt_cd": row["sbjt_cd"],
        "lt_no": row["lt_no"],
        "subh_cd": row["subh_cd"],
        "name": row["name"],
    }
    if live is not None:
        result.update({
            "live_subh_cd": live.get("subh_cd", ""),
            "live_cart": live.get("cart"),
            "match": method,
        })
    return result


def summarize_null_rows(rows: list[dict]) -> dict:
    by_term = Counter((row["year"], row["term"]) for row in rows)
    by_subh = Counter(row["subh_cd"] for row in rows)
    return {
        "stored_null": len(rows),
        "stored_null_by_term": {
            f"{year}/{term}": count
            for (year, term), count in sorted(by_term.items())
        },
        "stored_null_by_subh_cd": dict(sorted(by_subh.items())),
    }


def analyze_live_records(catalog_rows: list[dict], null_rows: list[dict],
                         live_records: list[dict], *, limit: int = 20) -> dict:
    """Classify live observations for rows currently stored with cart=NULL."""
    candidates = defaultdict(list)
    for row in catalog_rows:
        candidates[(row["year"], row["term"], row["sbjt_cd"], row["lt_no"])].append(row)
    null_by_id = {row["id"]: row for row in null_rows}
    observed: set[int] = set()
    seen: set[tuple] = set()
    recoverable: list[dict] = []
    site_null: list[dict] = []
    ambiguous: list[dict] = []

    for live in live_records:
        term = (live.get("shtm_fg", "") or "") + (live.get("deta_shtm_fg", "") or "")
        key = (live.get("year", ""), term, live.get("sbjt_cd", ""), live.get("lt_no", ""))
        pool = candidates.get(key, [])
        if not pool:
            continue
        class_id, method = db.resolve_live_candidates(
            pool,
            live.get("subh_cd", ""),
            name=live.get("name"),
            professor=live.get("professor"),
            department=live.get("department"),
        )
        if class_id is None:
            for row in pool:
                if row["id"] not in null_by_id:
                    continue
                observation_key = (row["id"], live.get("subh_cd", ""),
                                   live.get("cart"), method)
                if observation_key in seen:
                    continue
                seen.add(observation_key)
                observed.add(row["id"])
                ambiguous.append(_row_summary(row, live=live, method=method))
            continue
        if class_id not in null_by_id:
            continue
        row = null_by_id[class_id]
        observation_key = (class_id, live.get("subh_cd", ""), live.get("cart"), method)
        if observation_key in seen:
            continue
        seen.add(observation_key)
        observed.add(class_id)
        if isinstance(live.get("cart"), int) and live["cart"] >= 0:
            recoverable.append(_row_summary(row, live=live, method=method))
        elif live.get("cart") is None:
            site_null.append(_row_summary(row, live=live, method=method))

    unseen = [row for row in null_rows if row["id"] not in observed]
    return {
        "live_records_checked": len(live_records),
        "stored_nulls_seen_live": len(observed),
        "live_nonnegative_for_stored_null": len(recoverable),
        "live_cart_null": len(site_null),
        "ambiguous": len(ambiguous),
        "not_seen_in_live": len(unseen),
        "recoverable_examples": recoverable[:limit],
        "site_null_examples": site_null[:limit],
        "ambiguous_examples": ambiguous[:limit],
        "not_seen_examples": [_row_summary(row) for row in unseen[:limit]],
    }


def _snapshot(year: str | None, term: str | None,
              lock_timeout: float) -> tuple[list[dict], list[dict]]:
    with process_lock.ProcessLock(timeout=lock_timeout):
        conn = db.connect()
        try:
            null_rows = find_null_cart_rows(conn, year=year, term=term)
            scopes = sorted({(row["year"], row["term"]) for row in null_rows})
            return null_rows, load_catalog_rows(conn, scopes)
        finally:
            conn.close()


def _report_text(report: dict) -> str:
    lines = [
        "Cart NULL audit",
        f"stored NULL: {report['stored_null']}",
    ]
    if report["stored_null_by_term"]:
        lines.append("by term: " + ", ".join(
            f"{term}={count}" for term, count in report["stored_null_by_term"].items()
        ))
    if report["stored_null_by_subh_cd"]:
        lines.append("by catalog subh_cd: " + ", ".join(
            f"{subh}={count}" for subh, count in report["stored_null_by_subh_cd"].items()
        ))
    live = report.get("live")
    if live:
        lines.extend([
            f"live records checked: {live['live_records_checked']}",
            "live non-negative for stored NULL: "
            f"{live['live_nonnegative_for_stored_null']}",
            f"live cart NULL: {live['live_cart_null']}",
            f"ambiguous: {live['ambiguous']}",
            f"not seen in live: {live['not_seen_in_live']}",
        ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", help="limit the audit to one catalog year")
    parser.add_argument("--term", help="term code or alias (spring/fall/summer/winter)")
    parser.add_argument("--live", action="store_true",
                        help="also fetch the live SNU roster; never writes the DB")
    parser.add_argument("--limit", type=int, default=20,
                        help="maximum examples per live category (default: 20)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="print the full report as JSON")
    parser.add_argument("--lock-timeout", type=float,
                        default=process_lock.DEFAULT_TIMEOUT,
                        help="seconds to wait for the shared read snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    term = normalize_term(args.term)
    try:
        null_rows, catalog_rows = _snapshot(args.year, term, args.lock_timeout)
    except process_lock.LockTimeout as exc:
        print(f"cart audit could not obtain the shared lock: {exc}", file=sys.stderr)
        return process_lock.LOCK_TIMEOUT_EXIT

    scopes = sorted({(row["year"], row["term"]) for row in null_rows})
    report = {"scope": [{"year": year, "term": term} for year, term in scopes]}
    report.update(summarize_null_rows(null_rows))
    if args.live and scopes:
        client = crawl.SnuClient()
        live_records: list[dict] = []
        for year, scope_term in scopes:
            live_records.extend(crawl.fetch_live_classes(client, year, scope_term))
        report["live"] = analyze_live_records(
            catalog_rows, null_rows, live_records, limit=args.limit
        )

    print(json.dumps(report, ensure_ascii=False, indent=2) if args.as_json
          else _report_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
