from __future__ import annotations

import datetime as dt
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.cleanup_cart_window import _window_paths
from scripts.start_enrollment_window import (
    build_collection_schedule,
    cleanup_time,
    parse_dates,
    parse_time,
    render_timer_unit,
    render_window_environment,
    window_id,
)


class EnrollmentWindowScheduleTests(unittest.TestCase):
    def test_schedule_covers_only_requested_registration_days(self) -> None:
        dates = parse_dates("2026-08-07,2026-08-10,2026-08-11")
        start_time = parse_time("08:30")
        end_time = parse_time("16:30")

        schedule = build_collection_schedule(dates, start_time, end_time)

        self.assertEqual(len(schedule), 147)
        self.assertEqual(schedule[0], dt.datetime(2026, 8, 7, 8, 30))
        self.assertEqual(schedule[48], dt.datetime(2026, 8, 7, 16, 30))
        self.assertEqual(schedule[49], dt.datetime(2026, 8, 10, 8, 30))
        self.assertNotIn(dt.datetime(2026, 8, 8, 8, 30), schedule)
        self.assertNotIn(dt.datetime(2026, 8, 12, 8, 30), schedule)
        self.assertEqual(schedule[-1], dt.datetime(2026, 8, 11, 16, 30))

    def test_schedule_and_environment_render_enrollment_scope(self) -> None:
        dates = parse_dates("2026-08-07,2026-08-10,2026-08-11")
        identifier = window_id(dates)
        schedule = build_collection_schedule(
            dates, parse_time("08:30"), parse_time("16:30")
        )

        timer = render_timer_unit(identifier, schedule, "Asia/Seoul")
        environment = render_window_environment(
            dates, "2026", "fall", "Asia/Seoul"
        )

        self.assertEqual(identifier, "20260807-20260810-20260811")
        self.assertIn(
            "Unit=class-checker.enrollment@20260807-20260810-20260811.service",
            timer,
        )
        self.assertIn("OnCalendar=2026-08-07 08:30:00 Asia/Seoul", timer)
        self.assertIn("OnCalendar=2026-08-10 16:30:00 Asia/Seoul", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("COUNT_MODE=enrollment", environment)
        self.assertIn(
            "ENROLL_WINDOWS=2026-08-07,2026-08-10,2026-08-11", environment
        )
        self.assertIn("CART_WINDOWS=", environment)

    def test_cleanup_is_ten_minutes_after_last_collection(self) -> None:
        dates = parse_dates("2026-08-07,2026-08-10,2026-08-11")

        self.assertEqual(
            cleanup_time(dates[-1], parse_time("16:30")),
            dt.datetime(2026, 8, 11, 16, 40),
        )

    def test_validation_rejects_duplicate_dates_and_bad_intervals(self) -> None:
        with self.assertRaises(ValueError):
            parse_dates("2026-08-07,2026-08-07")
        with self.assertRaises(ValueError):
            parse_time("8:30")
        with self.assertRaises(ValueError):
            parse_time("08:35")

    def test_no_start_installs_reusable_units_and_one_multi_date_timer(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            unit_dir = Path(tmp) / "user"
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "start_enrollment_window.py"),
                    "--dates",
                    "2026-08-07,2026-08-10,2026-08-11",
                    "--year",
                    "2026",
                    "--semester",
                    "fall",
                    "--timezone",
                    "Asia/Seoul",
                    "--unit-dir",
                    str(unit_dir),
                    "--no-start",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (unit_dir / "class-checker.enrollment@.service").exists()
            )
            self.assertTrue(
                (unit_dir / "class-checker.enrollment-cleanup@.service").exists()
            )
            timer = unit_dir / (
                "class-checker.enrollment@20260807-20260810-20260811.timer"
            )
            self.assertTrue(timer.exists())
            self.assertEqual(timer.read_text(encoding="utf-8").count("OnCalendar="), 147)
            self.assertTrue(
                (
                    unit_dir
                    / "class-checker.enrollment-cleanup@20260807-20260810-20260811.timer"
                ).exists()
            )
            self.assertTrue(
                (
                    unit_dir
                    / "class-checker.enrollment-window-20260807-20260810-20260811.env"
                ).exists()
            )

    def test_cleanup_targets_only_the_enrollment_window_paths(self) -> None:
        unit_dir = Path("unit-dir")
        paths = _window_paths(
            unit_dir, "20260807-20260810-20260811", "enrollment"
        )

        self.assertEqual(
            paths,
            (
                unit_dir / "class-checker.enrollment@20260807-20260810-20260811.timer",
                unit_dir
                / "class-checker.enrollment-cleanup@20260807-20260810-20260811.timer",
                unit_dir
                / "class-checker.enrollment-window-20260807-20260810-20260811.env",
            ),
        )


if __name__ == "__main__":
    unittest.main()
