#!/usr/bin/env python3
"""Start a date-bounded, two-day cart collection window."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_SEMESTERS = frozenset(("spring", "fall", "summer", "winter"))
WINDOW_ID_PATTERN = re.compile(r"^[0-9]{8}$")
COLLECTOR_TIMER_PREFIX = "class-checker.cart@"
CLEANUP_TIMER_PREFIX = "class-checker.cart-cleanup@"


def parse_start_date(value: str) -> dt.date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"start date must be YYYY-MM-DD: {value!r}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"start date must be YYYY-MM-DD: {value!r}") from exc


def validate_year(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}", value):
        raise ValueError(f"year must be a four-digit value: {value!r}")
    return value


def validate_semester(value: str) -> str:
    if value not in VALID_SEMESTERS:
        choices = ", ".join(sorted(VALID_SEMESTERS))
        raise ValueError(f"semester must be one of {choices}: {value!r}")
    return value


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {value!r}") from exc
    return value


def window_id(start: dt.date) -> str:
    return start.strftime("%Y%m%d")


def build_collection_schedule(start: dt.date) -> tuple[dt.datetime, ...]:
    """Return every collection timestamp from D 09:00 through D+1 16:00."""
    first_day = [
        dt.datetime.combine(start, dt.time(9, 0)) + dt.timedelta(minutes=10 * i)
        for i in range(90)
    ]
    next_day = start + dt.timedelta(days=1)
    second_day = [
        dt.datetime.combine(next_day, dt.time(0, 0)) + dt.timedelta(minutes=10 * i)
        for i in range(96)
    ]
    final = dt.datetime.combine(next_day, dt.time(16, 0))
    return tuple(first_day + second_day + [final])


def cleanup_time(start: dt.date) -> dt.datetime:
    return dt.datetime.combine(start + dt.timedelta(days=1), dt.time(16, 10))


def _calendar_timestamp(value: dt.datetime, timezone: str) -> str:
    return f"{value:%Y-%m-%d %H:%M:%S} {timezone}"


def render_timer_unit(
    identifier: str, schedule: tuple[dt.datetime, ...], timezone: str
) -> str:
    lines = [
        "[Unit]",
        "Description=SNU Class Checker bounded cart collection",
        "",
        "[Timer]",
        "Unit=class-checker.cart@%s.service" % identifier,
        "AccuracySec=1s",
        "Persistent=false",
    ]
    lines.extend(f"OnCalendar={_calendar_timestamp(value, timezone)}" for value in schedule)
    lines.extend(("", "[Install]", "WantedBy=timers.target", ""))
    return "\n".join(lines)


def render_cleanup_timer_unit(
    identifier: str, cleanup: dt.datetime, timezone: str
) -> str:
    return "\n".join(
        (
            "[Unit]",
            "Description=SNU Class Checker bounded cart cleanup",
            "",
            "[Timer]",
            f"Unit=class-checker.cart-cleanup@{identifier}.service",
            f"OnCalendar={_calendar_timestamp(cleanup, timezone)}",
            "AccuracySec=1s",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )


def render_window_environment(
    start: dt.date, year: str, semester: str, timezone: str
) -> str:
    end = start + dt.timedelta(days=1)
    return "\n".join(
        (
            f"COUNT_YEAR={year}",
            f"COUNT_SEM={semester}",
            "COUNT_MODE=cart",
            f"COLLECTION_TIMEZONE={timezone}",
            f"CART_WINDOWS={start.isoformat()}..{end.isoformat()}",
            "ENROLL_WINDOWS=",
            "CRAWL_LOCK_TIMEOUT=900",
            "",
        )
    )


def default_unit_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user"


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}."
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        os.chmod(temporary.name, mode)
        os.replace(temporary.name, path)


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
        accepted.add(5)
    if result.returncode not in accepted:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"systemctl {' '.join(arguments)} failed ({result.returncode})"
            + (f": {detail}" if detail else "")
        )
    return result


def _broad_timer_conflict(executable: str) -> bool:
    try:
        active = subprocess.run(
            [executable, "--user", "is-active", "class-checker.update-counts.timer"],
            check=False,
            capture_output=True,
            text=True,
        )
        enabled = subprocess.run(
            [executable, "--user", "is-enabled", "class-checker.update-counts.timer"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"systemctl is unavailable: {executable}") from exc
    return active.stdout.strip() == "active" or enabled.stdout.strip() == "enabled"


def _existing_window_ids(unit_dir: Path) -> set[str]:
    identifiers: set[str] = set()
    for prefix in (COLLECTOR_TIMER_PREFIX, CLEANUP_TIMER_PREFIX):
        for path in unit_dir.glob(f"{prefix}*.timer"):
            name = path.name
            identifier = name[len(prefix) : -len(".timer")]
            if WINDOW_ID_PATTERN.fullmatch(identifier):
                identifiers.add(identifier)
    return identifiers


def install_window(
    start: dt.date,
    year: str,
    semester: str,
    timezone: str,
    *,
    project_root: Path = PROJECT_ROOT,
    unit_dir: Path | None = None,
    systemctl: str = "systemctl",
    no_start: bool = False,
    disable_broad_timer: bool = False,
) -> tuple[str, str]:
    """Render one window and optionally activate its user timers."""
    unit_dir = unit_dir or default_unit_dir()
    identifier = window_id(start)
    existing = _existing_window_ids(unit_dir)
    conflicting = existing - {identifier}
    if conflicting:
        values = ", ".join(sorted(conflicting))
        raise RuntimeError(f"another bounded cart window is installed: {values}")
    if not no_start and not disable_broad_timer and _broad_timer_conflict(systemctl):
        raise RuntimeError(
            "class-checker.update-counts.timer is active or enabled; disable it "
            "or pass --disable-broad-timer before starting a bounded window"
        )

    templates = {
        "class-checker.cart@.service": project_root / "systemd" / "class-checker.cart@.service",
        "class-checker.cart-cleanup@.service": project_root / "systemd" / "class-checker.cart-cleanup@.service",
    }
    for name, source in templates.items():
        if not source.is_file():
            raise RuntimeError(f"missing reusable unit template: {source}")
        _atomic_write(unit_dir / name, source.read_text(encoding="utf-8"), 0o644)

    schedule = build_collection_schedule(start)
    _atomic_write(
        unit_dir / f"{COLLECTOR_TIMER_PREFIX}{identifier}.timer",
        render_timer_unit(identifier, schedule, timezone),
        0o644,
    )
    _atomic_write(
        unit_dir / f"{CLEANUP_TIMER_PREFIX}{identifier}.timer",
        render_cleanup_timer_unit(identifier, cleanup_time(start), timezone),
        0o644,
    )
    _atomic_write(
        unit_dir / f"class-checker.cart-window-{identifier}.env",
        render_window_environment(start, year, semester, timezone),
        0o600,
    )

    collector_timer = f"{COLLECTOR_TIMER_PREFIX}{identifier}.timer"
    cleanup_timer = f"{CLEANUP_TIMER_PREFIX}{identifier}.timer"
    if not no_start:
        _systemctl(systemctl, ["daemon-reload"])
        if disable_broad_timer:
            _systemctl(
                systemctl,
                ["disable", "--now", "class-checker.update-counts.timer"],
                tolerate_unit_missing=True,
            )
        _systemctl(systemctl, ["enable", "--now", collector_timer])
        _systemctl(systemctl, ["enable", "--now", cleanup_timer])
    return collector_timer, cleanup_timer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--semester", required=True)
    parser.add_argument(
        "--timezone",
        default=os.environ.get("COLLECTION_TIMEZONE", "Asia/Seoul"),
    )
    parser.add_argument("--unit-dir", type=Path, default=default_unit_dir())
    parser.add_argument("--systemctl", default=os.environ.get("SYSTEMCTL", "systemctl"))
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="render/install units without enabling or starting timers",
    )
    parser.add_argument(
        "--disable-broad-timer",
        action="store_true",
        help="disable the old broad count timer before starting this window",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        start = parse_start_date(args.start_date)
        validate_year(args.year)
        validate_semester(args.semester)
        validate_timezone(args.timezone)
    except ValueError as exc:
        parser.error(str(exc))
    schedule = build_collection_schedule(start)
    identifier = window_id(start)
    if args.dry_run:
        print(f"window={identifier} collector_runs={len(schedule)}")
        print("collector_schedule:")
        for value in schedule:
            print(f"  {_calendar_timestamp(value, args.timezone)}")
        print(f"cleanup_at={_calendar_timestamp(cleanup_time(start), args.timezone)}")
        return 0
    try:
        collector, cleanup = install_window(
            start,
            args.year,
            args.semester,
            args.timezone,
            unit_dir=args.unit_dir,
            systemctl=args.systemctl,
            no_start=args.no_start,
            disable_broad_timer=args.disable_broad_timer,
        )
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"installed {collector} and {cleanup} in {args.unit_dir}")
    if args.no_start:
        print("timers not started (--no-start)")
    else:
        print("timers enabled and started")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
