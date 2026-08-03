"""A host-local, cross-process lock for every DB/export writer.

The web server and scheduled shell jobs are separate processes, so a
``threading.Lock`` cannot protect the database between them.  This module uses
the same advisory ``flock(2)`` lock file from Python and from the shell entry
points (via its command wrapper).
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path


LOCK_HELD_ENV = "CLASS_CHECKER_PROCESS_LOCK_HELD"
LOCK_TIMEOUT_EXIT = 75  # EX_TEMPFAIL: retryable contention for a timer job
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = PROJECT_ROOT / "data" / ".crawl.lock"
DEFAULT_TIMEOUT = float(os.environ.get("CRAWL_LOCK_TIMEOUT", "900"))


class LockTimeout(TimeoutError):
    """The lock could not be acquired within the configured wait period."""


def lock_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Return the shared lock path, honoring the deployment override."""
    value = path or os.environ.get("CRAWL_LOCK_PATH")
    return Path(value) if value else DEFAULT_LOCK_PATH


class ProcessLock:
    """Exclusive advisory lock shared by all class-checker writer processes."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.2,
    ) -> None:
        self.path = lock_path(path)
        self.timeout = DEFAULT_TIMEOUT if timeout is None else max(0.0, timeout)
        self.poll_interval = max(0.01, poll_interval)
        self._file = None

    def acquire(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+")
        started = time.monotonic()
        try:
            while True:
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    elapsed = time.monotonic() - started
                    if self.timeout == 0 or elapsed >= self.timeout:
                        raise LockTimeout(str(self.path)) from exc
                    time.sleep(min(self.poll_interval, self.timeout - elapsed))
        except Exception:
            self._close()
            raise

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._close()

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "ProcessLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


def run_locked(command: list[str], *, path=None, timeout=None) -> int:
    """Run a command while holding the shared lock.

    The environment marker prevents a nested entry point (for example,
    ``update.sh`` calling ``refresh.sh``) from trying to acquire the same lock
    again in a child process.
    """
    if not command:
        raise ValueError("a command is required")
    env = os.environ.copy()
    env[LOCK_HELD_ENV] = "1"
    with ProcessLock(path, timeout=timeout):
        return subprocess.run(command, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None, help="shared lock file")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"seconds to wait (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("a command is required after --")
    try:
        return run_locked(command, path=args.path, timeout=args.timeout)
    except LockTimeout as exc:
        print(f"process lock busy: {exc}", file=sys.stderr)
        return LOCK_TIMEOUT_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
