from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RefreshLockReexecTests(unittest.TestCase):
    def test_lock_reexec_preserves_cli_arguments(self) -> None:
        """Without the wrapper lock, refresh.sh re-execs itself through
        scraper.process_lock — the original CLI arguments must survive.

        PY=/bin/echo turns the re-exec into a printout of what would run."""
        result = subprocess.run(
            [
                str(ROOT / "refresh.sh"),
                "--year", "2026",
                "--collect", "enrollment",
                "--windowed", "fall",
            ],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "PY": "/bin/echo"},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("process_lock", result.stdout)
        for arg in ("--year 2026", "--collect enrollment", "--windowed", "fall"):
            self.assertIn(arg, result.stdout)


if __name__ == "__main__":
    unittest.main()
