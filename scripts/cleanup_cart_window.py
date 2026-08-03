#!/usr/bin/env python3
"""Stop and remove one bounded cart window after its final collection."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scraper"))
import process_lock  # noqa: E402


WINDOW_ID_PATTERN = re.compile(r"^[0-9]{8}$")
UNIT_NOT_FOUND = 5


def default_unit_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user"


def _systemctl(
    executable: str, arguments: list[str], *, tolerate_unit_missing: bool = False
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [executable, "--user", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    accepted = {0}
    if tolerate_unit_missing:
        accepted.add(UNIT_NOT_FOUND)
    if result.returncode not in accepted:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"systemctl {' '.join(arguments)} failed ({result.returncode})"
            + (f": {detail}" if detail else "")
        )
    return result


def _service_state(executable: str, service: str) -> str:
    result = _systemctl(
        executable,
        ["show", service, "-p", "ActiveState", "--value"],
        tolerate_unit_missing=True,
    )
    return result.stdout.strip()


def wait_for_service_inactive(
    executable: str,
    service: str,
    timeout: float,
    poll_interval: float,
) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    active_states = {"activating", "active", "deactivating", "reloading"}
    while True:
        if _service_state(executable, service) not in active_states:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"collector service remained active: {service}")
        time.sleep(min(max(0.01, poll_interval), max(0.01, deadline - time.monotonic())))


def wait_for_lock(
    lock_path: Path | None, timeout: float, poll_interval: float
) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            with process_lock.ProcessLock(lock_path, timeout=0):
                return
        except process_lock.LockTimeout:
            if time.monotonic() >= deadline:
                lock_name = lock_path or process_lock.DEFAULT_LOCK_PATH
                raise TimeoutError(f"shared process lock remained busy: {lock_name}")
            time.sleep(min(max(0.01, poll_interval), max(0.01, deadline - time.monotonic())))


def _window_paths(unit_dir: Path, identifier: str) -> tuple[Path, Path, Path]:
    if not WINDOW_ID_PATTERN.fullmatch(identifier):
        raise ValueError(f"unsafe window id: {identifier!r}")
    return (
        unit_dir / f"class-checker.cart@{identifier}.timer",
        unit_dir / f"class-checker.cart-cleanup@{identifier}.timer",
        unit_dir / f"class-checker.cart-window-{identifier}.env",
    )


def cleanup_window(
    identifier: str,
    *,
    unit_dir: Path,
    systemctl: str,
    lock_path: Path | None,
    wait_timeout: float,
    poll_interval: float,
) -> int:
    collector_timer = f"class-checker.cart@{identifier}.timer"
    collector_service = f"class-checker.cart@{identifier}.service"
    cleanup_timer = f"class-checker.cart-cleanup@{identifier}.timer"
    timer_path, cleanup_path, env_path = _window_paths(unit_dir, identifier)

    # Stop only future timer activations. Never stop the collector service: the
    # final 16:00 run must be allowed to finish naturally.
    _systemctl(systemctl, ["stop", collector_timer], tolerate_unit_missing=True)
    try:
        wait_for_service_inactive(
            systemctl, collector_service, wait_timeout, poll_interval
        )
        wait_for_lock(lock_path, wait_timeout, poll_interval)
    except TimeoutError as exc:
        print(f"cleanup deferred: {exc}", file=sys.stderr)
        return 1

    _systemctl(
        systemctl, ["disable", "--now", collector_timer], tolerate_unit_missing=True
    )
    _systemctl(
        systemctl, ["disable", "--now", cleanup_timer], tolerate_unit_missing=True
    )
    for path in (timer_path, cleanup_path, env_path):
        path.unlink(missing_ok=True)
    _systemctl(systemctl, ["daemon-reload"])
    print(f"cleaned bounded cart window {identifier}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--unit-dir", type=Path, default=default_unit_dir())
    parser.add_argument("--systemctl", default=os.environ.get("SYSTEMCTL", "systemctl"))
    parser.add_argument("--lock-path", type=Path, default=None)
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return cleanup_window(
            args.window_id,
            unit_dir=args.unit_dir,
            systemctl=args.systemctl,
            lock_path=args.lock_path,
            wait_timeout=args.wait_timeout,
            poll_interval=args.poll_interval,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
