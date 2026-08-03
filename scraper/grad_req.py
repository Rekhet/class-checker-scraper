"""Extract 졸업요건 (graduation requirements) into web/data/grad_req/<dept>_<batch>.json.

Design
------
* One **Extractor** per page *style*. `StatExtractor` parses the stat.snu.ac.kr table
  layout deterministically (no LLM). Other departments publish the same data in wildly
  different HTML — for those, `LLMExtractor` is the extension point (see NOTE: LLM).
* Batch URLs are NOT guessed. We fetch the dept's 이수규정 index page and read the actual
  per-batch links (labels like "2026학번", "2023~2024학번"), so slug naming never matters.
* 전공필수/전공선택 course *lists* are intentionally NOT scraped — the web app derives them
  live from the catalog (dept + 전필/전선 classification). We only capture the numeric
  minimums + match rules.
* 교양 (공통교육과정) areas are college-wide and change rarely, so they're hand-encoded per
  (college, ruleset) below. NOTE: LLM — a new/changed college PDF can be converted to this
  shape by an LLM (gyo_from_pdf) instead of hand-editing.

Usage
-----
    python grad_req.py --dept stat --list                 # show available batch links
    python grad_req.py --dept stat --batch 2026 --dry-run # print JSON, don't write
    python grad_req.py --dept stat --batch 2026,2023      # write json(s) + index
    python grad_req.py --dept stat --all                  # every batch the index lists
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "data" / "grad_req"
UA = {"User-Agent": "Mozilla/5.0 (grad-req-bot)"}

# 자연과학대학 공통교육과정 이수규정 (2026~). College-wide; reused by every NatSci dept until
# the college changes the ruleset. NOTE: LLM — if a new PDF appears, feed its text to an LLM
# asking for exactly this shape, then paste the result here (or wire gyo_from_pdf()).
GYO_NATSCI_2026 = {
    "general_min_credits": 46,
    "general_min_credits_diagnostic": 36,
    "general_areas": [
        {"key": "writing", "name": "글쓰기와 말하기", "credits": 4, "match": ["대학 글쓰기", "글쓰기", "말하기"]},
        {"key": "foreign", "name": "외국어", "credits": 6, "match": ["영어", "프랑스어", "독일어", "중국어", "일본어", "러시아어", "스페인어", "라틴어", "이탈리아어", "회화", "작문", "강독", "외국어"]},
        {"key": "msc", "name": "수학·과학·컴퓨팅", "credits": 24, "match": ["미적분학", "수학", "통계학", "물리학", "물리의 기본", "화학", "생물학", "지구환경", "천문학", "대기과학", "지구시스템", "해양학", "수치", "컴퓨터", "프로그래밍", "정보"]},
        {"key": "culture", "name": "문화 해석과 상상", "credits": 3, "match": []},
        {"key": "history", "name": "역사적 탐구와 철학적 사유", "credits": 3, "match": []},
        {"key": "human", "name": "인간의 이해와 사회 분석", "credits": 3, "match": []},
        {"key": "veritas", "name": "베리타스", "credits": 3, "match": ["베리타스"]},
    ],
}
GYO_NATSCI_A = GYO_NATSCI_2026   # 학문의 토대 / 지성의 열쇠 / 베리타스 (2025학번~)

# 2021~2024 ruleset: 학문의 기초 (사고와표현·외국어·수량+과학) + 학문의 세계 12 (catch-all).
GYO_NATSCI_B = {
    "general_min_credits": 46,
    "general_min_credits_diagnostic": 36,
    "general_areas": [
        {"key": "writing", "name": "사고와 표현", "credits": 4, "match": ["대학 글쓰기", "글쓰기", "말하기"]},
        {"key": "foreign", "name": "외국어", "credits": 6, "match": ["영어", "프랑스어", "독일어", "중국어", "일본어", "러시아어", "스페인어", "라틴어", "이탈리아어", "회화", "작문", "강독", "외국어"]},
        {"key": "msc", "name": "수량적 분석·과학적 사고", "credits": 24, "match": ["미적분학", "수학", "통계학", "물리학", "물리의 기본", "화학", "생물학", "지구환경", "천문학", "대기과학", "지구시스템", "해양학", "수치", "컴퓨터", "프로그래밍", "정보"]},
        {"key": "world", "name": "학문의 세계", "credits": 12, "match": ["*"]},   # catch-all for the 5+ 학문의 세계 영역
    ],
}


def gyo_for(years: list[str]) -> dict:
    """Pick the college general-ed ruleset by batch year (reform boundary at 2025)."""
    return GYO_NATSCI_A if any(int(y) >= 2025 for y in years) else GYO_NATSCI_B


def years_from_label(label: str) -> list[str]:
    """'2023~2024학번' -> ['2023','2024']; '2025학번 이후' -> ['2025']; '2026학번' -> ['2026']."""
    ys = re.findall(r"(20\d{2})", label)
    if "~" in label and len(ys) == 2:
        return [str(y) for y in range(int(ys[0]), int(ys[1]) + 1)]
    return ys[:1]


def fetch(url: str) -> str:
    url = urllib.parse.quote(url, safe="%/:?=&#")   # urllib needs ascii; encode Korean paths
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _text(html: str) -> str:
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    html = re.sub(r"</(td|th)>", " | ", html, flags=re.I)
    html = re.sub(r"</(tr|p|div|li|h[1-6])>", "\n", html, flags=re.I)
    t = unescape(re.sub(r"<[^>]+>", " ", html))
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n") if ln.strip())


def _tables(html: str) -> list[list[list[str]]]:
    out = []
    for tb in re.findall(r"<table[\s\S]*?</table>", html, flags=re.I):
        rows = []
        for tr in re.findall(r"<tr[\s\S]*?</tr>", tb, flags=re.I):
            cells = [unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                     for c in re.findall(r"<t[dh][\s\S]*?</t[dh]>", tr, flags=re.I)]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


class Extractor:
    """Base: a page style -> spec dict. Subclass per dept layout."""
    dept = ""
    major = ""
    college = ""
    index_url = ""              # the 이수규정 index page listing per-batch links
    gyo = GYO_NATSCI_2026

    def list_batches(self) -> list[dict]:
        """[{label, href}] of available per-batch regulation pages."""
        html = fetch(self.index_url)
        seen, out = set(), []
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', html, flags=re.I):
            href, txt = m.group(1), unescape(re.sub(r"<[^>]+>", " ", m.group(2))).strip()
            if re.search(r"\d{4}\s*학번|학번\s*이전|학번", txt) and "href" not in txt:
                href = urllib.parse.urljoin(self.index_url, href)
                if href not in seen:
                    seen.add(href)
                    out.append({"label": re.sub(r"\s+", " ", txt), "href": href})
        return out

    def resolve(self, batch: str) -> dict:
        """Find the batch link whose label contains `batch` (e.g. '2026' or '2023~2024')."""
        for b in self.list_batches():
            if batch in b["label"].replace(" ", ""):
                return b
        raise SystemExit(f"batch {batch!r} not found. Try --list.")

    def extract(self, batch: str) -> dict:
        raise NotImplementedError


class StatExtractor(Extractor):
    dept = "stat"
    major = "통계학과"
    college = "자연과학대학"
    index_url = "https://stat.snu.ac.kr/교과과정/학사과정/교과목-이수규정/"

    def extract(self, batch: str) -> dict:
        link = self.resolve(batch)
        years = years_from_label(link["label"])     # batch is a LIST; each year shown independently
        gyo = gyo_for(years)
        html = fetch(link["href"])
        tables, text = _tables(html), _text(html)

        # 1) 졸업이수학점표 value row: the 졸업학점 label sits in a separate header cell, so
        # find the row whose integers lead with the 졸업학점 (120-140). Order on every batch
        # page: 졸업 | 교양 | 주전공(단일) | 주전공(병행) | 복수전공 | 부전공.
        nums = []
        for tb in tables:
            for r in tb:
                ints = [int(x) for c in r for x in re.findall(r"\b(\d{2,3})\b", c)]
                if len(ints) >= 5 and 120 <= ints[0] <= 140:
                    nums = ints
                    break
            if nums:
                break
        total, gen, major_single, minor = None, None, None, None
        if len(nums) >= 6:
            total, gen, major_single, _double, _dbl2, minor = nums[:6]
        elif nums:
            total = nums[0]

        # 2) text-derived minimums (robust to wording)
        m = re.search(r"(\d+)\s*과목\s*이상", text)
        major_select_min = int(m.group(1)) if m else 5
        m = re.search(r"최대\s*(\d+)\s*학점", text)
        approval_max = int(m.group(1)) if m else 9
        english_min = 1 if re.search(r"영어진행강좌|외국어진행강좌|영어강의", text) else 0
        recog_depts = [d for d in ("수리과학부", "컴퓨터공학부") if d in text]

        spec = {
            "id": "stat_" + "_".join(years),
            "major": self.major, "college": self.college, "batch": years, "track": "단일전공",
            "source": link["href"],
            "total_credits": total or 130,
            "major_min_credits": major_single or 60,
            "minor_credits": minor or 21,
            "major_select_min_courses": major_select_min,
            "english_min_courses": english_min,
            "external_recognition": {"depts": recog_depts or ["수리과학부", "컴퓨터공학부"],
                                     "approval_max_credits": approval_max},
            "major_required_match": {"departments": ["통계"], "classifications": ["전필"]},
            "major_select_match": {"departments": ["통계"], "classifications": ["전선"]},
            "major_required_known": [
                {"name": "수리통계 1", "code": "326.311"},
                {"name": "수리통계 2", "code": "326.312"},
                {"name": "회귀분석 및 실습", "code": "326.313"},
            ],
            "dept_required_general": [{"name": "통계학", "area": "msc"}, {"name": "통계학실험", "area": "msc"}],
            # 수리통계: 주전공·복수전공은 1(326.311)+2(326.312) 필수, 수리통계(M1399.000900) 이수 불가.
            # 부전공만 M1399로 수리통계 1·2를 대체 가능.
            "suri": {"seq": [{"name": "수리통계 1", "code": "326.311", "credits": 3}, {"name": "수리통계 2", "code": "326.312", "credits": 3}],
                     "combined": {"name": "수리통계", "code": "M1399.000900", "credits": 3}},
            "tracks": [
                {"key": "single", "name": "심화전공(단일전공)", "major_min_credits": (nums[2] if len(nums) > 2 else 60), "general": True, "select_min": major_select_min, "suri_sub": False},
                {"key": "multi", "name": "주전공(다전공)", "major_min_credits": (nums[3] if len(nums) > 3 else 39), "general": True, "select_min": major_select_min, "suri_sub": False},
                {"key": "double", "name": "복수전공", "major_min_credits": (nums[4] if len(nums) > 4 else 39), "general": False, "select_min": major_select_min, "suri_sub": False},
                {"key": "minor", "name": "부전공", "major_min_credits": (minor or 21), "general": False, "select_min": 0, "suri_sub": True},
            ],
        }
        spec.update({k: gyo[k] for k in ("general_min_credits", "general_min_credits_diagnostic", "general_areas")})
        spec["notes"] = [
            "통계학과: 통계학 및 통계학실험 필수, 미적분학 1·2 원칙.",
            "외국어진행강좌(영어강의) 최소 1과목 권장.",
            f"전공선택인정: {', '.join(spec['external_recognition']['depts'])} 전 교과목 + 학과장 사전승인 최대 {approval_max}학점.",
            "기초수학과학 진단평가 면제 시 수과컴/총 교양 최소학점이 차감될 수 있음.",
        ]
        _validate(spec)
        return spec


class LLMExtractor(Extractor):
    """For arbitrary dept pages whose layout StatExtractor can't parse.

    NOTE: LLM — implement `llm_extract` to call your model with the page text and the
    target schema (copy stat_2026.json as the example). Until then this raises with
    instructions so it can be done MANUALLY: run --emit-prompt, paste into any LLM, save
    the returned JSON to web/data/grad_req/<dept>_<batch>.json.
    """
    def extract(self, batch: str) -> dict:
        link = self.resolve(batch)
        text = _text(fetch(link["href"]))
        return llm_extract(text, dept=self.dept, batch=batch, source=link["href"], gyo=self.gyo)


def llm_extract(text: str, *, dept: str, batch: str, source: str, gyo: dict) -> dict:
    raise NotImplementedError(
        "LLM extraction not wired. Two options:\n"
        "  (a) implement this function: send `text` + the stat_2026.json schema to an LLM,\n"
        "      return the parsed dict (merge `gyo` for general_areas).\n"
        "  (b) manual: run with --emit-prompt to get a ready prompt, paste into an LLM,\n"
        "      save the JSON to web/data/grad_req/<dept>_<batch>.json.")


def emit_prompt(ex: Extractor, batch: str) -> str:
    link = ex.resolve(batch)
    text = _text(fetch(link["href"]))
    schema = (OUT_DIR / "stat_2026.json")
    example = schema.read_text(encoding="utf-8") if schema.exists() else "{...see StatExtractor...}"
    return (f"Extract SNU graduation requirements into JSON EXACTLY matching this schema:\n\n{example}\n\n"
            f"Rules: keep general_areas as-is for 자연과학대학; fill major/total/교양 numbers and match rules "
            f"from the page. dept={ex.dept} batch={batch} source={link['href']}\n\nPAGE TEXT:\n{text}")


def _validate(spec: dict) -> None:
    t = spec.get("total_credits")
    if not (100 <= (t or 0) <= 140):
        print(f"  ! WARN total_credits={t} out of expected range — verify the page parse", file=sys.stderr)
    if not (30 <= (spec.get("major_min_credits") or 0) <= 90):
        print(f"  ! WARN major_min_credits={spec.get('major_min_credits')} suspicious", file=sys.stderr)


EXTRACTORS = {"stat": StatExtractor}


def write_index() -> None:
    specs = []
    for p in sorted(OUT_DIR.glob("*.json")):
        if p.name == "index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for y in (d["batch"] if isinstance(d["batch"], list) else [d["batch"]]):
                specs.append({"id": d["id"], "major": d["major"], "batch": str(y), "file": p.name})
        except Exception as e:  # noqa: BLE001
            print(f"  ! skip {p.name}: {e}", file=sys.stderr)
    specs.sort(key=lambda s: (s["major"], s["batch"]), reverse=True)
    (OUT_DIR / "index.json").write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"index.json: {len(specs)} batch entr(ies)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract SNU 졸업요건 into web/data/grad_req/")
    ap.add_argument("--dept", default="stat", choices=list(EXTRACTORS) + ["llm"])
    ap.add_argument("--batch", default="", help="comma list, e.g. 2026,2023~2024 (matches link labels)")
    ap.add_argument("--all", action="store_true", help="every batch the index page lists")
    ap.add_argument("--list", action="store_true", help="print available batch links and exit")
    ap.add_argument("--dry-run", action="store_true", help="print JSON, don't write")
    ap.add_argument("--emit-prompt", action="store_true", help="print an LLM prompt for the batch (manual extraction)")
    args = ap.parse_args()

    ex = (EXTRACTORS.get(args.dept) or LLMExtractor)()
    if args.list:
        for b in ex.list_batches():
            print(f"{b['label']:<18} {b['href']}")
        return

    batches = []
    if args.all:
        batches = [re.sub(r"\s", "", b["label"]) for b in ex.list_batches() if re.search(r"\d{4}", b["label"])]
    elif args.batch:
        batches = [b.strip() for b in args.batch.split(",") if b.strip()]
    else:
        ap.error("give --batch, --all, or --list")

    if args.emit_prompt:
        print(emit_prompt(ex, batches[0]))
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for b in batches:
        print(f"== {args.dept} {b} ==")
        spec = ex.extract(b)
        if args.dry_run:
            print(json.dumps(spec, ensure_ascii=False, indent=2))
            continue
        out = OUT_DIR / f"{spec['id']}.json"
        out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  wrote {out.relative_to(OUT_DIR.parent.parent)} batch={spec['batch']}")
    if not args.dry_run:
        write_index()


if __name__ == "__main__":
    main()
