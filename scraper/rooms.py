"""Room names in the catalog: campus, building, normalized name.

The Excel's 강의실 value carries the campus as a prefix (header legend
`강의실(동-호)(#연건, *평창)`); the 2020 terms and 2021 여름학기 zero-pad
segments (`001-101`) that every later term writes `1-101`. Normalizing both
makes one room one key across terms. The building is an INFERENCE used only
for grouping (spec docs/superpowers/specs/2026-10-09-rooms-page-design.md);
web/app.js `normRoom` is the JavaScript twin, and both are tested against
tests/fixtures/room_names.json.
"""
from __future__ import annotations

import re
from typing import NamedTuple

CAMPUS_MARK = {"#": "연건", "*": "평창"}
DEFAULT_CAMPUS = "관악"
_DIGITS = re.compile(r"[0-9]+")
_SUB_BUILDING = re.compile(r"[0-9]{1,2}")


class Room(NamedTuple):
    campus: str
    building: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.campus}|{self.name}"


def normalize_room(raw: str | None) -> Room | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    campus = CAMPUS_MARK.get(raw[0], DEFAULT_CAMPUS)
    body = raw[1:] if raw[0] in CAMPUS_MARK else raw
    parts = [(p.lstrip("0") or "0") if _DIGITS.fullmatch(p) else p
             for p in body.split("-") if p]
    if not parts:
        return None
    if len(parts) >= 3 and _SUB_BUILDING.fullmatch(parts[1]):
        building = f"{parts[0]}-{parts[1]}"
    else:
        building = parts[0]
    return Room(campus, building, "-".join(parts))


def building_rank(building: str) -> tuple:
    """Numeric building order: 2 < 9-2 < 10 < 10-1 < 100 < non-numeric."""
    nums = building.split("-")
    if all(_DIGITS.fullmatch(n) for n in nums):
        return (0, *[int(n) for n in nums])
    return (1, building)
