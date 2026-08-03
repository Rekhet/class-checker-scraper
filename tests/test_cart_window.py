from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scraper.process_lock import ProcessLock
from scripts.start_cart_window import (
    build_collection_schedule,
    cleanup_time,
    parse_start_date,
    render_cleanup_timer_unit,
    render_timer_unit,
    render_window_environment,
    validate_semester,
    validate_timezone,
    validate_year,
    window_id,
)


ROOT = Path(__file__).resolve().parents[1]
CLEANUP = ROOT / "scripts" / "cleanup_cart_window.py"


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class CartWindowScheduleTests(unittest.TestCase):
    def test_schedule_covers_exact_two_day_window(self) -> None:
        start = dt.date(2026, 8, 4)

        schedule = build_collection_schedule(start)

        self.assertEqual(len(schedule), 187)
        self.assertEqual(schedule[0], dt.datetime(2026, 8, 4, 9, 0))
        self.assertEqual(schedule[89], dt.datetime(2026, 8, 4, 23, 50))
        self.assertEqual(schedule[90], dt.datetime(2026, 8, 5, 0, 0))
        self.assertEqual(schedule[-1], dt.datetime(2026, 8, 5, 16, 0))
        self.assertNotIn(dt.datetime(2026, 8, 5, 16, 10), schedule)
        self.assertEqual(cleanup_time(start), dt.datetime(2026, 8, 5, 16, 10))

    def test_schedule_handles_month_year_and_leap_boundaries(self) -> None:
        self.assertEqual(
            build_collection_schedule(dt.date(2026, 1, 31))[90].date(),
            dt.date(2026, 2, 1),
        )
        self.assertEqual(
            build_collection_schedule(dt.date(2026, 12, 31))[90].date(),
            dt.date(2027, 1, 1),
        )
        self.assertEqual(
            build_collection_schedule(dt.date(2028, 2, 29))[90].date(),
            dt.date(2028, 3, 1),
        )

    def test_validation_rejects_unsafe_or_invalid_scope(self) -> None:
        with self.assertRaises(ValueError):
            parse_start_date("2026-02-30")
        with self.assertRaises(ValueError):
            parse_start_date("20260804")
        with self.assertRaises(ValueError):
            validate_year("26")
        with self.assertRaises(ValueError):
            validate_year("2026/08")
        with self.assertRaises(ValueError):
            validate_semester("fall/../../tmp")
        with self.assertRaises(ValueError):
            validate_timezone("Not/A_Timezone")

        self.assertEqual(parse_start_date("2026-08-04"), dt.date(2026, 8, 4))
        self.assertEqual(validate_year("2026"), "2026")
        self.assertEqual(validate_semester("fall"), "fall")
        self.assertEqual(validate_timezone("Asia/Seoul"), "Asia/Seoul")
        self.assertEqual(window_id(dt.date(2026, 8, 4)), "20260804")

    def test_rendered_timer_has_explicit_timezone_and_no_late_collector_run(self) -> None:
        start = dt.date(2026, 8, 4)
        timer = render_timer_unit(
            window_id(start), build_collection_schedule(start), "Asia/Seoul"
        )
        cleanup = render_cleanup_timer_unit(
            window_id(start), cleanup_time(start), "Asia/Seoul"
        )

        self.assertIn("Unit=class-checker.cart@20260804.service", timer)
        self.assertIn("OnCalendar=2026-08-04 09:00:00 Asia/Seoul", timer)
        self.assertIn("OnCalendar=2026-08-05 16:00:00 Asia/Seoul", timer)
        self.assertNotIn("OnCalendar=2026-08-05 16:10:00 Asia/Seoul", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("AccuracySec=1s", timer)
        self.assertIn("OnCalendar=2026-08-05 16:10:00 Asia/Seoul", cleanup)
        self.assertIn("Unit=class-checker.cart-cleanup@20260804.service", cleanup)
        self.assertIn("Persistent=true", cleanup)

    def test_window_environment_is_explicit_and_overrides_collect_env(self) -> None:
        environment = render_window_environment(
            dt.date(2026, 8, 4), "2026", "fall", "Asia/Seoul"
        )

        self.assertIn("COUNT_YEAR=2026", environment)
        self.assertIn("COUNT_SEM=fall", environment)
        self.assertIn("COUNT_MODE=cart", environment)
        self.assertIn("COLLECTION_TIMEZONE=Asia/Seoul", environment)
        self.assertIn("CART_WINDOWS=2026-08-04..2026-08-05", environment)

    def test_launcher_is_idempotent_for_one_window_and_rejects_another(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unit_dir = Path(tmp) / "user"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "start_cart_window.py"),
                "--start-date",
                "2026-08-04",
                "--year",
                "2026",
                "--semester",
                "fall",
                "--timezone",
                "Asia/Seoul",
                "--unit-dir",
                str(unit_dir),
                "--no-start",
            ]
            first = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
            second = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
            other = subprocess.run(
                [*command[:2], "--start-date", "2026-08-05", *command[4:]],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(other.returncode, 0)
            self.assertTrue((unit_dir / "class-checker.cart@.service").exists())
            self.assertTrue((unit_dir / "class-checker.cart-cleanup@.service").exists())
            self.assertTrue((unit_dir / "class-checker.cart@20260804.timer").exists())
            self.assertTrue((unit_dir / "class-checker.cart-cleanup@20260804.timer").exists())
            self.assertTrue((unit_dir / "class-checker.cart-window-20260804.env").exists())
            self.assertFalse((unit_dir / "class-checker.cart@20260805.timer").exists())

    def test_launcher_requires_explicit_replacement_of_broad_timer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            log = root / "systemctl.log"
            fake_systemctl = root / "systemctl"
            _executable(
                fake_systemctl,
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\n"
                "case \"$2\" in\n"
                "  is-active) echo active; exit 0;;\n"
                "  is-enabled) echo enabled; exit 0;;\n"
                "esac\n"
                "exit 0\n",
            )
            base = [
                sys.executable,
                str(ROOT / "scripts" / "start_cart_window.py"),
                "--start-date",
                "2026-08-04",
                "--year",
                "2026",
                "--semester",
                "fall",
                "--unit-dir",
                str(unit_dir),
                "--systemctl",
                str(fake_systemctl),
            ]
            env = os.environ.copy()
            env["SYSTEMCTL_LOG"] = str(log)
            blocked = subprocess.run(base, cwd=ROOT, env=env, check=False, capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertFalse((unit_dir / "class-checker.cart@20260804.timer").exists())

            allowed = subprocess.run(
                [*base, "--disable-broad-timer"],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            commands = log.read_text(encoding="utf-8")
            self.assertIn("disable --now class-checker.update-counts.timer", commands)
            self.assertIn("enable --now class-checker.cart@20260804.timer", commands)

    def test_dry_run_prints_schedule_without_writing_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unit_dir = Path(tmp) / "user"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "start_cart_window.py"),
                    "--start-date",
                    "2026-08-04",
                    "--year",
                    "2026",
                    "--semester",
                    "fall",
                    "--timezone",
                    "Asia/Seoul",
                    "--unit-dir",
                    str(unit_dir),
                    "--dry-run",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("collector_runs=187", result.stdout)
            self.assertIn("2026-08-05 16:00:00 Asia/Seoul", result.stdout)
            self.assertIn("cleanup_at=2026-08-05 16:10:00 Asia/Seoul", result.stdout)
            self.assertFalse(unit_dir.exists())


class CartWindowCleanupTests(unittest.TestCase):
    def test_cleanup_waits_for_delayed_collector_without_stopping_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            unit_dir.mkdir()
            state = root / "state"
            log = root / "systemctl.log"
            fake_systemctl = root / "systemctl"
            _executable(
                fake_systemctl,
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "log = pathlib.Path(os.environ['SYSTEMCTL_LOG'])\n"
                "with log.open('a', encoding='utf-8') as fh: fh.write(' '.join(args) + '\\n')\n"
                "command = args[1] if args and args[0] == '--user' and len(args) > 1 else (args[0] if args else '')\n"
                "if command == 'show' and 'Result' in args:\n"
                "    print(os.environ.get('SYSTEMCTL_RESULT', 'success'))\n"
                "elif command == 'show' and 'ExecMainStartTimestamp' in args:\n"
                "    print('Tue 2026-08-04 09:00:00 KST')\n"
                "elif command == 'show' and any(x.endswith('.service') for x in args):\n"
                "    state = pathlib.Path(os.environ['SYSTEMCTL_STATE'])\n"
                "    count = int(state.read_text() or '0') + 1 if state.exists() else 1\n"
                "    state.write_text(str(count))\n"
                "    print('active' if count < 3 else 'inactive')\n"
            )
            timer = unit_dir / "class-checker.cart@20260804.timer"
            env_file = unit_dir / "class-checker.cart-window-20260804.env"
            timer.write_text("timer", encoding="utf-8")
            env_file.write_text("env", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "SYSTEMCTL_LOG": str(log),
                    "SYSTEMCTL_STATE": str(state),
                    "CRAWL_LOCK_PATH": str(root / "crawl.lock"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP),
                    "--window-id",
                    "20260804",
                    "--unit-dir",
                    str(unit_dir),
                    "--systemctl",
                    str(fake_systemctl),
                    "--wait-timeout",
                    "2",
                    "--poll-interval",
                    "0.01",
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(timer.exists())
            self.assertFalse(env_file.exists())
            commands = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("stop class-checker.cart@20260804.timer" in x for x in commands))
            self.assertFalse(any(
                (x.startswith("stop ") or x.startswith("disable "))
                and "class-checker.cart@20260804.service" in x
                for x in commands
            ))

    def test_cleanup_leaves_units_when_collector_result_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            unit_dir.mkdir()
            log = root / "systemctl.log"
            fake_systemctl = root / "systemctl"
            _executable(
                fake_systemctl,
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "log = pathlib.Path(os.environ['SYSTEMCTL_LOG'])\n"
                "with log.open('a', encoding='utf-8') as fh: fh.write(' '.join(args) + '\\n')\n"
                "command = args[1] if args and args[0] == '--user' and len(args) > 1 else (args[0] if args else '')\n"
                "if command == 'show' and 'Result' in args:\n"
                "    print('exit-code')\n"
                "elif command == 'show' and any(x.endswith('.service') for x in args):\n"
                "    print('inactive')\n"
            )
            timer = unit_dir / "class-checker.cart@20260804.timer"
            cleanup_timer = unit_dir / "class-checker.cart-cleanup@20260804.timer"
            env_file = unit_dir / "class-checker.cart-window-20260804.env"
            for path in (timer, cleanup_timer, env_file):
                path.write_text("placeholder", encoding="utf-8")

            env = os.environ.copy()
            env["SYSTEMCTL_LOG"] = str(log)
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP),
                    "--window-id",
                    "20260804",
                    "--unit-dir",
                    str(unit_dir),
                    "--systemctl",
                    str(fake_systemctl),
                    "--wait-timeout",
                    "2",
                    "--poll-interval",
                    "0.01",
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collector service result", result.stderr)
            self.assertTrue(timer.exists())
            self.assertTrue(cleanup_timer.exists())
            self.assertTrue(env_file.exists())

    def test_cleanup_leaves_units_when_collector_result_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            unit_dir.mkdir()
            fake_systemctl = root / "systemctl"
            _executable(
                fake_systemctl,
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "command = args[1] if args and args[0] == '--user' and len(args) > 1 else (args[0] if args else '')\n"
                "if command == 'show' and 'Result' in args:\n"
                "    raise SystemExit(5)\n"
                "if command == 'show':\n"
                "    print('inactive')\n"
            )
            timer = unit_dir / "class-checker.cart@20260804.timer"
            env_file = unit_dir / "class-checker.cart-window-20260804.env"
            timer.write_text("placeholder", encoding="utf-8")
            env_file.write_text("placeholder", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP),
                    "--window-id",
                    "20260804",
                    "--unit-dir",
                    str(unit_dir),
                    "--systemctl",
                    str(fake_systemctl),
                    "--wait-timeout",
                    "2",
                    "--poll-interval",
                    "0.01",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collector service not found", result.stderr)
            self.assertTrue(timer.exists())
            self.assertTrue(env_file.exists())

    def test_cleanup_leaves_units_when_collector_has_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            unit_dir.mkdir()
            fake_systemctl = root / "systemctl"
            _executable(
                fake_systemctl,
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "command = args[1] if args and args[0] == '--user' and len(args) > 1 else (args[0] if args else '')\n"
                "if command == 'show' and 'Result' in args:\n"
                "    print('success')\n"
                "elif command == 'show' and 'ExecMainStartTimestamp' in args:\n"
                "    print('')\n"
                "elif command == 'show':\n"
                "    print('inactive')\n"
            )
            timer = unit_dir / "class-checker.cart@20260804.timer"
            env_file = unit_dir / "class-checker.cart-window-20260804.env"
            timer.write_text("placeholder", encoding="utf-8")
            env_file.write_text("placeholder", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP),
                    "--window-id",
                    "20260804",
                    "--unit-dir",
                    str(unit_dir),
                    "--systemctl",
                    str(fake_systemctl),
                    "--wait-timeout",
                    "2",
                    "--poll-interval",
                    "0.01",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("did not run", result.stderr)
            self.assertTrue(timer.exists())
            self.assertTrue(env_file.exists())

    def test_cleanup_leaves_units_when_shared_lock_stays_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "user"
            unit_dir.mkdir()
            fake_systemctl = root / "systemctl"
            _executable(fake_systemctl, "#!/bin/sh\nexit 0\n")
            timer = unit_dir / "class-checker.cart@20260804.timer"
            env_file = unit_dir / "class-checker.cart-window-20260804.env"
            timer.write_text("timer", encoding="utf-8")
            env_file.write_text("env", encoding="utf-8")
            lock_path = root / "crawl.lock"

            with ProcessLock(lock_path, timeout=0):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(CLEANUP),
                        "--window-id",
                        "20260804",
                        "--unit-dir",
                        str(unit_dir),
                        "--systemctl",
                        str(fake_systemctl),
                        "--lock-path",
                        str(lock_path),
                        "--wait-timeout",
                        "0.05",
                        "--poll-interval",
                        "0.01",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(timer.exists())
            self.assertTrue(env_file.exists())


if __name__ == "__main__":
    unittest.main()
