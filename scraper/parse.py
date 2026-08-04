"""Parse SNU sugang search-result HTML (cc100InterfaceSrch).

The Excel export (excel.py) is the source of the catalog + exact timing, but it
LAGS on the fast-moving enrollment counts — 장바구니신청 (cart) and 수강신청인원
(applied) — which the live search UI updates immediately. Those volatile numbers
must come from the search HTML, so this module turns one result page into
per-class records (identity + professor/department/credits + applied/quota/
enrolled). A future live-count refresh will query the search endpoint per term,
paginate, and update only those columns. The TimeSlot/query_slot fields are
legacy from the old cell-stacking crawl and are unused now that timing is in the
Excel; kept because they ride along harmlessly in the same parse.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict, field

from bs4 import BeautifulSoup

import slots

try:
    import lxml  # noqa: F401
    _PARSER = "lxml"
except Exception:
    _PARSER = "html.parser"


@dataclass
class TimeSlot:
    """The single day/period cell this response was queried under."""
    code: str
    label: str
    day: str | None
    day_index: int | None
    period: int | None
    start_time: str | None


@dataclass
class ClassRecord:
    year: str
    shtm_fg: str
    deta_shtm_fg: str
    sbjt_cd: str
    lt_no: str
    subh_cd: str
    name: str
    classification: list[str] = field(default_factory=list)
    professor: str = ""
    department: str = ""
    credits: int | None = None
    quota: int | None = None
    applied: int | None = None
    enrolled: int | None = None
    cart: int | None = None       # 장바구니신청 인원 (volatile; from search results)
    slots: list[TimeSlot] = field(default_factory=list)
    time_blocks: list[dict] = field(default_factory=list)  # exact day/start/end

    @property
    def key(self) -> tuple:
        """Identity of a class across queries (stack key)."""
        return (self.year, self.shtm_fg, self.deta_shtm_fg,
                self.sbjt_cd, self.lt_no, self.subh_cd)


def _hidden(form_scope, name: str) -> str:
    el = form_scope.find("input", attrs={"name": name})
    if el:
        return el.get("value", "").strip()
    return ""


def _parse_label(code: str, label: str) -> TimeSlot:
    """'7교시(15:00~),금요일' -> period 7, 15:00, 금/4."""
    day = day_index = period = start = None
    for part in label.split(","):
        part = part.strip()
        if part.endswith("요일") and part[0] in slots.DAY_INDEX:
            day = part[0]
            day_index = slots.DAY_INDEX[day]
    m = re.search(r"(\d+)\s*교시", label)
    if m:
        period = int(m.group(1))
    m = re.search(r"(\d{1,2}:\d{2})", label)
    if m:
        start = m.group(1)
    return TimeSlot(code=code, label=label, day=day, day_index=day_index,
                    period=period, start_time=start)


def _query_slot(soup) -> TimeSlot:
    code = ""
    label = ""
    el = soup.find("input", id="srchOpenSbjtTm")
    if el:
        code = el.get("value", "").strip()
    el = soup.find("input", id="srchOpenSbjtNm")
    if el:
        label = el.get("value", "").strip()
    return _parse_label(code, label)


def _total_count(soup) -> int:
    el = soup.select_one(".search-result-con small em")
    if el and el.text.strip().isdigit():
        return int(el.text.strip())
    return 0


def _int_or_none(s: str):
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else None


def _parse_item(item, slot: TimeSlot, year, shtm, deta) -> ClassRecord:
    name = _hidden(item, "sbjtNm")
    rec = ClassRecord(
        year=year or _hidden(item, "openSchyy"),
        shtm_fg=shtm or _hidden(item, "openShtmFg"),
        deta_shtm_fg=deta or _hidden(item, "openDetaShtmFg"),
        sbjt_cd=_hidden(item, "sbjtCd"),
        lt_no=_hidden(item, "ltNo"),
        subh_cd=_hidden(item, "sbjtSubhCd"),
        name=name,
        slots=[slot],
    )
    cname = item.select_one(".course-name")
    if cname:
        rec.classification = re.findall(r"\[([^\]]+)\]", cname.get_text())
        strong = cname.find("strong")
        if strong:
            rec.name = re.sub(r"\s+", " ", strong.get_text(" ", strip=True)) or name
    txts = item.select("ul.course-info li.txt")
    if txts:
        spans = txts[0].find_all("span")
        if len(spans) >= 3:
            rec.professor = spans[0].get_text(strip=True)
            rec.department = spans[1].get_text(strip=True)
    if len(txts) >= 2:
        for span in txts[1].find_all("span", recursive=False):
            t = span.get_text(" ", strip=True)
            em = span.find("em")
            val = em.get_text(strip=True) if em else ""
            if "정원" in t:
                m = re.search(r"(\d+)\s*/\s*(\d+)", val)
                if m:
                    rec.applied = int(m.group(1))
                    rec.quota = int(m.group(2))
            elif "총수강인원" in t:
                rec.enrolled = _int_or_none(val)
            elif "장바구니" in t:
                rec.cart = _int_or_none(val)
            elif "학점" in t:
                rec.credits = _int_or_none(val)
    # The live search page renders the cart count outside the course-info list.
    # It is the text node after <em title="장바구니"></em> in this span.
    cart = item.select_one("span.carts")
    if cart:
        rec.cart = _int_or_none(cart.get_text(" ", strip=True))
    rec.time_blocks = slots.parse_blocks(item.get_text(" "))
    return rec


def parse_response(html: str) -> dict:
    """Parse one result page into {query_slot, total, classes:[...]}."""
    soup = BeautifulSoup(html, _PARSER)
    slot = _query_slot(soup)
    year = label_year = ""
    el = soup.find("input", id="srchOpenSchyy")
    if el:
        year = el.get("value", "").strip()
    el = soup.find("input", id="srchOpenShtm")
    shtm = deta = ""
    if el:
        combo = el.get("value", "").strip()
        if len(combo) == 20:
            shtm, deta = combo[:10], combo[10:]
    items = soup.select("div.course-info-item")
    classes = [_parse_item(it, slot, year, shtm, deta) for it in items]
    return {
        "query_slot": asdict(slot),
        "total": _total_count(soup),
        "page_count": len(classes),
        "classes": [asdict(c) for c in classes],
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_response.txt"
    with open(path, encoding="utf-8") as f:
        out = parse_response(f.read())
    print(json.dumps(out, ensure_ascii=False, indent=2))
