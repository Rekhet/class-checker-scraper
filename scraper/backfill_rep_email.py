"""One-time backfill: representative-professor email per class, from cc103ajax.

The syllabus-popup endpoint `POST /sugang/cc/cc103ajax.action` returns
`LISTTAB03.profEmail` (rep's school email) + `profEmail2` (rep's personal/alt
email) for the class's 대표교수 (representative professor). This is the ONLY
real, unique, stable professor identifier anywhere in the anonymous sugang data
(no persno/profId is ever populated). Co-professors have NO email.

We cache one row per class key into `class_rep_email` (in the same SQLite DB as
`classes`). The export step keys rep identity by a hash of this email instead of
uuid5(name|dept), which merges dept-renames/cross-dept teaching and splits
homonym reps. THE RAW EMAIL NEVER LEAVES THIS DB — it is a private identity
basis, hashed at export; it is never emitted to the public JSON.

Endpoint facts (verified 2026-07-01):
  - HTTP status is 201 on success (never 200).
  - Presence signal = LISTTAB03 is a dict with non-empty profNm; top-level
    ROWCNT is unreliable (0 even for valid multi-prof classes).
  - Email is rep-only: a 22-professor class returns exactly one email.

Resumable + idempotent: rows already cached are skipped. Re-run with
`--retry-failed` to re-attempt rows that previously errored (ok=0).

Usage:
    ./.venv/bin/python scraper/backfill_rep_email.py [--rps 10] [--retry-failed] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from contextlib import nullcontext
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session as snu_session  # noqa: E402
import process_lock  # noqa: E402

DB_PATH = Path("data/turso.db")
CC103 = f"{snu_session.BASE}/sugang/cc/cc103ajax.action"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS class_rep_email (
            year TEXT, shtm_fg TEXT, deta_shtm_fg TEXT,
            sbjt_cd TEXT, lt_no TEXT, subh_cd TEXT,
            rep_email TEXT, rep_email2 TEXT, prof_nm TEXT,
            list03_rowcnt TEXT, http_status INTEGER, ok INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd)
        )""")
    conn.commit()


def worklist(conn: sqlite3.Connection, retry_failed: bool, limit: int | None) -> list[tuple]:
    """Distinct class keys not yet successfully cached. Order is stable
    (deterministic) so a killed run resumes cleanly."""
    join_cond = "e.ok IS NULL" if not retry_failed else "(e.ok IS NULL OR e.ok=0)"
    sql = f"""
        SELECT DISTINCT c.year, c.shtm_fg, c.deta_shtm_fg, c.sbjt_cd, c.lt_no, c.subh_cd
        FROM classes c
        LEFT JOIN class_rep_email e
          ON e.year=c.year AND e.shtm_fg=c.shtm_fg AND e.deta_shtm_fg=c.deta_shtm_fg
         AND e.sbjt_cd=c.sbjt_cd AND e.lt_no=c.lt_no AND e.subh_cd=c.subh_cd
        WHERE {join_cond}
        ORDER BY c.year, c.shtm_fg, c.sbjt_cd, c.lt_no
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def new_http(sess: dict) -> requests.Session:
    http = requests.Session()
    http.cookies.update(sess["cookies"])
    http.headers.update({
        "User-Agent": sess["ua"],
        "Referer": snu_session.SEARCH_URL,
        "Origin": snu_session.BASE,
        "X-Requested-With": "XMLHttpRequest",
    })
    return http


def fetch_one(http: requests.Session, key: tuple) -> dict | None:
    """POST cc103ajax for one class key. Returns a parsed result dict, or None
    when the response looks like an expired/invalid session (caller re-mints)."""
    year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd = key
    params = {
        "openSchyy": year, "openShtmFg": shtm_fg, "openDetaShtmFg": deta_shtm_fg,
        "sbjtCd": sbjt_cd, "ltNo": lt_no, "sbjtSubhCd": subh_cd or "000",
    }
    try:
        r = http.post(CC103, data=params, timeout=30)
    except requests.RequestException:
        return {"ok": 0, "http_status": -1, "rep_email": None, "rep_email2": None,
                "prof_nm": None, "list03_rowcnt": None}
    if r.status_code not in (200, 201):
        # 302/redirect or 5xx -> likely session gone; signal re-mint.
        return None
    try:
        j = r.json()
    except ValueError:
        return None  # HTML (login/entry splash) -> session gone
    t3 = j.get("LISTTAB03")
    if not isinstance(t3, dict):
        # valid JSON but no syllabus tab: a real "no data" answer, cache it.
        return {"ok": 1, "http_status": r.status_code, "rep_email": None,
                "rep_email2": None, "prof_nm": None,
                "list03_rowcnt": str(j.get("LISTTAB03_ROWCNT"))}
    return {
        "ok": 1, "http_status": r.status_code,
        "rep_email": (t3.get("profEmail") or "").strip() or None,
        "rep_email2": (t3.get("profEmail2") or "").strip() or None,
        "prof_nm": (t3.get("profNm") or "").strip() or None,
        "list03_rowcnt": str(j.get("LISTTAB03_ROWCNT")),
    }


def upsert(conn: sqlite3.Connection, key: tuple, res: dict, now: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO class_rep_email
           (year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd,
            rep_email, rep_email2, prof_nm, list03_rowcnt, http_status, ok, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (*key, res["rep_email"], res["rep_email2"], res["prof_nm"],
         res["list03_rowcnt"], res["http_status"], res["ok"], now))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=float, default=10.0, help="requests per second (gentle default)")
    ap.add_argument("--retry-failed", action="store_true", help="re-attempt rows cached with ok=0")
    ap.add_argument("--limit", type=int, default=None, help="cap work items (for a smoke test)")
    args = ap.parse_args()

    min_interval = 1.0 / args.rps if args.rps > 0 else 0.0
    conn = sqlite3.connect(str(DB_PATH))
    ensure_table(conn)
    work = worklist(conn, args.retry_failed, args.limit)
    total = len(work)
    print(f"START backfill total={total} rps={args.rps} retry_failed={args.retry_failed}",
          flush=True)
    if not total:
        print("DONE nothing-to-do", flush=True)
        return 0

    sess = snu_session.mint_session(headless=True)
    http = new_http(sess)
    print("SESSION minted", flush=True)

    done = ok = fail = with_email = remints = 0
    consecutive_bad = 0
    t0 = time.time()
    try:
        for key in work:
            t_req = time.time()
            res = fetch_one(http, key)
            if res is None:  # session expired -> re-mint once, retry this key
                remints += 1
                print(f"REMINT #{remints} (session expired) at done={done}", flush=True)
                sess = snu_session.mint_session(headless=True)
                http = new_http(sess)
                res = fetch_one(http, key)
                if res is None:
                    res = {"ok": 0, "http_status": -2, "rep_email": None,
                           "rep_email2": None, "prof_nm": None, "list03_rowcnt": None}
            upsert(conn, key, res, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            done += 1
            if res["ok"]:
                ok += 1
                consecutive_bad = 0
                if res["rep_email"]:
                    with_email += 1
            else:
                fail += 1
                consecutive_bad += 1
            if done % 200 == 0:
                conn.commit()
            if done % 1000 == 0:
                rate = done / max(time.time() - t0, 1e-9)
                eta_min = (total - done) / max(rate, 1e-9) / 60.0
                print(f"PROGRESS done={done}/{total} ok={ok} email={with_email} "
                      f"fail={fail} remints={remints} rate={rate:.1f}/s eta={eta_min:.0f}min",
                      flush=True)
            if consecutive_bad and consecutive_bad % 25 == 0:
                print(f"WARN {consecutive_bad} consecutive failures near done={done} "
                      f"(last status={res['http_status']})", flush=True)
            # rate limit
            elapsed = time.time() - t_req
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
    except KeyboardInterrupt:
        print(f"INTERRUPTED at done={done}", flush=True)
    finally:
        conn.commit()
    print(f"DONE done={done}/{total} ok={ok} email={with_email} fail={fail} "
          f"remints={remints} elapsed={ (time.time()-t0)/60:.1f}min", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    try:
        with lock:
            raise SystemExit(main())
    except process_lock.LockTimeout as exc:
        print(f"process lock busy: {exc}", file=sys.stderr)
        raise SystemExit(process_lock.LOCK_TIMEOUT_EXIT)
