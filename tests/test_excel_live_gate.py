from __future__ import annotations

import unittest

from scraper.crawl import excel_live_mismatches


def _ex(sbjt, applied, enrolled):
    return {"sbjt_cd": sbjt, "lt_no": "001", "applied": applied,
            "enrolled": enrolled}


def _html(sbjt, applied, enrolled):
    return {"sbjt_cd": sbjt, "lt_no": "001", "applied": applied,
            "enrolled": enrolled}


class ExcelLiveGateTests(unittest.TestCase):
    def test_matching_values_produce_no_mismatches(self) -> None:
        ex = {("A", "001"): _ex("A", 40, 44), ("B", "001"): _ex("B", 13, 13)}
        html = [_html("A", 40, 44), _html("B", 13, 13)]

        checked, bad = excel_live_mismatches(ex, html)

        self.assertEqual(checked, 2)
        self.assertEqual(bad, 0)

    def test_one_off_churn_is_tolerated(self) -> None:
        # a student enrolled between the two fetches: ±1 is live churn, not lag
        ex = {("A", "001"): _ex("A", 13, 13)}
        html = [_html("A", 14, 14)]

        checked, bad = excel_live_mismatches(ex, html)

        self.assertEqual((checked, bad), (1, 0))

    def test_stale_values_are_flagged(self) -> None:
        ex = {("A", "001"): _ex("A", 10, 10), ("B", "001"): _ex("B", 5, 5)}
        html = [_html("A", 25, 30), _html("B", 5, 5)]

        checked, bad = excel_live_mismatches(ex, html)

        self.assertEqual((checked, bad), (2, 1))

    def test_rows_missing_from_excel_count_as_mismatches(self) -> None:
        ex = {}
        html = [_html("A", 25, 30)]

        checked, bad = excel_live_mismatches(ex, html)

        self.assertEqual((checked, bad), (1, 1))

    def test_null_counts_are_skipped_not_flagged(self) -> None:
        ex = {("A", "001"): _ex("A", None, None)}
        html = [_html("A", None, None)]

        checked, bad = excel_live_mismatches(ex, html)

        self.assertEqual((checked, bad), (0, 0))


if __name__ == "__main__":
    unittest.main()
