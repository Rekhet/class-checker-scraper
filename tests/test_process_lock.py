from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scraper.process_lock import LOCK_HELD_ENV, LOCK_TIMEOUT_EXIT, ProcessLock


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
