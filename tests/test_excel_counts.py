from __future__ import annotations

import unittest

from scraper.crawl import excel_count_fields


class ExcelCountFieldsTests(unittest.TestCase):
    def test_maps_raw_applied_to_enrolled_and_caps_applied(self) -> None:
        # over-quota class (정원외신청 pending): raw 44 on quota 40
        f = excel_count_fields({"applied": 44, "quota": 40, "cart": 3})
        self.assertEqual(f, {"applied": 40, "enrolled": 44, "quota": 40, "cart": 3})

    def test_under_quota_passes_through(self) -> None:
        f = excel_count_fields({"applied": 13, "quota": 40, "cart": None})
        self.assertEqual(f, {"applied": 13, "enrolled": 13, "quota": 40, "cart": None})

    def test_missing_quota_keeps_raw_applied(self) -> None:
        f = excel_count_fields({"applied": 7, "quota": None, "cart": None})
        self.assertEqual(f["applied"], 7)
        self.assertEqual(f["enrolled"], 7)
        self.assertIsNone(f["quota"])

    def test_missing_applied_yields_nones(self) -> None:
        f = excel_count_fields({"applied": None, "quota": 30, "cart": 2})
        self.assertIsNone(f["applied"])
        self.assertIsNone(f["enrolled"])
        self.assertEqual(f["quota"], 30)
        self.assertEqual(f["cart"], 2)


if __name__ == "__main__":
    unittest.main()
