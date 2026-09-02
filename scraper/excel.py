"""Download + parse the sugang Excel export (workType=EX).

cc100InterfaceExcel.action returns a BIFF .xls whose 수업교시 column carries the
real per-class schedule as `요일(HH:MM~HH:MM)` blocks joined by '/', e.g.
`월(09:00~10:15)/수(09:00~10:15)`. This is the exact timing (start AND end,
multiple days, 1.5h blocks) — far better than stacking half-hour cell queries —
and the file already includes time-less classes (empty 수업교시) and the full
roster, so one request per term replaces the whole cell crawl.
"""
from __future__ import annotations

import re
import time

import xlrd

import session as snu_session
import slots

_XLS_MAGIC = b"\xd0\xcf\x11\xe0"   # OLE2 / BIFF .xls header

EXCEL_URL = f"{snu_session.BASE}/sugang/cc/cc100InterfaceExcel.action"
HEADER_ROW = 2   # row0 title, row1 filter summary, row2 column names


def _excel_form(year: str, term: str, extra: dict | None = None) -> dict:
    """Full HD102 form the page submits for workType=EX (no time filter).
    `extra` overlays additional filter fields (e.g. srchMrksGvMthd) — the export
    honours the same search filters as workType=S, but returns the FULL result
    set in one file (the HTML search is hard-capped at 10 rows per page)."""
    f = snu_session.blank_hd102_fields()
    f.update({"workType": "EX", "pageNo": "1", "srchOpenSchyy": year,
              "srchOpenShtm": term, "srchLanguage": "ko",
              "srchCurrPage": "1", "srchPageSize": "9999", "seeMore": "더보기"})
    if extra:
        f.update(extra)
    return f


def fetch_excel(client, year: str, term: str, retries: int = 4,
                extra: dict | None = None) -> bytes:
    """POST the EX form and return raw .xls bytes. The first request after a
    fresh session can return an HTML interstitial (NetFunnel/warm-up) instead of
    the file, so validate the BIFF magic and re-mint + retry if it's not there."""
    body = b""
    for attempt in range(retries):
        body = client._post(EXCEL_URL, _excel_form(year, term, extra=extra)).content
        if body[:4] == _XLS_MAGIC:
            return body
        # empty term replies with a tiny "history.go(-1)" script -> no data
        if body[:64].lstrip().lower().startswith(b"<script") and len(body) < 4096:
            return b""
        client.refresh()          # large HTML = interstitial/NetFunnel -> re-mint, retry
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(
        f"Excel for {year}/{term} not returned after {retries} tries "
        f"(got {len(body)} bytes starting {body[:16]!r})")


def _int(v):
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def _quota(v):
    """정원 cell -> (total, returning). It arrives as '130 (120)' meaning
    총정원 130 with 재학생 정원 120 (so 신입생 정원 = 130-120 = 10). Plain '30' has
    no split -> (30, None); empty -> (None, None)."""
    nums = re.findall(r"\d+", str(v) if v is not None else "")
    if not nums:
        return None, None
    total = int(nums[0])
    returning = int(nums[1]) if len(nums) > 1 else None
    return total, returning


_ROOM_COL = "강의실(동-호)(#연건, *평창)"   # header carries the campus legend (#=연건, *=평창)


def _room(raw: str) -> str:
    """'38-422(무선랜제공)/38-422/38-422' -> '38-422'; '/ /' -> ''. The column repeats
    the room per meeting; dedupe and drop the parenthetical notes."""
    seen: list[str] = []
    for part in (raw or "").split("/"):
        p = re.sub(r"\(.*?\)", "", part).strip()
        if p and p not in seen:
            seen.append(p)
    return "/".join(seen)


def parse_excel(content: bytes, year: str, term: str) -> list[dict]:
    """Parse the .xls into class records with exact slots (day/start/end)."""
    shtm, deta = term[:10], term[10:]
    sh = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
    hdr = {str(sh.cell_value(HEADER_ROW, c)).strip(): c for c in range(sh.ncols)}

    def g(r, name):
        c = hdr.get(name)
        return str(sh.cell_value(r, c)).strip() if c is not None else ""

    out = []
    for r in range(HEADER_ROW + 1, sh.nrows):
        sbjt = g(r, "교과목번호")
        if not sbjt:
            continue
        # 부제명 carries the detailed title (the part SNU shows in parentheses),
        # e.g. 교과목명 "특강" + 부제명 "미래의 지방행정체제". Keep it on the name.
        name = g(r, "교과목명")
        subtitle = g(r, "부제명")
        if subtitle and subtitle != name:
            name = f"{name} ({subtitle})"
        classification = [x for x in (g(r, "이수과정"), g(r, "교과구분")) if x]
        quota, quota_returning = _quota(g(r, "정원"))
        out.append({
            "year": year, "shtm_fg": shtm, "deta_shtm_fg": deta,
            "sbjt_cd": sbjt, "lt_no": g(r, "강좌번호") or "001", "subh_cd": "000",
            "name": name, "professor": g(r, "주담당교수"),
            "college": g(r, "개설대학"), "department": g(r, "개설학과"),
            "classification": classification,
            "grade": g(r, "학년"),
            "credits": _int(g(r, "학점")),
            "quota": quota, "quota_returning": quota_returning,
            "applied": _int(g(r, "수강신청인원")), "enrolled": None,
            "cart": _int(g(r, "장바구니신청")),
            "room": _room(g(r, _ROOM_COL)),
            "language": g(r, "강의언어"),       # '영어' / '한국어' / ...
            "status": g(r, "개설상태"),         # '설강' / '폐강대상'
            "slots": slots.parse_blocks(g(r, "수업교시")),
        })
    return out
