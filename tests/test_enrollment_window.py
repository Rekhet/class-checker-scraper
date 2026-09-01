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

        self.assertEqual(len(schedule), 156)
        self.assertEqual(schedule[0], dt.datetime(2026, 8, 7, 8, 30))
        self.assertEqual(
            schedule[:7],
            (
                dt.datetime(2026, 8, 7, 8, 30),
                dt.datetime(2026, 8, 7, 8, 35),
                dt.datetime(2026, 8, 7, 8, 40),
                dt.datetime(2026, 8, 7, 8, 45),
                dt.datetime(2026, 8, 7, 8, 50),
                dt.datetime(2026, 8, 7, 8, 55),
                dt.datetime(2026, 8, 7, 9, 0),
            ),
        )
        self.assertEqual(schedule[7], dt.datetime(2026, 8, 7, 9, 10))
        self.assertEqual(schedule[51], dt.datetime(2026, 8, 7, 16, 30))
        self.assertEqual(schedule[52], dt.datetime(2026, 8, 10, 8, 30))
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

    def test_schedule_accepts_explicit_burst_and_regular_intervals(self) -> None:
        dates = parse_dates("2026-08-07")

        schedule = build_collection_schedule(
            dates,
            parse_time("08:30"),
            parse_time("09:30"),
            burst_minutes=10,
            burst_interval_minutes=5,
            interval_minutes=10,
        )

        self.assertEqual(
            schedule,
            (
                dt.datetime(2026, 8, 7, 8, 30),
                dt.datetime(2026, 8, 7, 8, 35),
                dt.datetime(2026, 8, 7, 8, 40),
                dt.datetime(2026, 8, 7, 8, 50),
                dt.datetime(2026, 8, 7, 9, 0),
                dt.datetime(2026, 8, 7, 9, 10),
                dt.datetime(2026, 8, 7, 9, 20),
                dt.datetime(2026, 8, 7, 9, 30),
            ),
        )

    def test_until_midnight_runs_continuously_to_midnight_after_last_date(self) -> None:
        dates = parse_dates("2026-09-01")

        schedule = build_collection_schedule(
            dates,
            parse_time("23:40"),
            parse_time("23:40"),  # end time is unused in until-midnight mode
            burst_minutes=10,
            burst_interval_minutes=5,
            interval_minutes=10,
            until_midnight=True,
        )

        self.assertEqual(
            schedule,
            (
                dt.datetime(2026, 9, 1, 23, 40),
                dt.datetime(2026, 9, 1, 23, 45),
                dt.datetime(2026, 9, 1, 23, 50),
                dt.datetime(2026, 9, 2, 0, 0),
            ),
        )

    def test_until_midnight_collects_overnight_between_consecutive_dates(self) -> None:
        dates = parse_dates("2026-09-01,2026-09-02")

        schedule = build_collection_schedule(
            dates, parse_time("08:30"), parse_time("16:30"), until_midnight=True
        )

        # burst 08:30-09:00 on day one only, then every 10 minutes through the
        # nights until midnight after the last date
        self.assertEqual(schedule[0], dt.datetime(2026, 9, 1, 8, 30))
        self.assertEqual(schedule[6], dt.datetime(2026, 9, 1, 9, 0))
        self.assertEqual(schedule[7], dt.datetime(2026, 9, 1, 9, 10))
        self.assertIn(dt.datetime(2026, 9, 1, 23, 50), schedule)
        self.assertIn(dt.datetime(2026, 9, 2, 0, 0), schedule)
        self.assertIn(dt.datetime(2026, 9, 2, 8, 30), schedule)
        self.assertNotIn(dt.datetime(2026, 9, 2, 8, 35), schedule)  # no second burst
        self.assertEqual(schedule[-1], dt.datetime(2026, 9, 3, 0, 0))

    def test_until_midnight_rejects_non_consecutive_dates(self) -> None:
        dates = parse_dates("2026-09-01,2026-09-03")

        with self.assertRaises(ValueError):
            build_collection_schedule(
                dates, parse_time("08:30"), parse_time("16:30"), until_midnight=True
            )

    def test_until_midnight_cleanup_is_ten_minutes_after_midnight(self) -> None:
        dates = parse_dates("2026-09-01,2026-09-02")

        self.assertEqual(
            cleanup_time(dates[-1], parse_time("16:30"), until_midnight=True),
            dt.datetime(2026, 9, 3, 0, 10),
        )

    def test_until_midnight_environment_extends_window_past_last_date(self) -> None:
        dates = parse_dates("2026-09-01,2026-09-02")

        environment = render_window_environment(
            dates, "2026", "fall", "Asia/Seoul", until_midnight=True
        )

        # the midnight run fires on the day AFTER the last date; the crawl gate
        # checks today against ENROLL_WINDOWS, so the window must cover it
        self.assertIn(
            "ENROLL_WINDOWS=2026-09-01..2026-09-03", environment
        )

    def test_dry_run_wires_until_midnight_option(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "start_enrollment_window.py"),
                "--dates",
                "2026-09-01",
                "--start-time",
                "23:40",
                "--burst-minutes",
                "10",
                "--burst-interval",
                "5",
                "--interval",
                "10",
                "--until-midnight",
                "--year",
                "2026",
                "--semester",
                "fall",
                "--timezone",
                "Asia/Seoul",
                "--dry-run",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("window=20260901 collector_runs=4", result.stdout)
        self.assertIn("2026-09-02 00:00:00 Asia/Seoul", result.stdout)
        self.assertIn("cleanup_at=2026-09-02 00:10:00 Asia/Seoul", result.stdout)

    def test_dry_run_wires_cli_cadence_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "start_enrollment_window.py"),
                "--dates",
                "2026-08-07",
                "--start-time",
                "08:30",
                "--end-time",
                "09:30",
                "--burst-minutes",
                "10",
                "--burst-interval",
                "5",
                "--interval",
                "10",
                "--year",
                "2026",
                "--semester",
                "fall",
                "--timezone",
                "Asia/Seoul",
                "--dry-run",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("window=20260807 collector_runs=8", result.stdout)
        self.assertIn("2026-08-07 08:35:00 Asia/Seoul", result.stdout)
        self.assertIn("2026-08-07 08:50:00 Asia/Seoul", result.stdout)

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
        self.assertEqual(parse_time("08:35"), dt.time(8, 35))
        with self.assertRaises(ValueError):
            parse_time("08:33")

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
            self.assertEqual(
                timer.read_text(encoding="utf-8").count("OnCalendar="), 156
            )
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
