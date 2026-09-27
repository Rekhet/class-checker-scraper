from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import scraper.server as server
from scraper import db


def _catalog(path: Path) -> None:
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    conn = db._Conn(raw, "sqlite")
    db.init_schema(conn)
    raw.execute("INSERT INTO terms (term, year, label) VALUES ('T1','2026','2026 2학기')")
    for i, (name, dept, cls) in enumerate([("통계학", "통계학과", '["학사","전필"]'),
                                           ("확률론", "통계학과", '["학사","전선"]'),
                                           ("대학 글쓰기 1", "기초교육원", '["학사","교양"]')]):
        raw.execute(
            "INSERT INTO classes (id, year, term, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no, subh_cd,"
            " name, professor, department, classification, credits, quota, applied)"
            " VALUES (?, '2026', 'T1', 'U1', 'U2', ?, '001', '000', ?, '김교수', ?, ?, 3, 30, 10)",
            (i + 1, f"C{i}", name, dept, cls))
        raw.execute("INSERT INTO class_slots (class_id, day_index, period, start_time, end_time)"
                    " VALUES (?, ?, 1, '09:00', '10:15')", (i + 1, i))
    raw.commit()
    raw.close()


class ServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.db_path = tmp / "catalog.db"
        _catalog(self.db_path)

        def connect(*_a, **_k):
            raw = sqlite3.connect(self.db_path)
            raw.row_factory = sqlite3.Row
            return db._Conn(raw, "sqlite")

        self.curated_dir = tmp / "scraper"
        self.curated_dir.mkdir()
        self._patches = [patch.object(server.db, "connect", connect),
                         patch.object(server, "SERVE_STATIC", False),
                         patch.object(server, "ADMIN_TOKEN", ""),
                         patch.object(server, "__file__", str(self.curated_dir / "server.py"))]
        for p in self._patches:
            p.start()
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _req(self, method: str, path: str, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers=headers or {})
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_vocabularies_and_search(self) -> None:
        status, out = self._req("GET", "/api/terms")
        self.assertEqual(status, 200)
        self.assertEqual(out["terms"][0]["label"], "2026 2학기")
        status, out = self._req("GET", "/api/search?year=2026&term=T1&department=%ED%86%B5%EA%B3%84")
        self.assertEqual((status, out["total"]), (200, 2))
        self.assertEqual({c["name"] for c in out["classes"]}, {"통계학", "확률론"})
        status, out = self._req("GET", "/api/search?classification=%EA%B5%90%EC%96%91")
        self.assertEqual([c["name"] for c in out["classes"]], ["대학 글쓰기 1"])
        status, out = self._req("GET", "/api/search?limit=1&offset=1")
        self.assertEqual((out["count"], out["total"], out["offset"]), (1, 3, 1))

    def test_lookup_returns_slots(self) -> None:
        status, out = self._req("POST", "/api/lookup", {"keys": [["2026", "T1", "C1", "001"]]})
        self.assertEqual(status, 200)
        self.assertEqual(out["classes"][0]["name"], "확률론")
        self.assertEqual(out["classes"][0]["slots"][0]["start_time"], "09:00")

    def test_status_counts(self) -> None:
        status, out = self._req("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(out["counts"], {"classes": 3, "slots": 3, "terms": 1})

    def test_paths_outside_web_and_docs_are_refused(self) -> None:
        for path in ("/../scraper/server.py", "/static/../../pyproject.toml",
                     "/docs/../collect.env", "/%2e%2e/%2e%2e/etc/passwd"):
            with self.subTest(path=path):
                status, _ = self._req("GET", path)
                self.assertEqual(status, 404)

    def test_unknown_api_is_404(self) -> None:
        self.assertEqual(self._req("GET", "/api/nope")[0], 404)

    def test_curated_write_needs_the_token_when_one_is_set(self) -> None:
        endpoint = next(iter(server.CURATED_ENDPOINTS))
        filename, bucket = server.CURATED_ENDPOINTS[endpoint]
        with patch.object(server, "ADMIN_TOKEN", "s3cret"):
            status, _ = self._req("POST", endpoint, {"a": 1})
            self.assertEqual(status, 401)
            status, out = self._req("POST", endpoint, {"a": 1},
                                    headers={"X-Admin-Token": "s3cret"})
            self.assertEqual(status, 200, out)
        written = json.loads((self.curated_dir / filename).read_text(encoding="utf-8"))
        self.assertEqual(written[bucket], [{"a": 1}])
        self.assertEqual(self._req("POST", endpoint, [1, 2])[0], 400)   # not an object


if __name__ == "__main__":
    unittest.main()
