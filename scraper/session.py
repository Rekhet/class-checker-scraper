"""Mint a valid sugang.snu.ac.kr session via a headless browser.

The search endpoint 302s to the site root unless the session has "entered"
(enter=Y cookie + JSESSIONID). A real browser visit sets these naturally, so
we drive Playwright to the search page, click through any entry splash, and
hand the resulting cookies to the fast requests-based crawler.
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

BASE = "https://sugang.snu.ac.kr"
SEARCH_URL = f"{BASE}/sugang/cc/cc100InterfaceSrch.action"
# Anchors/buttons that gate entry to the system (Korean + English).
ENTER_HINTS = ["입장", "시작", "동의", "확인", "검색", "강좌검색", "Enter", "Agree"]

# The HD102 search form carries these optional filter fields, which the portal
# expects present-but-blank on an unfiltered query. workType=S (crawl) and
# workType=EX (excel) submit the exact same skeleton — only the head (workType,
# paging, seeMore) differs. Seeded here so the two callers can't drift.
_HD102_BLANK_FIELDS = (
    "srchSbjtNm", "srchSbjtCd", "srchCptnCorsFg", "srchOpenShyr",
    "srchOpenUpSbjtFldCd", "srchOpenSbjtFldCd", "srchOpenUpDeptCd",
    "srchOpenDeptCd", "srchOpenMjCd", "srchOpenSubmattCorsFg",
    "srchExcept", "srchOpenPntMin", "srchOpenPntMax", "srchCamp",
    "srchBdNo", "srchProfNm", "srchOpenSbjtTmNm", "srchOpenSbjtDayNm",
    "srchOpenSbjtTm", "srchOpenSbjtNm", "srchTlsnAplyCapaCntMin",
    "srchTlsnAplyCapaCntMax", "srchLsnProgType", "srchTlsnRcntMin",
    "srchTlsnRcntMax", "srchMrksGvMthd", "srchIsEngSbjt",
    "srchMrksApprMthdChgPosbYn", "srchIsPendingCourse",
    "srchGenrlRemoteLtYn",
)


def blank_hd102_fields() -> dict:
    """The optional HD102 filter fields, all blank (`srchOpenSubmattFgCd1..9`
    included). Callers overlay their head fields (workType, paging) on top."""
    f = {k: "" for k in _HD102_BLANK_FIELDS}
    for i in range(1, 10):
        f[f"srchOpenSubmattFgCd{i}"] = ""
    return f


def mint_session(headless: bool = True, timeout_ms: int = 30000) -> dict:
    """Return {"cookies": {name: value}, "cookie_header": "...", "ua": "..."}.

    Raises RuntimeError if the search page could not be reached.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"),
            locale="ko-KR",
        )
        page = context.new_page()
        try:
            # networkidle lets the entry JS run and set the enter=Y cookie.
            # The search page is POST-only (a GET 302s to root), so we don't
            # navigate to it; we just need the entered session's cookies.
            page.goto(BASE + "/", wait_until="networkidle", timeout=timeout_ms)
            ua = page.evaluate("() => navigator.userAgent")
            if not _search_works(context, ua):
                _try_enter(page)
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
                if not _search_works(context, ua):
                    raise RuntimeError(
                        f"session not search-capable (url: {page.url}, "
                        f"cookies: {[c['name'] for c in context.cookies()]})")
            cookies = {c["name"]: c["value"] for c in context.cookies()}
        finally:
            browser.close()

    if "JSESSIONID" not in cookies:
        raise RuntimeError(f"no JSESSIONID minted; got {list(cookies)}")
    cookies.setdefault("enter", "Y")
    cookies.setdefault("NetFunnel_ID", "")
    header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {"cookies": cookies, "cookie_header": header, "ua": ua}


def _search_works(context, ua: str) -> bool:
    """POST a tiny search with the current cookies; True if it returns results."""
    try:
        resp = context.request.post(
            SEARCH_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": BASE, "Referer": SEARCH_URL, "User-Agent": ua},
            form={"workType": "S", "pageNo": "1", "srchOpenSchyy": "2026",
                  "srchOpenShtm": "U000200002U000300001",
                  "srchLanguage": "ko", "srchCurrPage": "1"},
            max_redirects=0,
        )
        return resp.status == 200 and "srchOpenSbjtTm" in resp.text()
    except Exception:
        return False


def _try_enter(page) -> None:
    """Best-effort click through an entry/agreement splash if shown."""
    for hint in ENTER_HINTS:
        try:
            el = page.query_selector(
                f"a:has-text('{hint}'), button:has-text('{hint}')")
            if el and el.is_visible():
                el.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=8000)
                return
        except Exception:
            continue


if __name__ == "__main__":
    s = mint_session(headless=True)
    print("cookies:", list(s["cookies"]))
    print("JSESSIONID len:", len(s["cookies"].get("JSESSIONID", "")))
    print("header:", s["cookie_header"][:80], "...")
