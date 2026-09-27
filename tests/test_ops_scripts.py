from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


class BackupScriptTests(unittest.TestCase):
    def test_keeps_the_newest_archives_and_they_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "turso.db"
            conn = sqlite3.connect(src)
            conn.execute("CREATE TABLE count_samples (ts TEXT, v INTEGER)")
            conn.executemany("INSERT INTO count_samples VALUES (?, ?)",
                             [(f"t{i}", i) for i in range(500)])
            conn.commit()
            conn.close()
            env = dict(os.environ, BACKUP_SRC=str(src), BACKUP_DIR=str(tmp / "out"),
                       BACKUP_KEEP="2")
            for i in range(3):
                # distinct timestamps without sleeping: fake `date`
                bindir = tmp / f"bin{i}"
                bindir.mkdir()
                _stub(bindir, "date", f'echo 20260927-00000{i}\n')
                subprocess.run([str(ROOT / "scripts/backup-db.sh")], check=True,
                               env=dict(env, PATH=f"{bindir}:{env['PATH']}"),
                               capture_output=True)

            archives = sorted((tmp / "out").glob("turso-*.db.xz"))
            self.assertEqual([a.name for a in archives],
                             ["turso-20260927-000001.db.xz", "turso-20260927-000002.db.xz"])
            self.assertEqual(list((tmp / "out").glob(".snapshot-*")), [])
            restored = tmp / "restored.db"
            restored.write_bytes(subprocess.run(["xz", "-dc", str(archives[-1])],
                                                check=True, capture_output=True).stdout)
            rows = sqlite3.connect(restored).execute(
                "SELECT COUNT(*) FROM count_samples").fetchone()[0]
            self.assertEqual(rows, 500)


class NotifyFailureTests(unittest.TestCase):
    def _run(self, tmp: Path, open_issue: str = "") -> tuple[str, Path]:
        bindir = tmp / "bin"
        bindir.mkdir(exist_ok=True)
        calls = tmp / "gh-calls"
        _stub(bindir, "journalctl", "echo 'SECRET-LOOKING LOG LINE token=abc'\n")
        _stub(bindir, "systemctl", 'case "$*" in *Result*) echo exit-code;; *) echo 1;; esac\n')
        _stub(bindir, "gh", f'printf "%s\\n---\\n" "$*" >> "{calls}"\n'
                            f'case "$1 $2" in "issue list") echo "{open_issue}";; esac\n')
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                   ALERT_LOG_DIR=str(tmp / "logs"))
        out = subprocess.run([str(ROOT / "scripts/notify-failure.sh"),
                              "class-checker.update.service"],
                             check=True, env=env, capture_output=True, text=True).stdout
        return calls.read_text(), out

    def test_opens_an_issue_without_log_text_and_keeps_the_log_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            calls, _out = self._run(tmp)
            self.assertIn("issue create", calls)
            self.assertIn("ops-alert: class-checker.update.service failed", calls)
            self.assertIn("exit-code", calls)
            self.assertNotIn("SECRET-LOOKING", calls)          # no log text leaves
            logs = list((tmp / "logs").glob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("SECRET-LOOKING", logs[0].read_text())
            self.assertEqual(logs[0].stat().st_mode & 0o077, 0)

    def test_comments_on_the_open_issue_for_the_same_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls, _out = self._run(Path(tmp), open_issue="42")
            self.assertIn("issue comment 42", calls)
            self.assertNotIn("issue create", calls)


if __name__ == "__main__":
    unittest.main()
