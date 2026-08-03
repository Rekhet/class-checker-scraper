"""Render search-result class rows to CSV or XLSX for the export endpoints.

CSV is stdlib (UTF-8 BOM so Excel opens Korean correctly). XLSX needs openpyxl
(optional `export` extra) and is imported lazily so the core stays dependency-light.
"""
from __future__ import annotations

import csv
import io

_DAYS = "월화수목금토일"
_SEMESTER = {
    "U000200001U000300001": "1학기",
    "U000200002U000300001": "2학기",
    "U000200001U000300002": "여름학기",
    "U000200002U000300002": "겨울학기",
}
HEADERS = ["연도", "학기", "교과목명", "교수", "단과대학", "학과", "교과목번호", "강좌번호",
           "학점", "학년", "이수구분", "정원", "재학생정원", "신입생정원", "신청",
           "평가방식", "평가방식전환가능", "수업시간"]


def _fmt_slots(slots: list[dict]) -> str:
    """[{day_index,start_time,end_time}] -> '월(09:00~10:15)/수(09:00~10:15)'."""
    parts = []
    for s in slots or []:
        di, st, en = s.get("day_index"), s.get("start_time"), s.get("end_time")
        if di is None or not st:
            continue
        day = _DAYS[di] if 0 <= di < len(_DAYS) else "?"
        parts.append(f"{day}({st}~{en or ''})")
    return "/".join(parts)


def _row(c: dict) -> list:
    return [
        c.get("year", ""),
        _SEMESTER.get(c.get("term", ""), c.get("term", "")),
        c.get("name", ""),
        c.get("professor", ""),
        c.get("college", ""),
        c.get("department", ""),
        c.get("sbjt_cd", ""),
        c.get("lt_no", ""),
        c.get("credits"),
        c.get("grade", ""),
        " ".join(c.get("classification") or []),
        c.get("quota"),
        c.get("quota_returning"),
        (c["quota"] - c["quota_returning"]
         if c.get("quota") is not None and c.get("quota_returning") is not None
         else None),
        c.get("applied"),
        c.get("grading", "") or "",
        c.get("grading_switch", "") or "",
        _fmt_slots(c.get("slots") or []),
    ]


def to_csv(classes) -> bytes:
    """classes: any iterable of class dicts (a list or a chunked generator)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    for c in classes:
        w.writerow(_row(c))
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM => Excel reads UTF-8


def to_xlsx(classes) -> bytes:
    """classes: any iterable of class dicts. write_only streams rows to the zip so
    a huge export doesn't hold every cell in memory at once."""
    from openpyxl import Workbook   # lazy: optional `export` extra
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("classes")
    ws.append(HEADERS)
    for c in classes:
        ws.append(_row(c))
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
