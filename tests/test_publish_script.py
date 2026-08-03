from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PublishScriptTests(unittest.TestCase):
    def test_counts_publisher_targets_the_web_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            scripts = project / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/publish.sh", scripts / "publish.sh")
            (project / "web").mkdir()

            fake_bin = project / "bin"
            fake_bin.mkdir()
            git_log = project / "git.log"
            _executable(
                fake_bin / "git",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$PUBLISH_GIT_LOG\"\n"
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-parse\" ]; then\n"
                "  printf '%s\\n' \"$2\"\n"
                "elif [ \"$1\" = \"rev-parse\" ]; then\n"
                "  pwd\n"
                "fi\n"
                "exit 0\n",
            )
            fake_python = fake_bin / "python"
            _executable(fake_python, "#!/bin/sh\nexit 0\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PUBLISH_GIT_LOG": str(git_log),
                    "PUBLISH_GIT": "1",
                    "PUBLISH_PUSH": "0",
                    "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
                    "PY": str(fake_python),
                    "YEAR": "2026",
                    "SEM": "fall",
                }
            )
            result = subprocess.run(
                ["bash", str(scripts / "publish.sh"), "counts"],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = git_log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(calls)
            expected_root = str(project / "web")
            self.assertTrue(all(f"-C {expected_root}" in call for call in calls))
            self.assertNotIn("web/data/", git_log.read_text(encoding="utf-8"))

    def test_full_update_reads_canonical_semester(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            scripts = project / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/update.sh", scripts / "update.sh")
            shutil.copy2(ROOT / "scripts/publish.sh", scripts / "publish.sh")
            (project / "collect.env").write_text(
                "COUNT_YEAR=2099\nCOUNT_SEM=spring\n", encoding="utf-8"
            )

            fake_bin = project / "bin"
            fake_bin.mkdir()
            make_log = project / "make.log"
            _executable(
                fake_bin / "make",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" > \"$MAKE_LOG\"\n",
            )
            fake_python = fake_bin / "python"
            _executable(fake_python, "#!/bin/sh\nexit 0\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "MAKE_LOG": str(make_log),
                    "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
                    "PUBLISH_GIT": "0",
                    "PY": str(fake_python),
                }
            )
            result = subprocess.run(
                ["bash", str(scripts / "update.sh")],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(make_log.read_text(encoding="utf-8").strip(),
                             "refresh YEAR=2099 SEM=spring "
                             "COLLECT=catalog,enrollment,grading")


if __name__ == "__main__":
    unittest.main()
