from __future__ import annotations

import http.client
import threading
import unittest
from unittest.mock import patch

import scraper.server as server


class CrossOriginPostTests(unittest.TestCase):
    """Without ADMIN_TOKEN the write endpoints must still refuse a POST that a
    browser sends on behalf of another site (or through DNS rebinding)."""

    def setUp(self) -> None:
        # SERVE_STATIC: no database; an accepted /api POST answers 404, a
        # refused one 403 — enough to observe the gate without side effects.
        self._patches = [patch.object(server, "SERVE_STATIC", True),
                         patch.object(server, "ADMIN_TOKEN", "")]
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

    def _post(self, headers: dict) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/refresh", body=b"{}",
                     headers={"Content-Type": "text/plain", **headers})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_same_origin_browser_post_is_accepted(self) -> None:
        host = f"127.0.0.1:{self.port}"
        self.assertEqual(self._post({"Origin": f"http://{host}",
                                     "Sec-Fetch-Site": "same-origin"}), 404)

    def test_non_browser_client_is_accepted(self) -> None:
        self.assertEqual(self._post({}), 404)

    def test_cross_site_origin_is_refused(self) -> None:
        self.assertEqual(self._post({"Origin": "https://evil.example"}), 403)

    def test_cross_site_fetch_metadata_is_refused(self) -> None:
        self.assertEqual(self._post({"Sec-Fetch-Site": "cross-site"}), 403)

    def test_null_origin_is_refused(self) -> None:
        self.assertEqual(self._post({"Origin": "null"}), 403)

    def test_rebound_host_name_is_refused(self) -> None:
        self.assertEqual(self._post({"Host": f"evil.example:{self.port}"}), 403)


class LookupLimitTests(unittest.TestCase):
    def test_oversized_lookup_is_rejected_before_the_database(self) -> None:
        handler = server.Handler.__new__(server.Handler)
        sent = {}
        handler._read_json_body = lambda: {
            "keys": [["2026", "T", "C", "1"]] * (server.LOOKUP_MAX_KEYS + 1)}
        handler._json = lambda obj, status=200: sent.update(status=status)
        with patch.object(server.db, "connect") as connect:
            handler._lookup()
        self.assertEqual(sent["status"], 400)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
