from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PublishScriptTests(unittest.TestCase):
    def test_refresh_script_defaults_to_non_cart_full_collection(self) -> None:
        env = os.environ.copy()
        env.update({
            "DRY_RUN": "1",
            "PY": str(ROOT / ".venv/bin/python"),
        })
        result = subprocess.run(
            ["bash", str(ROOT / "refresh.sh"), "--year", "2026", "fall"],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "--collect catalog,enrollment,grading",
            result.stdout,
        )

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
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"diff\" ] && "
                "[ \"$4\" = \"--cached\" ] && [ \"$5\" = \"--quiet\" ]; then\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-list\" ]; then\n"
                "  printf '1\\n'\n"
                "elif [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-parse\" ]; then\n"
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
                    "PUBLISH_PUSH": "1",
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
            self.assertNotIn(" push", git_log.read_text(encoding="utf-8"))

    def test_full_publisher_retains_hourly_push(self) -> None:
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
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"diff\" ] && "
                "[ \"$4\" = \"--cached\" ] && [ \"$5\" = \"--quiet\" ]; then\n"
                "  exit 1\n"
                "fi\n"
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-list\" ]; then\n"
                "  printf '1\\n'\n"
                "elif [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-parse\" ]; then\n"
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
                    "PUBLISH_PUSH": "1",
                    "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
                    "PY": str(fake_python),
                }
            )
            result = subprocess.run(
                ["bash", str(scripts / "publish.sh"), "full"],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("push", git_log.read_text(encoding="utf-8"))

    def test_full_publisher_pushes_existing_local_commit_without_new_staged_changes(self) -> None:
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
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"diff\" ] && "
                "[ \"$4\" = \"--cached\" ] && [ \"$5\" = \"--quiet\" ]; then\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-parse\" ] && "
                "[ \"$4\" = \"--show-toplevel\" ]; then\n"
                "  printf '%s\\n' \"$2\"\n"
                "elif [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-parse\" ]; then\n"
                "  printf 'origin/main\\n'\n"
                "elif [ \"$1\" = \"-C\" ] && [ \"$3\" = \"rev-list\" ]; then\n"
                "  printf '1\\n'\n"
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
                    "PUBLISH_PUSH": "1",
                    "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
                    "PY": str(fake_python),
                }
            )
            result = subprocess.run(
                ["bash", str(scripts / "publish.sh"), "full"],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = git_log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(call.endswith("rev-list --count @{upstream}..HEAD") for call in calls))
            self.assertTrue(any(call.endswith("push") for call in calls))

    def test_counts_wrapper_forces_commit_only_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            scripts = project / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/update-counts.sh", scripts / "update-counts.sh")
            (project / "refresh.sh").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8"
            )
            (project / "refresh.sh").chmod(0o755)
            publish_log = project / "publish.log"
            publish = scripts / "publish.sh"
            publish.write_text(
                "#!/bin/sh\n"
                "printf '%s|%s\\n' \"${PUBLISH_PUSH-unset}\" \"$*\" "
                ">> \"$PUBLISH_LOG\"\n",
                encoding="utf-8",
            )
            publish.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
                    "COUNT_YEAR": "2099",
                    "COUNT_SEM": "fall",
                    "COUNT_MODE": "cart",
                    "PUBLISH_PUSH": "1",
                    "PUBLISH_LOG": str(publish_log),
                }
            )
            result = subprocess.run(
                ["bash", str(scripts / "update-counts.sh")],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(publish_log.read_text(encoding="utf-8").strip(), "0|counts")

    def _full_update_sandbox(self, tmp: str, *, remote_env: bool):
        """A throwaway project tree for scripts/update.sh: stub make/python and
        a publish.sh that only records how it was called."""
        project = Path(tmp)
        scripts = project / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts/update.sh", scripts / "update.sh")
        shutil.copy2(ROOT / "scripts/publish.sh", scripts / "publish.sh")
        (project / "collect.env").write_text(
            "COUNT_YEAR=2099\nCOUNT_SEM=spring\n", encoding="utf-8"
        )
        if remote_env:
            (project / "turso-remote.env").write_text(
                "TURSO_DATABASE_URL=libsql://example.turso.io\n"
                "TURSO_AUTH_TOKEN=stub\n", encoding="utf-8"
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
        env.pop("UPDATE_CRAWL", None)

        def run():
            return subprocess.run(
                ["bash", str(scripts / "update.sh")],
                cwd=project,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

        return run, env, make_log

    def test_full_update_publishes_cloud_counts_without_crawling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run, _env, make_log = self._full_update_sandbox(tmp, remote_env=True)

            result = run()

            self.assertEqual(result.returncode, 0, result.stderr)
            # the 인원 pass runs on GitHub-hosted runners; the scheduled local
            # run merges and publishes, it does not crawl sugang
            self.assertFalse(make_log.exists(), make_log.read_text()
                             if make_log.exists() else "")

    def test_full_update_publishes_even_without_a_counts_source(self) -> None:
        # A pull that cannot run must never withhold publication: local workers
        # may have collected this hour, and pending trend commits in web/ are
        # pushed by this run alone.
        with tempfile.TemporaryDirectory() as tmp:
            run, _env, _make_log = self._full_update_sandbox(tmp, remote_env=False)

            result = run()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("turso-remote.env is missing", result.stderr)
            self.assertIn("publishing whatever is already local", result.stderr)

    def test_full_update_crawl_opt_in_reads_canonical_semester(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run, env, make_log = self._full_update_sandbox(tmp, remote_env=True)
            env["UPDATE_CRAWL"] = "1"

            result = run()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(make_log.read_text(encoding="utf-8").strip(),
                             "refresh YEAR=2099 SEM=spring "
                             "COLLECT=catalog,enrollment,grading")

            env["UPDATE_COLLECTIONS"] = "CATALOG,CART"
            uppercase_blocked = run()
            self.assertEqual(uppercase_blocked.returncode, 2)
            self.assertIn("cannot collect cart", uppercase_blocked.stderr)

            env["UPDATE_COLLECTIONS"] = "cart"
            blocked = run()
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("cannot collect cart", blocked.stderr)
            self.assertEqual(make_log.read_text(encoding="utf-8").strip(),
                             "refresh YEAR=2099 SEM=spring "
                             "COLLECT=catalog,enrollment,grading")

    def test_cart_wrapper_dry_run_selects_only_cart(self) -> None:
        env = os.environ.copy()
        env.update({
            "CLASS_CHECKER_PROCESS_LOCK_HELD": "1",
            "DRY_RUN": "1",
            "COUNT_YEAR": "2099",
            "COUNT_SEM": "fall",
            "COUNT_MODE": "cart",
        })
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/update-counts.sh")],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--collect cart --windowed fall", result.stdout)

    def test_admin_full_refresh_explicitly_excludes_cart(self) -> None:
        import scraper.server as server

        class FakeConnection:
            def close(self) -> None:
                pass

        with patch.object(server.db, "connect", return_value=FakeConnection()), \
             patch.object(server.db, "init_schema"), \
             patch.object(server.process_lock, "ProcessLock",
                          return_value=nullcontext()), \
             patch.object(server.crawl, "refresh_all", return_value={}) as refresh:
            server._run_refresh(["2026"], ["fall"])

        kwargs = refresh.call_args.kwargs
        self.assertFalse(kwargs["collect_cart"])
        self.assertTrue(kwargs["collect_enrollment"])
        self.assertTrue(kwargs["collect_grading"])


if __name__ == "__main__":
    unittest.main()
