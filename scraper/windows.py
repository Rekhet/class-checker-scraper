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

    A slow window keeps collecting through a weeks-long period (수강취소 runs
    for six weeks) without the 10-minute cadence that period does not need:
    only the run that lands in the opening minutes of an hour samples, so the
    history keeps growing at one point per hour instead of six. The width
    absorbs GitHub's late cron dispatch, which routinely slips several minutes.
    """
    return now_local(now).minute < minutes


# Window lists the publisher consults to decide whether counts should be moving
# right now. The crawler keeps its own legacy single-window fallbacks.
CART_ENV = "CART_WINDOWS"
ENROLL_ENV = "ENROLL_WINDOWS"
ENROLL_SLOW_ENV = "ENROLL_SLOW_WINDOWS"


def collection_active(today: str | None = None) -> dict:
    """What collect.env expects to be collected today.

    Returns {"cart": bool, "enroll": bool, "slow": bool}; "slow" marks the
    hourly-cadence periods (수강취소), which are active days too, just sampled
    six times less often.
    """
    today = today or today_iso()
    return {
        "cart": in_windows(os.environ.get(CART_ENV), today),
        "enroll": in_windows(os.environ.get(ENROLL_ENV), today),
        "slow": in_windows(os.environ.get(ENROLL_SLOW_ENV), today),
    }
