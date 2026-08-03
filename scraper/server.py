"""Stdlib HTTP server: JSON API + static frontend for the class checker.

Endpoints
  GET  /                      -> web/index.html
  GET  /static/<f>            -> web asset
  GET  /api/terms            -> stored terms
  GET  /api/search?...       -> classes (name/professor/department/day/period/term/
                                grading=평가방식/switchable=전환가능만)
  GET  /api/status           -> counts + last crawl run + live refresh progress
  POST /api/refresh          -> admin: wipe & rebuild DB (background thread)

The temporary timetable lives in the browser (localStorage); the server is
stateless about it. Refresh is admin-gated by ADMIN_TOKEN when that env var
is set (sent as X-Admin-Token); left unset for local single-user use.
"""
from __future__ import annotations

import errno
import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import db
import crawl
import export
import process_lock

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
# Which shell to serve at "/". Set WEB_INDEX=prod.html to preview the production
# page (no dev/admin panels) locally.
INDEX_FILE = os.environ.get("WEB_INDEX", "index.html")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# Static preview of the production page (GitHub Pages parity): serve index.html +
# web/data/*.json only, with NO database. The prod page never calls /api, so the
# DB is skipped entirely and /api returns 404 just like the static host does.
SERVE_STATIC = os.environ.get("SERVE_STATIC", "").strip().lower() not in ("", "0", "false", "no")
HOST = os.environ.get("HOST", "127.0.0.1")
# Preferred port; if taken, scan upward to the next free one (Vite-style).
PORT = int(os.environ.get("PORT", "8000"))
PORT_SCAN_LIMIT = int(os.environ.get("PORT_SCAN_LIMIT", "50"))
CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8"}

# Curated working-tree JSON override maps: hand-authored human confirm/reject
# decisions (professor merges/splits, code prev/next links), git-tracked and
# reapplied deterministically by export_json on each rebuild — never the DB.
# CURATED_MAPS pins each map's fixed top-level list buckets; CURATED_ENDPOINTS
# routes one admin-gated POST per (map, bucket). To add a third map, add its
# filename+buckets here, its endpoints below, and a _load_two_bucket() call in
# export_json — the single _curated_write() handler needs no change.
CURATED_MAPS = {
    "prof_identity.json": ("merges", "splits"),
    "code_links.json": ("links", "suppress"),
}
CURATED_ENDPOINTS = {
    "/api/prof-merge": ("prof_identity.json", "merges"),
    "/api/prof-split": ("prof_identity.json", "splits"),
    "/api/code-link": ("code_links.json", "links"),
    "/api/code-suppress": ("code_links.json", "suppress"),
}

# LOG_LEVEL env (DEBUG/INFO/WARNING/ERROR) controls verbosity; default INFO.
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("class-checker")

# Default (year, term) scope for a manually triggered counts pass. Automatic
# scheduling is intentionally NOT done here — a cron job will call the refresh
# endpoints (or crawl.py) externally; see /api/refresh and /api/refresh-counts.
CRAWL_YEARS = [y for y in os.environ.get("CRAWL_YEARS", "2026").split(",") if y]
CRAWL_TERMS = [t for t in os.environ.get("CRAWL_TERMS", "").split(",") if t]

# A single lock serialises every DB-writing crawl op: a counts overlay must never
# run while a full Excel rebuild is wiping+repopulating its term.
_crawl_lock = threading.Lock()
_refresh_lock = threading.Lock()
REFRESH = {"running": False, "progress": {}, "result": None, "error": None}
COUNTS = {"running": False, "progress": {}, "result": None, "error": None}
# Cap concurrent full-catalog exports — each builds the whole file (CPU/memory),
# so unbounded parallel requests are a cheap DoS. Excess requests get 429.
EXPORT_MAX = int(os.environ.get("EXPORT_MAX_CONCURRENT", "2"))
_export_sem = threading.BoundedSemaphore(EXPORT_MAX)


def _run_refresh(years, terms):
    with _crawl_lock, process_lock.ProcessLock():
        conn = db.connect()
        db.init_schema(conn)

        def progress(p):
            REFRESH["progress"] = p

        try:
            REFRESH.update(running=True, error=None, result=None, progress={})
            log.info("refresh start years=%s terms=%s", years, terms or "all")
            out = crawl.refresh_all(conn, years, terms=terms or None, progress=progress)
            REFRESH["result"] = out
            log.info("refresh done: %s", out)
        except Exception as e:  # noqa: BLE001 - surfaced to the admin UI
            REFRESH["error"] = str(e)
            log.exception("refresh failed: %s", e)
        finally:
            REFRESH["running"] = False
            conn.close()


def _run_counts(years, terms, force=False):
    with _crawl_lock, process_lock.ProcessLock():
        conn = db.connect()
        db.init_schema(conn)

        def progress(p):
            COUNTS["progress"] = p

        try:
            COUNTS.update(running=True, error=None, result=None, progress={})
            log.info("counts start years=%s terms=%s force=%s", years, terms or "all", force)
            out = crawl.refresh_counts_all(conn, years, terms=terms or None,
                                           force=force, progress=progress)
            COUNTS["result"] = out
            log.info("counts done: %s", out)
        except Exception as e:  # noqa: BLE001 - best-effort; Excel data still stands
            COUNTS["error"] = str(e)
            log.exception("counts failed: %s", e)
        finally:
            COUNTS["running"] = False
            conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassChecker/1.0"

    # ---- helpers ----
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(301)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, path: Path):
        rp = path.resolve()
        allowed = any(base in rp.parents for base in (WEB_DIR, DOCS_DIR))
        if not path.is_file() or not allowed:
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")  # always revalidate; avoid stale JS/CSS
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # route stdlib access log through logging
        log.info("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt, *args):
        log.warning("%s %s", self.address_string(), fmt % args)

    # ---- routing ----
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if SERVE_STATIC and u.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        if u.path == "/" or u.path == "/index.html":
            return self._file(WEB_DIR / INDEX_FILE)
        if u.path.startswith("/static/"):
            return self._file(WEB_DIR / u.path[len("/static/"):])
        if u.path == "/docs":          # need the trailing slash so relative
            return self._redirect("/docs/")  # asset/nav links resolve under /docs/
        if u.path == "/docs/":
            return self._file(DOCS_DIR / "index.html")
        if u.path.startswith("/docs/"):
            return self._file(DOCS_DIR / u.path[len("/docs/"):])
        if u.path == "/api/terms":
            conn = db.connect()
            return self._json({"terms": db.list_terms(conn)})
        if u.path == "/api/departments":
            conn = db.connect()
            return self._json({"departments": db.list_departments(conn)})
        if u.path == "/api/classifications":
            conn = db.connect()
            return self._json({"classifications": db.list_classifications(conn)})
        if u.path == "/api/grades":
            conn = db.connect()
            return self._json({"grades": db.list_grades(conn)})
        if u.path == "/api/gradings":
            conn = db.connect()
            return self._json({"gradings": db.list_gradings(conn)})
        if u.path == "/api/timestats":
            conn = db.connect()
            return self._json({"stats": db.time_stats(conn)})
        if u.path == "/api/search":
            return self._search(q)
        if u.path == "/api/export.csv":
            return self._export(q, "csv")
        if u.path == "/api/export.xlsx":
            return self._export(q, "xlsx")
        if u.path == "/api/status":
            return self._status()
        # any other GET -> a web asset, so the frontend can use relative paths
        # (the same paths then work unchanged on a static host like GitHub Pages)
        if not u.path.startswith("/api/"):
            return self._file(WEB_DIR / u.path.lstrip("/"))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if SERVE_STATIC and u.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        if u.path == "/api/refresh":
            return self._refresh()
        if u.path == "/api/refresh-counts":
            return self._refresh_counts()
        if u.path == "/api/lookup":
            return self._lookup()
        spec = CURATED_ENDPOINTS.get(u.path)
        if spec:
            return self._curated_write(*spec)
        return self._json({"error": "not found"}, 404)

    # ---- handlers ----
    def _filters(self, q):
        """Pull the shared search filter kwargs out of the query string."""
        def one(k):
            return q.get(k, [""])[0].strip() or ""
        def num(k):
            v = q.get(k, [""])[0].strip()
            return int(v) if v.lstrip("-").isdigit() else None
        def many(k):
            return [v.strip() for v in q.get(k, []) if v.strip()] or None
        return {
            "term": one("term") or None, "year": one("year") or None,
            "name": one("name"), "professor": one("professor"),
            "department": one("department"),
            "classifications": many("classification"), "levels": many("level"),
            "grades": many("grade"),
            "gradings": many("grading"),                       # 평가방식 (A~F / S/U / S+/S/U)
            "switchable": one("switchable") or None,           # 평가방식 전환가능만 (any value = on)
            "day_index": num("day"), "period": num("period"),
        }

    def _page_num(self, q, k):
        v = q.get(k, [""])[0].strip()
        return int(v) if v.isdigit() else None

    def _search(self, q):
        f = self._filters(q)
        page = max(1, min(self._page_num(q, "limit") or 100, 500))
        offset = max(0, self._page_num(q, "offset") or 0)
        conn = db.connect()
        rows = db.search(conn, limit=page, offset=offset, **f)
        total = db.search_count(conn, **f)
        self._json({"count": len(rows), "total": total, "offset": offset,
                    "classes": rows})

    def _export(self, q, fmt):
        """Stream the FULL filtered result set (no 500 cap) as CSV or XLSX."""
        if not _export_sem.acquire(blocking=False):   # too many concurrent exports
            return self._json({"error": "export busy, retry shortly"}, 429)
        conn = db.connect()
        try:
            rows = db.iter_classes(conn, **self._filters(q))   # paged/chunked stream
            if fmt == "csv":
                data = export.to_csv(rows)
                ctype, fn = "text/csv; charset=utf-8", "classes.csv"
            else:
                data = export.to_xlsx(rows)
                ctype = ("application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet")
                fn = "classes.xlsx"
        except ImportError:   # openpyxl missing for xlsx
            return self._json({"error": "xlsx export needs openpyxl"}, 501)
        except Exception as e:  # noqa: BLE001 - return a clean 500, don't reset the socket
            log.exception("export failed")
            return self._json({"error": f"export failed: {e}"}, 500)
        finally:
            _export_sem.release()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        """Parse the request body as JSON; missing/empty/malformed -> {}. For the
        lenient read endpoints — the strict write path (_curated_write) inlines
        its own parse so it can reject bad input with a 400 instead of ignoring it."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError:
            return {}

    def _lookup(self):
        payload = self._read_json_body()
        keys = [(str(k[0]), str(k[1]), str(k[2]), str(k[3]))
                for k in payload.get("keys", [])
                if isinstance(k, (list, tuple)) and len(k) == 4]
        conn = db.connect()
        self._json({"classes": db.lookup(conn, keys)})

    def _curated_write(self, filename, bucket):
        """Append one human-override rule to a curated working-tree JSON map
        (scraper/<filename>). Admin-gated; writes ONLY the curated file, never
        the DB. The map's buckets are fixed in CURATED_MAPS; the file is created
        with every bucket empty if absent, and self-heals to that shape if it is
        corrupt or wrong-typed. Response: {"ok": true, "count": {<bucket>: N}}.

        This is the single writer behind every curated-map endpoint. To add a new
        map: register its filename+buckets in CURATED_MAPS and its endpoints in
        CURATED_ENDPOINTS — no new handler code."""
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        buckets = CURATED_MAPS.get(filename)
        if buckets is None or bucket not in buckets:
            return self._json({"error": "server misconfigured"}, 500)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            rule = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if not isinstance(rule, dict):
            return self._json({"error": "rule must be an object"}, 400)
        path = Path(__file__).with_name(filename)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        for b in buckets:
            if not isinstance(data.get(b), list):
                data[b] = []
        data[bucket].append(rule)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._json({"ok": True, "count": {b: len(data[b]) for b in buckets}})

    def _status(self):
        conn = db.connect()
        counts = {
            "classes": conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0],
            "slots": conn.execute("SELECT COUNT(*) FROM class_slots").fetchone()[0],
            "terms": conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0],
        }
        self._json({"counts": counts, "last_run": db.latest_run(conn),
                    "backend": conn.backend, "refresh": REFRESH,
                    "counts_refresh": COUNTS})

    def _authed(self) -> bool:
        """True if no admin token is configured (local single-user) or the request
        carries the right X-Admin-Token. Constant-time compare avoids token-timing
        leaks."""
        if not ADMIN_TOKEN:
            return True
        return hmac.compare_digest(self.headers.get("X-Admin-Token", ""), ADMIN_TOKEN)

    def _refresh(self):
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        with _refresh_lock:
            if REFRESH["running"]:
                return self._json({"error": "refresh already running",
                                   "progress": REFRESH["progress"]}, 409)
            payload = self._read_json_body()
            years = payload.get("years") or ["2026"]
            terms = payload.get("terms") or []
            REFRESH["running"] = True  # set before releasing lock
        threading.Thread(target=_run_refresh, args=(years, terms),
                         daemon=True).start()
        self._json({"started": True, "years": years, "terms": terms})

    def _refresh_counts(self):
        """Manual trigger for a counts-only overlay (no Excel download)."""
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        with _refresh_lock:
            if COUNTS["running"] or REFRESH["running"]:
                return self._json({"error": "a crawl is already running",
                                   "progress": COUNTS["progress"]}, 409)
            payload = self._read_json_body()
            years = payload.get("years") or CRAWL_YEARS
            terms = payload.get("terms") or CRAWL_TERMS
            force = bool(payload.get("force"))
            confirm = bool(payload.get("confirm"))
            # forced re-collect of an already-마감 term: ask first (UI prompts, default N)
            if force and not confirm:
                conn = db.connect()
                closed = [[y, t] for y in years for t in terms if db.is_closed(conn, y, t)]
                if closed:
                    return self._json({"needs_confirm": True, "closed": closed})
            COUNTS["running"] = True  # set before releasing lock
        threading.Thread(target=_run_counts, args=(years, terms, force),
                         daemon=True).start()
        self._json({"started": True, "years": years, "terms": terms, "force": force})


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # log the traceback via logging instead of dumping raw to stderr
        log.exception("unhandled error serving %s", client_address)


def _bind(start_port):
    """Bind on the first free port at or above start_port (Vite-style)."""
    for port in range(start_port, start_port + PORT_SCAN_LIMIT):
        try:
            return Server((HOST, port), Handler), port
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            log.warning("port %d unavailable, trying %d", port, port + 1)
    raise SystemExit(
        f"no free port in {start_port}..{start_port + PORT_SCAN_LIMIT - 1}")


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "")


def main():
    # Exposing beyond localhost without an admin token would leave /api/refresh
    # (wipe & rebuild) world-writable — refuse rather than ship that footgun.
    if HOST not in _LOCAL_HOSTS and not ADMIN_TOKEN:
        raise SystemExit(
            f"refusing to bind non-local HOST={HOST!r} without ADMIN_TOKEN: the "
            "refresh endpoints would be world-writable. Set ADMIN_TOKEN (and put "
            "TLS/a proxy in front).")
    if SERVE_STATIC:
        log.info("SERVE_STATIC set: no database (static prod preview); /api -> 404")
    else:
        db.init_schema(db.connect())
    httpd, port = _bind(PORT)
    log.info("class-checker on http://%s:%d (web=%s)", HOST, port, WEB_DIR)
    if ADMIN_TOKEN:
        log.info("admin refresh requires X-Admin-Token")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
