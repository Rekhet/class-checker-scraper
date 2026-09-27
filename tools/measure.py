#!/usr/bin/env python3
"""Measurement harness: the real-data checks behind the optimization pass,
kept runnable so a change can be measured the same way again.

Every command is READ-ONLY toward the cloud database and the deployed site.
Commands that read the cloud need its credentials in the environment:

    set -a; . ./turso-remote.env; set +a

Web commands serve web/ locally unless --site URL is given (the deployed site,
e.g. https://rekhet.github.io/class-checker/).

    python -m tools.measure site      [--site URL]   smoke: trend loads, KST, paging, errors
    python -m tools.measure mobile    [--site URL]   phone viewport: overflow, axis size, tap
    python -m tools.measure feed      [--site URL] [--sequential]   cold change-feed timings
    python -m tools.measure revisit   [--site URL]   bytes of app.js/partials, first vs repeat
    python -m tools.measure search    [--ref HEAD]   searchLocal timings, working tree vs a git ref
    python -m tools.measure trend-lossless            v2 payload == dense replay, every window
    python -m tools.measure trend-checkpoint          checkpoint vs full replay: time + equality
    python -m tools.measure export-profile            where a full export spends its time
    python -m tools.measure cloud                     cloud row counts, duplicate keys, plans, drift
    python -m tools.measure latest-compare            cloud vs local count_latest (current term)
    python -m tools.measure reseed-check              reseed_roster into a scratch copy of the cloud roster
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))

TERMS = {"spring": "U000200001U000300001", "summer": "U000200001U000300002",
         "fall": "U000200002U000300001", "winter": "U000200002U000300002"}


def _term() -> tuple[str, str]:
    year = os.environ.get("COUNT_YEAR", "2026")
    return year, TERMS[os.environ.get("COUNT_SEM", "fall")]


def _local_db():
    os.environ.setdefault("DB_BACKEND", "turso")
    os.environ.setdefault("TURSO_DATABASE_URL", str(ROOT / "data" / "turso.db"))
    import db
    return db.connect()


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def _site(url: str | None):
    """Yield the base URL (ending in index.html) of the site under test."""
    if url:
        yield url.rstrip("/") + "/index.html"
        return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(WEB)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/index.html"
    finally:
        srv.shutdown()
        srv.server_close()


def _trend_link(year: str, term: str, key: str) -> str:
    from urllib.parse import quote
    return f"#trend/{quote(year + '|' + term, safe='')}/{quote(key, safe='')}"


def _a_live_class() -> str:
    y, t = _term()
    live = json.loads((WEB / "data" / "trend" / f"trend_{y}_{t}.json").read_text())
    return next(iter(live["series"]))


# ---------------------------------------------------------------- web ----
def cmd_site(args) -> dict:
    from playwright.sync_api import sync_playwright
    y, t = _term()
    key = _a_live_class()
    errors, out = [], {}
    with _site(args.site) as base, sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(timezone_id="America/Los_Angeles").new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + _trend_link(y, t, key), wait_until="domcontentloaded")
        pg.wait_for_function("k => typeof _trend !== 'undefined' && _trend.key === k", arg=key,
                             timeout=30000)
        out["window"] = pg.text_content("#trendWinLabel")
        pg.locator("#trendChart svg rect").hover(position={"x": 100, "y": 60})
        pg.wait_for_selector("#trendTip:not(.hidden)")
        out["tooltip"] = pg.text_content("#trendTip .tip-t")
        pg.click("#trendPrev")
        pg.wait_for_function("() => document.querySelector('#trendWinLabel').textContent.startsWith('구간')",
                             timeout=30000)
        out["previous window"] = pg.text_content("#trendWinLabel")
        out["feed"] = pg.text_content("#trendFeedMeta")
        b.close()
    out["page errors"] = errors
    return out


def cmd_mobile(args) -> dict:
    from playwright.sync_api import sync_playwright
    y, t = _term()
    key = _a_live_class()
    out, errors = {}, []
    with _site(args.site) as base, sync_playwright() as p:
        b = p.chromium.launch()
        for scheme in ("light", "dark"):
            ctx = b.new_context(**p.devices["iPhone 13"], color_scheme=scheme)
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            for route in ("", "#explore", "#grad", "#trend"):
                pg.goto(base + route, wait_until="networkidle")
                out[f"{scheme} {route or '#timetable'} overflow px"] = pg.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
            pg.goto(base + _trend_link(y, t, key), wait_until="networkidle")
            pg.wait_for_selector("#trendChart svg text", state="attached")
            out[f"{scheme} axis label px"] = round(pg.evaluate(
                "() => document.querySelector('#trendChart svg text').getBoundingClientRect().height"), 1)
            pg.locator("#trendChart svg rect").tap(position={"x": 100, "y": 60})
            pg.wait_for_selector("#trendTip:not(.hidden)")
            out[f"{scheme} tap tooltip"] = True
            ctx.close()
        b.close()
    out["page errors"] = errors
    return out


def cmd_feed(args) -> dict:
    from playwright.sync_api import sync_playwright
    periods = {"3일": 72, "7일": 168, "14일": 336, "학기 전체": 0}
    times = {k: [] for k in periods}
    chunks = {}
    with _site(args.site) as base, sync_playwright() as p:
        b = p.chromium.launch()
        for _ in range(args.rounds):
            for label, h in periods.items():
                ctx = b.new_context()          # empty cache every time
                pg = ctx.new_page()
                pg.goto(base + "#trend", wait_until="domcontentloaded")
                pg.wait_for_function("() => typeof _trend !== 'undefined' && !!_trend.live",
                                     timeout=30000)
                r = pg.evaluate("""async ([h, seq]) => {
                  if (seq) _trend.archiveStarts = null;
                  const t0 = performance.now(); const d = await feedData(h);
                  const t1 = performance.now(); trendFeed(d, h, "all");
                  return { load: t1 - t0, feed: performance.now() - t1, chunks: _feedWindows.size };
                }""", [h, args.sequential])
                times[label].append(r["load"])
                chunks[label] = r["chunks"]
                ctx.close()
        b.close()
    return {label: {"chunks": chunks[label], "median ms": round(statistics.median(v)),
                    "runs ms": [round(x) for x in v]} for label, v in times.items()}


def cmd_revisit(args) -> dict:
    from playwright.sync_api import sync_playwright
    sizes = {}
    with _site(args.site) as base, sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        pg = ctx.new_page()
        cdp = ctx.new_cdp_session(pg)
        cdp.send("Network.enable")
        urls = {}
        visit = {"name": "first"}
        cdp.on("Network.responseReceived",
               lambda ev: urls.__setitem__(ev["requestId"], ev["response"]["url"]))

        def done(ev):
            name = urls.get(ev["requestId"], "").split("?")[0].rsplit("/", 1)[-1]
            if name.endswith((".js", ".html", ".css")):
                sizes.setdefault(visit["name"], {})[name] = ev["encodedDataLength"]
        cdp.on("Network.loadingFinished", done)
        for v in ("first", "repeat"):
            visit["name"] = v
            pg.goto(base, wait_until="networkidle")
            pg.wait_for_function("() => typeof searchLocal === 'function'")
            time.sleep(0.5)
        b.close()
    return sizes


def cmd_search(args) -> dict:
    from playwright.sync_api import sync_playwright
    old = subprocess.run(["git", "-C", str(WEB), "show", f"{args.ref}:app.js"],
                         capture_output=True, text=True, check=True).stdout
    old_loader = subprocess.run(["git", "-C", str(WEB), "show", f"{args.ref}:loader.js"],
                                capture_output=True, text=True, check=True).stdout
    js = """async () => {
      const f = { name: "", department: "", seatsOnly: true, year: "", term: "" };
      await searchLocal(f, { limit: 100, offset: 0 });            // warm: fetch + parse
      const g = { ...f };
      const t0 = performance.now(); const first = await searchLocal(g, { limit: 100, offset: 0 });
      const t1 = performance.now();
      for (let i = 1; i <= 5; i++) await searchLocal(g, { limit: 100, offset: i * 100 });
      const t2 = performance.now();
      const h = { name: "통계", department: "", year: "", term: "" };
      const t3 = performance.now();
      for (let i = 0; i <= 5; i++) await searchLocal(h, { limit: 100, offset: i * 100 });
      return { rows: first.total, first_ms: t1 - t0, five_more_ms: t2 - t1,
               name_six_pages_ms: performance.now() - t3 }; }"""
    out = {}
    with _site(None) as base, sync_playwright() as p:
        b = p.chromium.launch()
        for label, patched in ((args.ref, True), ("working tree", False)):
            pg = b.new_page()
            if patched:
                pg.route("**/app.js*", lambda r: r.fulfill(body=old, content_type="text/javascript"))
                pg.route("**/loader.js*", lambda r: r.fulfill(body=old_loader,
                                                                content_type="text/javascript"))
            pg.goto(base, wait_until="domcontentloaded")
            pg.wait_for_function("() => typeof searchLocal === 'function'", timeout=30000)
            out[label] = {k: round(v, 1) if isinstance(v, float) else v
                          for k, v in pg.evaluate(js).items()}
            pg.close()
        b.close()
    return out


# ------------------------------------------------------------ export -----
def cmd_trend_lossless(args) -> dict:
    import export_json as ej
    conn = _local_db()
    y, term = _term()
    t = {"year": y, "term": term}
    axis = ej._load_axis(conn, t)
    w = ej.TREND_WINDOW
    wins = {f"w{i:03d}": (i * w, (i + 1) * w) for i in range(len(axis) // w)}
    wins["live"] = ej.live_bounds(len(axis))
    series = ej._walk_samples(conn, t, axis, wins)
    series.pop("__state__", None)
    bad = 0
    for name, bounds in wins.items():
        dec = ej.decode_trend(ej._payload(axis, bounds, series[name]))
        if dec["ts"] != [row[0] for row in axis[bounds[0]:bounds[1]]]:
            bad += 1
        bad += sum(dec["series"][k][m] != arrs[m]
                   for k, arrs in series[name].items() for m in "acqe")
    return {"windows": len(wins), "mismatches": bad}


def cmd_trend_checkpoint(args) -> dict:
    from unittest.mock import patch
    import export_json as ej
    ej.TREND_CACHE = Path(tempfile.mkdtemp()) / "cache"
    conn = _local_db()
    y, term = _term()
    t = {"year": y, "term": term}
    a = time.perf_counter()
    with patch.object(ej, "_state_before", lambda *x, **k: (None, None)):
        full = ej.export_trend(conn, t)
    b = time.perf_counter()
    first = ej.export_trend(conn, t)
    c = time.perf_counter()
    second = ej.export_trend(conn, t)
    d = time.perf_counter()
    return {"full replay s": round(b - a, 1), "build checkpoint s": round(c - b, 1),
            "from checkpoint s": round(d - c, 2), "identical": full == first == second}


def cmd_export_profile(args) -> dict:
    import db
    import export_json as ej
    out_dir = Path(tempfile.mkdtemp())
    conn = _local_db()
    terms = db.list_terms(conn)
    r = {}
    a = time.perf_counter()
    for t in terms:
        ej._write(out_dir / f"{t['year']}_{t['term']}.json",
                  db.search(conn, year=t["year"], term=t["term"], limit=None))
    r["all class files s"] = round(time.perf_counter() - a, 1)
    y, term = _term()
    a = time.perf_counter()
    tr = ej.export_trend(conn, {"year": y, "term": term})
    r["current trend s"] = round(time.perf_counter() - a, 1)
    r["current trend passes"] = len(tr["t"]) if tr else 0
    a = time.perf_counter()
    ej._export_explore(conn, terms, lambda obj: ej._write(out_dir / "explore.json", obj))
    r["explore index s"] = round(time.perf_counter() - a, 1)
    return r


# ------------------------------------------------------------- cloud -----
def _remote():
    from scraper.cloud_collect import _remote_connect
    return _remote_connect()


def cmd_cloud(args) -> dict:
    r = _remote()
    q = lambda sql, p=(): [tuple(x) for x in r.execute(sql, p).fetchall()]
    y, term = _term()
    out = {t: q(f"SELECT COUNT(*) FROM {t}")[0][0]
           for t in ("classes", "count_samples", "count_passes", "count_latest")}
    out["duplicate sample keys"] = q(
        "SELECT COUNT(*) FROM (SELECT 1 FROM count_samples "
        "GROUP BY year, term, sbjt_cd, lt_no, ts HAVING COUNT(*) > 1)")[0][0]
    out["newest pass"] = q("SELECT MAX(ts) FROM count_passes WHERE year=? AND term=?", (y, term))[0][0]
    out["plan: roster read"] = q("EXPLAIN QUERY PLAN SELECT * FROM classes WHERE year=? AND term=?",
                                 (y, term))[0][-1]
    out["indexes"] = sorted(n for (n,) in q("SELECT name FROM sqlite_master WHERE type='index' "
                                            "AND name NOT LIKE 'sqlite_%'"))
    try:
        out["roster drift"] = q("SELECT signature, added, removed, passes, last_seen "
                                "FROM roster_drift ORDER BY last_seen DESC LIMIT 3")
    except Exception as exc:  # noqa: BLE001
        out["roster drift"] = str(exc)
    r.close()
    return out


def cmd_latest_compare(args) -> dict:
    y, term = _term()
    sql = ("SELECT sbjt_cd, lt_no, applied, cart, enrolled, quota, cancel_vacancy "
           "FROM count_latest WHERE year=? AND term=?")
    r = _remote()
    cloud = {tuple(x[:2]): tuple(x[2:]) for x in r.execute(sql, (y, term)).fetchall()}
    r.close()
    local = {tuple(x[:2]): tuple(x[2:]) for x in _local_db().execute(sql, (y, term)).fetchall()}
    return {"cloud": len(cloud), "local": len(local),
            "value mismatches": sum(1 for k in cloud if local.get(k) != cloud[k]),
            "only cloud": len(set(cloud) - set(local)), "only local": len(set(local) - set(cloud))}


def cmd_reseed_check(args) -> dict:
    import sqlite3
    import libsql
    from scraper import db
    from scraper.cloud_collect import _copy_query
    from scraper.reseed_roster import reseed
    y, term = _term()
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    db.init_schema(db._Conn(scratch, "sqlite"))
    r = _remote()
    n = _copy_query(r, scratch, "classes", "year=? AND term=?", (y, term))
    r.close()
    local = libsql.connect(str(ROOT / "data" / "turso.db"))
    a = time.perf_counter()
    out = reseed(local, scratch, year=y, term=term)
    cols = "sbjt_cd, lt_no, subh_cd, name, professor, credits, quota"
    same = (set(map(tuple, scratch.execute(f"SELECT {cols} FROM classes WHERE year=? AND term=?",
                                           (y, term)).fetchall()))
            == set(map(tuple, local.execute(f"SELECT {cols} FROM classes WHERE year=? AND term=?",
                                            (y, term)).fetchall())))
    return {"cloud roster copied": n, **out, "seconds": round(time.perf_counter() - a, 2),
            "identical to local": same}


COMMANDS = {
    "site": cmd_site, "mobile": cmd_mobile, "feed": cmd_feed, "revisit": cmd_revisit,
    "search": cmd_search, "trend-lossless": cmd_trend_lossless,
    "trend-checkpoint": cmd_trend_checkpoint, "export-profile": cmd_export_profile,
    "cloud": cmd_cloud, "latest-compare": cmd_latest_compare, "reseed-check": cmd_reseed_check,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.measure", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sp = sub.add_parser(name)
        if name in ("site", "mobile", "feed", "revisit"):
            sp.add_argument("--site", help="deployed site URL (default: serve web/ locally)")
        if name == "feed":
            sp.add_argument("--sequential", action="store_true",
                            help="ignore trendArchiveStarts (the old one-by-one walk)")
            sp.add_argument("--rounds", type=int, default=3)
        if name == "search":
            sp.add_argument("--ref", default="HEAD", help="web/ git ref to compare against")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = COMMANDS[args.command](args)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
