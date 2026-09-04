from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scraper import crawl, windows
from scraper.sync_counts import staleness_warning


SEOUL = {"COLLECTION_TIMEZONE": "Asia/Seoul"}


def _at(hour: int, minute: int, day: int = 15) -> datetime:
    """A UTC instant; Asia/Seoul is +9, so callers pass Seoul-relative values
    through _seoul() instead when the local clock matters."""
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _seoul(hour: int, minute: int, day: int = 15) -> datetime:
    return _at(hour - 9, minute, day)


class WindowArithmeticTests(unittest.TestCase):
    def test_single_day_and_range_windows(self) -> None:
        spec = "2026-08-07,2026-09-08..2026-10-20"
        self.assertTrue(windows.in_windows(spec, "2026-08-07"))
        self.assertTrue(windows.in_windows(spec, "2026-09-08"))
        self.assertTrue(windows.in_windows(spec, "2026-10-20"))
        self.assertFalse(windows.in_windows(spec, "2026-08-08"))
        self.assertFalse(windows.in_windows(spec, "2026-10-21"))
        self.assertFalse(windows.in_windows("", "2026-09-08"))

    def test_hour_slot_opens_only_at_the_top_of_the_hour(self) -> None:
        with patch.dict(os.environ, SEOUL, clear=False):
            self.assertTrue(windows.hour_slot_open(_seoul(14, 3)))
            self.assertTrue(windows.hour_slot_open(_seoul(14, 9)))
            self.assertFalse(windows.hour_slot_open(_seoul(14, 13)))
            self.assertFalse(windows.hour_slot_open(_seoul(14, 53)))

    def test_today_uses_the_collection_timezone(self) -> None:
        instant = datetime(2026, 9, 14, 15, 30, tzinfo=timezone.utc)  # 09-15 KST
        with patch.dict(os.environ, SEOUL, clear=False):
            self.assertEqual(windows.today_iso(instant), "2026-09-15")


class SlowWindowSamplingTests(unittest.TestCase):
    ENV = dict(SEOUL, CART_WINDOWS="2026-08-04",
               ENROLL_WINDOWS="2026-09-01..2026-09-09",
               ENROLL_SLOW_WINDOWS="2026-09-08..2026-10-20")

    def test_slow_window_samples_once_an_hour(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False), \
             patch.object(crawl, "_today_iso", return_value="2026-09-15"):
            self.assertTrue(crawl._slow_enroll_open(_seoul(14, 3)))
            self.assertFalse(crawl._slow_enroll_open(_seoul(14, 33)))

    def test_slow_window_feeds_the_enrollment_switch(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False), \
             patch.object(crawl, "_today_iso", return_value="2026-09-15"), \
             patch.object(crawl.windows, "hour_slot_open", return_value=True):
            self.assertTrue(crawl._sample_windows()["collect_enrolled"])
        with patch.dict(os.environ, self.ENV, clear=False), \
             patch.object(crawl, "_today_iso", return_value="2026-09-15"), \
             patch.object(crawl.windows, "hour_slot_open", return_value=False):
            self.assertFalse(crawl._sample_windows()["collect_enrolled"])

    def test_fast_window_day_ignores_the_hourly_slot(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False), \
             patch.object(crawl, "_today_iso", return_value="2026-09-08"), \
             patch.object(crawl.windows, "hour_slot_open", return_value=False):
            self.assertTrue(crawl._sample_windows()["collect_enrolled"])

    def test_outside_every_window_nothing_is_collected(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False), \
             patch.object(crawl, "_today_iso", return_value="2026-10-21"):
            options = crawl._sample_windows()
        self.assertFalse(options["collect_enrolled"])
        self.assertFalse(options["collect_cart"])


class StalenessWarningTests(unittest.TestCase):
    ENV = dict(SEOUL, CART_WINDOWS="", ENROLL_WINDOWS="2026-09-15",
               ENROLL_SLOW_WINDOWS="")

    def test_silent_outside_every_window(self) -> None:
        env = dict(self.ENV, ENROLL_WINDOWS="2026-09-01")
        with patch.dict(os.environ, env, clear=False):
            self.assertIsNone(staleness_warning("2026-09-01T09:00:00",
                                                _seoul(14, 0)))

    def test_silent_while_samples_are_fresh(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            self.assertIsNone(staleness_warning("2026-09-15T13:40:00",
                                                _seoul(14, 0)))

    def test_warns_when_a_fast_window_goes_quiet(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            warning = staleness_warning("2026-09-15T11:00:00", _seoul(14, 0))
        self.assertIsNotNone(warning)
        self.assertIn("180 minutes old", warning)

    def test_slow_window_tolerates_an_hourly_cadence(self) -> None:
        env = dict(self.ENV, ENROLL_WINDOWS="",
                   ENROLL_SLOW_WINDOWS="2026-09-08..2026-10-20")
        with patch.dict(os.environ, env, clear=False):
            self.assertIsNone(staleness_warning("2026-09-15T12:40:00",
                                                _seoul(14, 0)))
            self.assertIsNotNone(staleness_warning("2026-09-15T09:00:00",
                                                   _seoul(14, 0)))

    def test_warns_when_a_window_has_no_samples_at_all(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            self.assertIn("no samples exist yet",
                          staleness_warning(None, _seoul(14, 0)))


if __name__ == "__main__":
    unittest.main()
