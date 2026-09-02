from __future__ import annotations

import threading
import time
import unittest

from scraper.crawl import fetch_live_classes


def _page_html(rows):
    """Minimal client stub payload — the stub parser below consumes it."""
    return rows


class _StubClient:
    """Serves TOTAL classes 10 per page and records request concurrency."""

    def __init__(self, total: int, fail_pages: set[int] = frozenset(),
                 failures_per_page: int = 1):
        self.total = total
        self.calls: list[int] = []
        self.failed_once: set[int] = set(fail_pages)
        self._fail_left = {p: failures_per_page for p in fail_pages}
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def search_page(self, year, term, *, page=1, page_size=9999, extra=None):
        with self._lock:
            self.calls.append(page)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            time.sleep(0.02)   # emulate network latency so overlap is observable
            if self._fail_left.get(page, 0) > 0:
                self._fail_left[page] -= 1
                raise RuntimeError(f"transient failure on page {page}")
            start = (page - 1) * 10
            rows = [{"sbjt_cd": f"M{i:05d}", "lt_no": "001"}
                    for i in range(start, min(start + 10, self.total))]
            return {"classes": rows, "total": self.total,
                    "page_count": len(rows)}
        finally:
            with self._lock:
                self._active -= 1


def _identity_parse(payload):
    return payload


class ParallelFetchTests(unittest.TestCase):
    def _fetch(self, client, **kw):
        return fetch_live_classes(client, "2026", "T1",
                                  parse_response=_identity_parse, **kw)

    def test_fetches_every_class_exactly_once(self) -> None:
        client = _StubClient(total=95)

        fetched = self._fetch(client)

        self.assertEqual(len(fetched), 95)
        self.assertEqual(len({c["sbjt_cd"] for c in fetched}), 95)
        self.assertEqual(sorted(client.calls), list(range(1, 11)))

    def test_pages_are_fetched_concurrently(self) -> None:
        client = _StubClient(total=200)

        self._fetch(client, workers=5)

        self.assertGreater(client.max_active, 1)

    def test_transient_page_failure_is_retried(self) -> None:
        client = _StubClient(total=50, fail_pages={3})

        fetched = self._fetch(client)

        self.assertEqual(len(fetched), 50)
        self.assertEqual(client.calls.count(3), 2)  # failed once, retried

    def test_page_survives_up_to_five_transient_failures(self) -> None:
        client = _StubClient(total=50, fail_pages={3}, failures_per_page=5)

        fetched = self._fetch(client)

        self.assertEqual(len(fetched), 50)
        self.assertEqual(client.calls.count(3), 6)  # 1 try + 5 retries

    def test_sixth_failure_gives_up(self) -> None:
        client = _StubClient(total=50, fail_pages={3}, failures_per_page=6)

        with self.assertRaises(RuntimeError):
            self._fetch(client)

    def test_single_page_result_needs_no_extra_requests(self) -> None:
        client = _StubClient(total=7)

        fetched = self._fetch(client)

        self.assertEqual(len(fetched), 7)
        self.assertEqual(client.calls, [1])


if __name__ == "__main__":
    unittest.main()
