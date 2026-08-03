"""Shared SNU schedule parsing: `요일(HH:MM~HH:MM)` meeting blocks.

Both parse.py (search-result HTML) and excel.py (workType=EX .xls) read the same
`월(09:00~10:15)/수(...)` shape — the Excel 수업교시 column and the search item text
carry identical block syntax. This lives in one place so the two parsers cannot
drift out of sync (they previously held byte-identical copies of all of this)."""
from __future__ import annotations

import re

DAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
TIME_BLOCK = re.compile(r"([월화수목금토일])\((\d{1,2}:\d{2})~(\d{1,2}:\d{2})\)")


def period(hhmm: str):
    """Period index from an 8am base: '08:00' -> 0, '09:30' -> 1. Bad input -> None."""
    try:
        return max(0, int(hhmm.split(":")[0]) - 8)
    except ValueError:
        return None


def parse_blocks(text: str) -> list[dict]:
    """`월(09:00~10:15)/수(...)` -> [{day_index, period, start_time, end_time}]."""
    out = []
    for day, start, end in TIME_BLOCK.findall(text or ""):
        out.append({"day_index": DAY_INDEX[day], "period": period(start),
                    "start_time": start, "end_time": end})
    return out
