"""Collection-window arithmetic for the 인원 추이 history.

Split out of `crawl` so that anything needing to ask "should counts be moving
right now?" — the publisher's freshness check, for instance — can do so without
importing the crawler and, through it, Playwright.

Windows are configured in `collect.env` as comma-separated lists of
``YYYY-MM-DD`` days or ``YYYY-MM-DD..YYYY-MM-DD`` inclusive ranges, evaluated in
``COLLECTION_TIMEZONE``. The crawler owns the environment-variable names; this
module only does the date and clock arithmetic.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def now_local(now: datetime | None = None) -> datetime:
    """`now` (default: this instant) in COLLECTION_TIMEZONE, or unchanged when
    no timezone is configured. Naive input is read as UTC."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timezone_name = (os.environ.get("COLLECTION_TIMEZONE") or "").strip()
    if not timezone_name:
        return now
    try:
        return now.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown COLLECTION_TIMEZONE: {timezone_name}") from exc


def today_iso(now: datetime | None = None) -> str:
    """Today's date in the configured collection timezone.

    The crawler runs on a host whose process timezone may not match the SNU
    collection schedule. An explicit ``COLLECTION_TIMEZONE`` keeps the boundary
    at midnight in the schedule's timezone. With no setting, preserve the
    historical host-local behavior.
    """
    supplied_now = now is not None
    if not (os.environ.get("COLLECTION_TIMEZONE") or "").strip():
        if supplied_now:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return now.date().isoformat()
        return date.today().isoformat()
    return now_local(now).date().isoformat()


def in_window(start: str | None, end: str | None, today: str) -> bool:
    """True if `today` is within [start, end]; a blank bound means 'always'."""
    s = (start or "").strip()
    e = (end or "").strip()
    if s and today < s:
        return False
    if e and today > e:
        return False
    return True


def in_windows(spec: str | None, today: str) -> bool:
    """True if `today` falls in ANY window of the list. Lets one metric be
    sampled across several disjoint periods — 예비/본 수강신청, 개강전·개강후
    변경, 수강취소 — without collecting through the dead gaps between them."""
    for w in (spec or "").split(","):
        w = w.strip()
        if not w:
            continue
        s, _, e = w.partition("..")
        s = s.strip()
        e = e.strip() or s
        if (not s or today >= s) and (not e or today <= e):
            return True
    return False


def hour_slot_open(now: datetime | None = None, minutes: int = 10) -> bool:
    """True during the first `minutes` of an hour.

    Thins a slow window's cadence: only the run that lands in the opening
    minutes of an hour samples. The width absorbs GitHub's late cron dispatch,
    which routinely slips several minutes.

    collect.env sets ENROLL_SLOW_SLOT_MINUTES to 60, so in practice the gate is
    open all hour and 수강취소 is sampled at the full 10-minute cadence; see
    crawl._slow_enroll_open() for why thinning is not worth its cost under
    delta storage. The mechanism is kept for a period that does need it.
    """
    return now_local(now).minute < minutes


# Window lists the publisher consults to decide whether counts should be moving
# right now. The crawler keeps its own legacy single-window fallbacks.
CART_ENV = "CART_WINDOWS"
ENROLL_ENV = "ENROLL_WINDOWS"
ENROLL_SLOW_ENV = "ENROLL_SLOW_WINDOWS"


def slow_slot_minutes() -> int:
    """ENROLL_SLOW_SLOT_MINUTES clamped to 1..60; unset or invalid means 60,
    the gate open all hour (see crawl._slow_enroll_open)."""
    raw = (os.environ.get("ENROLL_SLOW_SLOT_MINUTES") or "").strip()
    try:
        value = int(raw) if raw else 60
    except ValueError:
        value = 60
    return max(1, min(60, value))


def enrollment_pass_due(now: datetime | None = None) -> bool:
    """Would a windowed enrollment-only pass collect right now?

    The same decision crawl._window_active(collect_cart=False) makes, without
    importing the crawler: the GitHub runner asks this BEFORE installing
    dependencies and a browser, so the off-season runs that make up most of
    the year cost a checkout and nothing else. tests/test_windows.py keeps the
    two answers identical.
    """
    today = today_iso(now)
    spec = (os.environ.get(ENROLL_ENV) or "").strip()
    if spec:
        enrolled = in_windows(spec, today)
    else:   # the crawler's legacy single-window fallback
        enrolled = in_window(os.environ.get("ENROLL_START"),
                             os.environ.get("ENROLL_END"), today)
    if enrolled:
        return True
    slow = (os.environ.get(ENROLL_SLOW_ENV) or "").strip()
    return bool(slow) and in_windows(slow, today) and hour_slot_open(
        now, slow_slot_minutes())


def collection_active(today: str | None = None) -> dict:
    """What collect.env expects to be collected today.

    Returns {"cart": bool, "enroll": bool, "slow": bool}; "slow" marks the
    periods declared slow-moving (수강취소), which are ordinary active days —
    with ENROLL_SLOW_SLOT_MINUTES at its default they are sampled at the same
    10-minute cadence as the rest.
    """
    today = today or today_iso()
    return {
        "cart": in_windows(os.environ.get(CART_ENV), today),
        "enroll": in_windows(os.environ.get(ENROLL_ENV), today),
        "slow": in_windows(os.environ.get(ENROLL_SLOW_ENV), today),
    }


def main(argv: list[str] | None = None) -> int:
    """``python3 scraper/windows.py gate``: print (and, on a GitHub runner,
    export as a step output) whether an enrollment pass is due right now."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if args != ["gate"]:
        print("usage: windows.py gate", file=sys.stderr)
        return 2
    active = enrollment_pass_due()
    line = f"active={'true' if active else 'false'}"
    print(line)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
