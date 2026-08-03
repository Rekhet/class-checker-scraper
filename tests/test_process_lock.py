from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scraper.process_lock import (
    LOCK_HELD_ENV,
    LOCK_TIMEOUT_EXIT,
    LockTimeout,
    ProcessLock,
)


class ProcessLockTests(unittest.TestCase):
    def test_child_cannot_enter_until_parent_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "crawl.lock"
            command = [
                sys.executable,
                "-c",
                "import os; raise SystemExit(0 if os.getenv('" + LOCK_HELD_ENV + "') == '1' else 2)",
            ]

            with ProcessLock(lock_path, timeout=0):
                blocked = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scraper.process_lock",
                        "--path",
                        str(lock_path),
                        "--timeout",
                        "0",
                        "--",
                        *command,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(blocked.returncode, LOCK_TIMEOUT_EXIT)

            released = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scraper.process_lock",
                    "--path",
                    str(lock_path),
                    "--timeout",
                    "1",
                    "--",
                    *command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(released.returncode, 0, released.stderr)

    def test_lock_path_parent_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "nested" / "crawl.lock"
            with ProcessLock(lock_path, timeout=0):
                self.assertTrue(lock_path.exists())

    def test_nonzero_child_releases_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "crawl.lock"
            failed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scraper.process_lock",
                    "--path",
                    str(lock_path),
                    "--timeout",
                    "1",
                    "--",
                    sys.executable,
                    "-c",
                    "raise SystemExit(17)",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(failed.returncode, 17, failed.stderr)
            with ProcessLock(lock_path, timeout=0):
                pass

    def test_exception_inside_lock_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "crawl.lock"
            with self.assertRaises(RuntimeError):
                with ProcessLock(lock_path, timeout=0):
                    raise RuntimeError("simulated worker failure")

            with ProcessLock(lock_path, timeout=0):
                pass

    def test_process_group_interruption_releases_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "crawl.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scraper.process_lock",
                    "--path",
                    str(lock_path),
                    "--timeout",
                    "30",
                    "--",
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with ProcessLock(lock_path, timeout=0):
                            pass
                    except LockTimeout:
                        break
                    if holder.poll() is not None:
                        self.fail(
                            "lock holder exited before acquiring: "
                            f"{holder.returncode}"
                        )
                    if time.monotonic() >= deadline:
                        self.fail("lock holder did not acquire the lock")
                    time.sleep(0.02)

                os.killpg(holder.pid, signal.SIGTERM)
                holder.wait(timeout=5)
                holder.communicate()

                with ProcessLock(lock_path, timeout=0):
                    pass
            finally:
                if holder.poll() is None:
                    os.killpg(holder.pid, signal.SIGKILL)
                    holder.wait(timeout=5)
                holder.communicate()

    def test_direct_export_cli_uses_the_shared_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "crawl.lock"
            env = os.environ.copy()
            env["CRAWL_LOCK_PATH"] = str(lock_path)
            env["CRAWL_LOCK_TIMEOUT"] = "0"

            with ProcessLock(lock_path, timeout=0):
                blocked = subprocess.run(
                    [sys.executable, "scraper/export_json.py", "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

            self.assertEqual(blocked.returncode, LOCK_TIMEOUT_EXIT, blocked.stderr)


if __name__ == "__main__":
    unittest.main()
