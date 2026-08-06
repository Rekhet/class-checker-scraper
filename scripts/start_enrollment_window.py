#!/usr/bin/env python3
"""Install a bounded, multi-date enrollment-count collection window."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

try:
    from .start_cart_window import (
        _atomic_write,
        _broad_timer_conflict,
        _calendar_timestamp,
        _systemctl,
        default_unit_dir,
        parse_start_date,
        validate_semester,
        validate_timezone,
        validate_year,
    )
except ImportError:  # pragma: no cover - direct script execution
    from start_cart_window import (  # type: ignore[no-redef]
        _atomic_write,
        _broad_timer_conflict,
        _calendar_timestamp,
        _systemctl,
        default_unit_dir,
        parse_start_date,
        validate_semester,
        validate_timezone,
        validate_year,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOW_ID_PATTERN = re.compile(r"^[0-9]{8}(?:-[0-9]{8})+$")
COLLECTOR_TIMER_PREFIX = "class-checker.enrollment@"
CLEANUP_TIMER_PREFIX = "class-checker.enrollment-cleanup@"
DEFAULT_BURST_MINUTES = 30
DEFAULT_BURST_INTERVAL_MINUTES = 5
DEFAULT_INTERVAL_MINUTES = 10


def parse_dates(value: str) -> tuple[dt.date, ...]:
    """Parse an ascending, comma-separated list of distinct ISO dates."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        raise ValueError("at least one collection date is required")
    dates = tuple(parse_start_date(part) for part in parts)
    if len(set(dates)) != len(dates):
        raise ValueError("collection dates must not repeat")
    if dates != tuple(sorted(dates)):
        raise ValueError("collection dates must be in ascending order")
    return dates


def parse_time(value: str) -> dt.time:
    """Parse a five-minute-aligned local time in HH:MM form."""
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError(f"time must be HH:MM: {value!r}")
    try:
        parsed = dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"time must be HH:MM: {value!r}") from exc
    if parsed.minute % 5:
        raise ValueError(f"time must fall on a five-minute boundary: {value!r}")
    return parsed


def build_collection_schedule(
    dates: tuple[dt.date, ...],
    start_time: dt.time,
    end_time: dt.time,
    *,
    burst_minutes: int = DEFAULT_BURST_MINUTES,
    burst_interval_minutes: int = DEFAULT_BURST_INTERVAL_MINUTES,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> tuple[dt.datetime, ...]:
    """Return the inclusive burst-then-regular collection schedule for each date."""
    if not dates:
        raise ValueError("at least one collection date is required")
    if start_time > end_time:
        raise ValueError("collection start time must not be after its end time")
    if burst_minutes < 0:
        raise ValueError("initial burst duration must not be negative")
    if burst_interval_minutes <= 0:
        raise ValueError("initial burst interval must be positive")
    if interval_minutes <= 0:
        raise ValueError("collection interval must be positive")
    if burst_minutes % burst_interval_minutes:
        raise ValueError(
            "initial burst duration must be divisible by its interval"
        )
    span_seconds = (
        dt.datetime.combine(dt.date.min, end_time)
        - dt.datetime.combine(dt.date.min, start_time)
    ).total_seconds()
    if span_seconds % 60:
        raise ValueError("collection times must be aligned to whole minutes")
    span_minutes = int(span_seconds // 60)
    burst_end_offset = min(burst_minutes, span_minutes)
    if burst_end_offset % burst_interval_minutes:
        raise ValueError(
            "collection window must end on an initial burst interval"
        )
    regular_start_offset = burst_end_offset + interval_minutes
    if burst_end_offset < span_minutes and (
        regular_start_offset > span_minutes
        or (span_minutes - regular_start_offset) % interval_minutes
    ):
        raise ValueError(
            "collection window must end on a regular collection interval"
        )

    schedule: list[dt.datetime] = []
    for day in dates:
        current = dt.datetime.combine(day, start_time)
        for offset in range(
            0, burst_end_offset + 1, burst_interval_minutes
        ):
            schedule.append(current + dt.timedelta(minutes=offset))
        if burst_end_offset < span_minutes:
            for offset in range(
                regular_start_offset, span_minutes + 1, interval_minutes
            ):
                schedule.append(current + dt.timedelta(minutes=offset))
    return tuple(schedule)


def cleanup_time(last_date: dt.date, end_time: dt.time) -> dt.datetime:
    """Run cleanup ten minutes after the final collection on the last date."""
    return dt.datetime.combine(last_date, end_time) + dt.timedelta(minutes=10)


def window_id(dates: tuple[dt.date, ...]) -> str:
    return "-".join(day.strftime("%Y%m%d") for day in dates)


def render_timer_unit(
    identifier: str, schedule: tuple[dt.datetime, ...], timezone: str
) -> str:
    lines = [
        "[Unit]",
        "Description=SNU Class Checker bounded enrollment collection",
        "",
        "[Timer]",
        f"Unit={COLLECTOR_TIMER_PREFIX}{identifier}.service",
        "AccuracySec=1s",
        "Persistent=false",
    ]
    lines.extend(
        f"OnCalendar={_calendar_timestamp(value, timezone)}" for value in schedule
    )
    lines.extend(("", "[Install]", "WantedBy=timers.target", ""))
    return "\n".join(lines)


def render_cleanup_timer_unit(
    identifier: str, cleanup: dt.datetime, timezone: str
) -> str:
    return "\n".join(
        (
            "[Unit]",
            "Description=SNU Class Checker bounded enrollment cleanup",
            "",
            "[Timer]",
            f"Unit={CLEANUP_TIMER_PREFIX}{identifier}.service",
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
    dates: tuple[dt.date, ...], year: str, semester: str, timezone: str
) -> str:
    enrollment_windows = ",".join(day.isoformat() for day in dates)
    return "\n".join(
        (
            f"COUNT_YEAR={year}",
            f"COUNT_SEM={semester}",
            "COUNT_MODE=enrollment",
            f"COLLECTION_TIMEZONE={timezone}",
            "CART_WINDOWS=",
            f"ENROLL_WINDOWS={enrollment_windows}",
            "CRAWL_LOCK_TIMEOUT=900",
            "",
        )
    )


def _existing_window_ids(unit_dir: Path) -> set[str]:
    identifiers: set[str] = set()
    for prefix in (COLLECTOR_TIMER_PREFIX, CLEANUP_TIMER_PREFIX):
        for path in unit_dir.glob(f"{prefix}*.timer"):
            identifier = path.name[len(prefix) : -len(".timer")]
            if WINDOW_ID_PATTERN.fullmatch(identifier):
                identifiers.add(identifier)
    return identifiers


def install_window(
    dates: tuple[dt.date, ...],
    start_time: dt.time,
    end_time: dt.time,
    year: str,
    semester: str,
    timezone: str,
    *,
    project_root: Path = PROJECT_ROOT,
    unit_dir: Path | None = None,
    systemctl: str = "systemctl",
    no_start: bool = False,
    disable_broad_timer: bool = False,
    burst_minutes: int = DEFAULT_BURST_MINUTES,
    burst_interval_minutes: int = DEFAULT_BURST_INTERVAL_MINUTES,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> tuple[str, str]:
    """Render one enrollment window and optionally activate its user timers."""
    unit_dir = unit_dir or default_unit_dir()
    identifier = window_id(dates)
    existing = _existing_window_ids(unit_dir)
    conflicting = existing - {identifier}
    if conflicting:
        values = ", ".join(sorted(conflicting))
        raise RuntimeError(f"another bounded enrollment window is installed: {values}")
    if not no_start and not disable_broad_timer and _broad_timer_conflict(systemctl):
        raise RuntimeError(
            "class-checker.update-counts.timer is active or enabled; disable it "
            "or pass --disable-broad-timer before starting a bounded window"
        )

    templates = {
        "class-checker.enrollment@.service": project_root
        / "systemd"
        / "class-checker.enrollment@.service",
        "class-checker.enrollment-cleanup@.service": project_root
        / "systemd"
        / "class-checker.enrollment-cleanup@.service",
    }
    for name, source in templates.items():
        if not source.is_file():
            raise RuntimeError(f"missing reusable unit template: {source}")
        _atomic_write(unit_dir / name, source.read_text(encoding="utf-8"), 0o644)

    schedule = build_collection_schedule(
        dates,
        start_time,
        end_time,
        burst_minutes=burst_minutes,
        burst_interval_minutes=burst_interval_minutes,
        interval_minutes=interval_minutes,
    )
    _atomic_write(
        unit_dir / f"{COLLECTOR_TIMER_PREFIX}{identifier}.timer",
        render_timer_unit(identifier, schedule, timezone),
        0o644,
    )
    _atomic_write(
        unit_dir / f"{CLEANUP_TIMER_PREFIX}{identifier}.timer",
        render_cleanup_timer_unit(
            identifier, cleanup_time(dates[-1], end_time), timezone
        ),
        0o644,
    )
    _atomic_write(
        unit_dir / f"class-checker.enrollment-window-{identifier}.env",
        render_window_environment(dates, year, semester, timezone),
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
    parser.add_argument(
        "--dates",
        required=True,
        help="ascending comma-separated YYYY-MM-DD dates",
    )
    parser.add_argument("--start-time", default="08:30")
    parser.add_argument("--end-time", default="16:30")
    parser.add_argument(
        "--burst-minutes",
        type=int,
        default=DEFAULT_BURST_MINUTES,
        help="duration of the initial high-frequency collection burst (default: 30)",
    )
    parser.add_argument(
        "--burst-interval",
        type=int,
        default=DEFAULT_BURST_INTERVAL_MINUTES,
        help="minutes between initial burst collections (default: 5)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MINUTES,
        help="minutes between collections after the initial burst (default: 10)",
    )
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
        dates = parse_dates(args.dates)
        start_time = parse_time(args.start_time)
        end_time = parse_time(args.end_time)
        validate_year(args.year)
        validate_semester(args.semester)
        validate_timezone(args.timezone)
        schedule = build_collection_schedule(
            dates,
            start_time,
            end_time,
            burst_minutes=args.burst_minutes,
            burst_interval_minutes=args.burst_interval,
            interval_minutes=args.interval,
        )
    except ValueError as exc:
        parser.error(str(exc))

    identifier = window_id(dates)
    cleanup = cleanup_time(dates[-1], end_time)
    if args.dry_run:
        print(f"window={identifier} collector_runs={len(schedule)}")
        print("collector_schedule:")
        for value in schedule:
            print(f"  {_calendar_timestamp(value, args.timezone)}")
        print(f"cleanup_at={_calendar_timestamp(cleanup, args.timezone)}")
        return 0

    try:
        collector, cleanup_timer = install_window(
            dates,
            start_time,
            end_time,
            args.year,
            args.semester,
            args.timezone,
            unit_dir=args.unit_dir,
            systemctl=args.systemctl,
            no_start=args.no_start,
            disable_broad_timer=args.disable_broad_timer,
            burst_minutes=args.burst_minutes,
            burst_interval_minutes=args.burst_interval,
            interval_minutes=args.interval,
        )
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"installed {collector} and {cleanup_timer} in {args.unit_dir}")
    if args.no_start:
        print("timers not started (--no-start)")
    else:
        print("timers enabled and started")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
