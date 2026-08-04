"""Crawl SNU sugang: Excel catalog/timing + live search count overlay.

Two sources, two cadences:

* Excel (cc100InterfaceExcel.action) is authoritative for the catalog AND the
  exact per-class schedule (수업교시: `요일(HH:MM~HH:MM)` blocks, incl. time-less
  classes). One download per term rebuilds the whole roster with precise times.
  It is downloaded once and then on a timer — it LAGS on enrollment, so we never
  trust its counts.
* Search (cc100InterfaceSrch.action) is queried per term and paginated to refresh
  the selected volatile numbers (수강신청인원/applied, 정원/quota,
  장바구니/cart, 총수강인원/enrolled) that the live UI updates immediately.
  These overlay the stale Excel counts; everything else (identity, timing,
  professor, credits) stays as the Excel wrote it. Selecting cart and enrollment
  together still uses one search pass per term.

So: Excel timing/catalog overrides; when Excel is missing/empty the search data is
what we have. crawl_term() runs the selected catalog, live-metric, and grading
steps; refresh_counts_all() does a selected live-metric pass between rebuilds.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

import changelog
import db
import excel
import parse
import process_lock
import session as snu_session

log = logging.getLogger("class-checker")


def _today_iso(now: datetime | None = None) -> str:
    """Return today's date in the configured collection timezone.

    The crawler runs on a host whose process timezone may not match the SNU
    collection schedule. An explicit ``COLLECTION_TIMEZONE`` keeps the boundary
    at midnight in the schedule's timezone. With no setting, preserve the
    historical host-local behavior.
    """
    supplied_now = now is not None
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    timezone_name = (os.environ.get("COLLECTION_TIMEZONE") or "").strip()
    if not timezone_name:
        return (now.date() if supplied_now else date.today()).isoformat()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown COLLECTION_TIMEZONE: {timezone_name}") from exc
    return now.astimezone(zone).date().isoformat()


def _in_window(start_env: str, end_env: str) -> bool:
    """True if today is within [<start_env>, <end_env>] (YYYY-MM-DD env vars, each
    optional). Gates which volatile metric is sampled into the 인원 추이 history:
    장바구니 (CART_START/CART_END) and 수강 인원 (ENROLL_START/ENROLL_END). Unset
    bounds mean 'always', so the default behaviour is unchanged."""
    today = _today_iso()
    s = (os.environ.get(start_env) or "").strip()
    e = (os.environ.get(end_env) or "").strip()
    if s and today < s:
        return False
    if e and today > e:
        return False
    return True


def _in_windows(spec: str) -> bool:
    """True if today falls in ANY window of a comma-separated list, each window a
    'YYYY-MM-DD' single day or 'YYYY-MM-DD..YYYY-MM-DD' inclusive range (a blank bound
    is open). Lets one metric be sampled across several disjoint periods — 예비/본
    수강신청, 개강전·개강후 변경 — without collecting through the dead gaps between them."""
    today = _today_iso()
    for w in (spec or "").split(","):
        w = w.strip()
        if not w:
            continue
        s, _, e = w.partition("..")
        s = s.strip(); e = e.strip() or s
        if (not s or today >= s) and (not e or today <= e):
            return True
    return False


def _sample_windows() -> dict:
    # Prefer the multi-window lists (CART_WINDOWS / ENROLL_WINDOWS); fall back to the
    # legacy single CART_START/END + ENROLL_START/END window when a list is unset.
    cart = (os.environ.get("CART_WINDOWS") or "").strip()
    enr = (os.environ.get("ENROLL_WINDOWS") or "").strip()
    return {"collect_cart": _in_windows(cart) if cart else _in_window("CART_START", "CART_END"),
            "collect_enrolled": _in_windows(enr) if enr else _in_window("ENROLL_START", "ENROLL_END")}


def _forced_windows() -> dict:
    """Forced update of a past semester: ignore the period windows (we're after the
    period, so don't annotate by it) and capture 수강 인원 only — never 장바구니
    (cart is meaningless once registration has ended)."""
    return {"collect_cart": False, "collect_enrolled": True}


def _sample_options(*, collect_cart: bool, collect_enrollment: bool,
                    force: bool) -> dict:
    """Return sampling switches after applying the selected collectors."""
    options = _forced_windows() if force else _sample_windows()
    options["collect_cart"] = bool(options["collect_cart"] and collect_cart)
    options["collect_enrolled"] = bool(
        options["collect_enrolled"] and collect_enrollment
    )
    # Applied is paired with the enrollment collector. A cart-only pass must
    # never turn the applied value from the same search response into a sample.
    options["collect_applied"] = bool(collect_enrollment)
    return options


def _window_active(*, collect_cart: bool = True,
                   collect_enrollment: bool = True) -> bool:
    windows = _sample_windows()
    return ((collect_cart and windows["collect_cart"])
            or (collect_enrollment and windows["collect_enrolled"]))

AJAX_URL = f"{snu_session.BASE}/sugang/cc/cc100ajax.action"
SEARCH_URL = snu_session.SEARCH_URL
# cc100ajax replies with an empty body unless a term code is supplied; any valid
# term unlocks the full SHTM list for the requested year. This is just the seed.
SEED_SHTM = "U000200002U000300001"
# Recover timing from the search results only when the Excel has times for fewer
# than this fraction of a term's classes (i.e. the Excel timetable hasn't landed
# yet, e.g. fall). Above it, the Excel-time-less classes are genuinely time-less.
SEARCH_TIMING_MAX_RATIO = 0.5
ProgressFn = Callable[[dict], None]

# The CLI and scheduled wrappers use these names as the stable collection
# contract. A single live search pass can update either or both volatile metric
# groups, so selecting both never requires two duplicate requests.
COLLECTION_COMPONENTS = frozenset(("catalog", "enrollment", "cart", "grading"))
LIVE_COMPONENTS = frozenset(("enrollment", "cart"))
DEFAULT_FULL_COMPONENTS = frozenset(("catalog", "enrollment", "grading"))


def parse_collection_spec(values: str | list[str]) -> frozenset[str]:
    """Parse one or more comma-separated collection component names."""
    if isinstance(values, str):
        values = [values]
    names = [name.strip().lower() for value in values for name in value.split(",")]
    names = [name for name in names if name]
    if not names:
        raise ValueError("collection selection cannot be empty")
    if "all" in names:
        if len(names) != 1:
            raise ValueError("all cannot be combined with other collection components")
        return COLLECTION_COMPONENTS
    unknown = sorted(set(names) - COLLECTION_COMPONENTS)
    if unknown:
        choices = ", ".join(sorted(COLLECTION_COMPONENTS))
        raise ValueError(f"unknown collection component(s): {', '.join(unknown)}; "
                         f"choose from {choices}")
    return frozenset(names)


def _search_form(year: str, term: str, *, page: int = 1, page_size: int = 9999,
                 extra: dict | None = None) -> dict:
    """Full HD102 form for a workType=S term search; returns the whole roster
    (each result item carries its exact schedule + live enrollment counts).
    `extra` overlays additional filter fields (e.g. srchMrksGvMthd) on the form."""
    f = snu_session.blank_hd102_fields()
    f.update({"workType": "S", "pageNo": str(page), "srchOpenSchyy": year,
              "srchOpenShtm": term, "srchLanguage": "ko",
              "srchCurrPage": str(page), "srchPageSize": str(page_size)})
    if extra:
        f.update(extra)
    return f


class SnuClient:
    """requests session with sugang cookies; re-mints on auth expiry."""

    def __init__(self, mint: Callable[[], dict] = snu_session.mint_session):
        self._mint = mint
        self.http = requests.Session()
        self.ua = ""
        self._lock = threading.Lock()
        self._gen = 0
        self.refresh()

    def refresh(self) -> None:
        s = self._mint()
        self.ua = s["ua"]
        self.http.cookies.clear()
        self.http.cookies.update(s["cookies"])
        self.http.headers.update({
            "User-Agent": self.ua,
            "Origin": snu_session.BASE,
            "Referer": snu_session.SEARCH_URL,
        })

    def _post(self, url: str, data: dict, _retry: bool = True):
        gen = self._gen
        r = self.http.post(url, data=data, allow_redirects=False, timeout=60)
        if r.status_code in (301, 302) and _retry:  # session dropped -> re-mint once
            with self._lock:
                if gen == self._gen:
                    self.refresh()
                    self._gen += 1
            return self._post(url, data, _retry=False)
        r.raise_for_status()
        return r

    def fetch_terms(self, year: str) -> list[dict]:
        """Available terms for a year: [{term, year, label}]. cc100ajax needs a
        seed term code (srchOpenShtm) or it answers with an empty body."""
        r = self._post(AJAX_URL, {"srchOpenSchyy": year,
                                  "srchOpenShtm": SEED_SHTM, "workType": "S"})
        try:
            shtm = r.json().get("SHTM", [])
        except ValueError:
            raise RuntimeError(
                f"cc100ajax returned non-JSON for year {year} "
                f"(status {r.status_code}, {len(r.text)} bytes); session may be invalid")
        return [{"term": t["cmmnCd"], "year": year, "label": f"{year} {t['korNm']}"}
                for t in shtm]

    def search_page(self, year: str, term: str, *, page: int = 1,
                    page_size: int = 9999, extra: dict | None = None) -> str:
        """One page of search-result HTML for a term (workType=S)."""
        return self._post(SEARCH_URL, _search_form(year, term, page=page,
                                                   page_size=page_size,
                                                   extra=extra)).text

def refresh_counts(conn, client: SnuClient, year: str, term: str, *,
                   label: str = "", progress: ProgressFn | None = None,
                   collect_cart: bool = True, collect_enrollment: bool = True,
                   cart_only: bool | None = None) -> dict:
    """Paginate one search response and update the selected live metrics.

    ``cart_only`` remains as a compatibility alias for callers using the old
    internal API. New callers should select ``collect_cart`` and
    ``collect_enrollment`` explicitly; both are applied in this one search
    pass when selected.
    """
    if cart_only is not None:
        collect_cart = True
        collect_enrollment = not cart_only
    if not collect_cart and not collect_enrollment:
        return {"term": term, "fetched": 0, "updated": 0,
                "skipped": "no live metrics selected"}

    fetched: list[dict] = []
    page = 1
    while True:
        res = parse.parse_response(client.search_page(year, term, page=page))
        fetched.extend(res["classes"])
        total = res["total"]
        if progress:
            progress({"phase": "counts", "term": term, "label": label,
                      "slot_index": len(fetched), "slot_total": total or len(fetched),
                      "slot_label": "수강인원 갱신"})
        if res["page_count"] == 0 or len(fetched) >= total:
            break
        page += 1

    updated = 0
    for c in fetched:
        kwargs = {}
        if collect_cart:
            kwargs["cart"] = c.get("cart")
        if collect_enrollment:
            kwargs.update(applied=c.get("applied"), quota=c.get("quota"),
                          enrolled=c.get("enrolled"))
        if db.update_counts(
            conn, year, c["shtm_fg"], c["deta_shtm_fg"], c["sbjt_cd"],
            c["lt_no"], c["subh_cd"], name=c.get("name"),
            professor=c.get("professor"), department=c.get("department"),
            **kwargs,
        ):
            updated += 1
    conn.commit()
    unmatched = len(fetched) - updated
    if unmatched:
        log.warning("live count overlay left %d/%d classes unmatched for %s",
                    unmatched, len(fetched), term)
    return {"term": term, "fetched": len(fetched), "updated": updated,
            "unmatched": unmatched}


# 성적부여형태 (MRKS_GV_MTHD) codes from cc100ajax STRINGCOMMONCODE. A~F + S/U +
# S+/S/U partition the roster exactly (verified: 5353+2955+12 = 8320 for 2026-2),
# so three filtered sweeps tag every class's 평가방식.
GRADING_CODES = [("U051200001", "A~F"),
                 ("U051200002", "S/U"),
                 ("U051200003", "S+/S/U")]
# 성적평가방법 변경가능 checkbox: the server only tests non-empty, any value works.
GRADING_SWITCH_PARAM = {"srchMrksApprMthdChgPosbYn": "Y"}


def _check_grading_codes(client: SnuClient, year: str) -> None:
    """Warn if the site's live MRKS_GV_MTHD list drifts from GRADING_CODES — a
    method added server-side would otherwise just leave its classes untagged
    (NULL) with no explanation. Best-effort, one cc100ajax call per client."""
    if getattr(client, "_grading_codes_checked", False):
        return
    client._grading_codes_checked = True
    try:
        r = client._post(AJAX_URL, {"srchOpenSchyy": year,
                                    "srchOpenShtm": SEED_SHTM, "workType": "S"})
        live = {(v["fldCd"], v["fldNm"]) for v in r.json().get("STRINGCOMMONCODE", [])
                if v.get("cdNm") == "MRKS_GV_MTHD"}
        if live != set(GRADING_CODES):
            log.warning("MRKS_GV_MTHD drift: site=%s crawler=%s — update GRADING_CODES",
                        sorted(live), GRADING_CODES)
    except Exception:  # noqa: BLE001 - a failed check must not block the sweep
        log.exception("MRKS_GV_MTHD live-code check failed")


def refresh_grading(conn, client: SnuClient, year: str, term: str, *,
                    label: str = "", progress: ProgressFn | None = None) -> dict:
    """Tag each class's 평가방식 + 전환가능여부. Neither field appears as a column
    in the Excel nor in the search-result items — only the detail popup shows them
    — but both endpoints FILTER on them (srchMrksGvMthd / srchMrksApprMthdChgPosbYn),
    so one FILTERED EXCEL download per value recovers the whole term by set
    membership: 3 downloads map 성적부여형태, 1 marks the 전환가능 classes. (The
    HTML search is hard-capped at 10 rows/page, so Excel is the only full sweep.)
    All keys are collected first and applied in one shot, so a failed download
    changes nothing."""
    _check_grading_codes(client, year)

    def sweep(extra: dict, slot_label: str) -> set[tuple]:
        if progress:
            progress({"phase": "grading", "term": term, "label": label,
                      "slot_label": slot_label})
        content = excel.fetch_excel(client, year, term, extra=extra)
        recs = excel.parse_excel(content, year, term) if content else []
        return {(c["sbjt_cd"], c["lt_no"], c["subh_cd"]) for c in recs}

    methods: dict[tuple, str] = {}
    for code, grading in GRADING_CODES:
        for k in sweep({"srchMrksGvMthd": code}, f"평가방식 {grading}"):
            methods[k] = grading

    total = conn.execute("SELECT COUNT(*) FROM classes WHERE year=? AND term=?",
                         (year, term)).fetchone()[0]
    if total and not methods:
        # Non-empty term but every filtered download came back empty: that is a
        # broken sweep (site hiccup / filter param change), not real data. Bail
        # BEFORE apply_grading so the existing tags aren't wiped to N/NULL.
        raise RuntimeError(
            f"grading sweep for {year}/{term} returned 0 classes "
            f"(term has {total}); leaving existing tags untouched")
    switchable = sweep(GRADING_SWITCH_PARAM, "평가방식 전환가능")

    tagged = db.apply_grading(conn, year, term, methods, switchable)
    untagged = conn.execute(
        "SELECT COUNT(*) FROM classes WHERE year=? AND term=? AND grading IS NULL",
        (year, term)).fetchone()[0]
    if untagged:
        # The 3 codes partition the roster, so any gap means the sweeps and the
        # catalog drifted (e.g. a class added between downloads) or a code was
        # added server-side. Loud but non-fatal: NULL renders as "no data".
        log.warning("grading %s/%s: %d/%d rows left untagged "
                    "(sweep/catalog drift or a new MRKS_GV_MTHD code?)",
                    year, term, untagged, total)
    return {"term": term, "tagged": tagged, "untagged": untagged,
            "switchable": len(switchable)}


def _search_times(client: SnuClient, year: str, term: str, *,
                  label: str = "", progress: ProgressFn | None = None) -> dict:
    """Paginate the full-term search and map (sbjt_cd, lt_no) -> [meeting blocks].
    Each result item carries its exact `요일(HH:MM~HH:MM)` schedule, so one paged
    sweep recovers all missing timing — far cheaper than the old per-cell sweep."""
    times: dict[tuple, list] = {}
    fetched = 0
    page = 1
    while True:
        res = parse.parse_response(client.search_page(year, term, page=page))
        for c in res["classes"]:
            blocks = c.get("time_blocks") or []
            if not blocks:
                continue
            seen = times.setdefault((c["sbjt_cd"], c["lt_no"]), [])
            for b in blocks:
                if b not in seen:
                    seen.append(b)
        fetched += res["page_count"]
        total = res["total"]
        if progress:
            progress({"phase": "search-timing", "term": term, "label": label,
                      "slot_index": fetched, "slot_total": total or fetched,
                      "slot_label": "검색 시간표 보강"})
        if res["page_count"] == 0 or fetched >= total or page > 200:
            break
        page += 1
    return times


def crawl_term(conn, client: SnuClient, year: str, term: str, *,
               label: str = "", live_counts: bool = True,
               collect_cart: bool = False, collect_enrollment: bool = True,
               collect_grading: bool = True,
               search_timing: bool = True,
               progress: ProgressFn | None = None) -> dict:
    """Rebuild one term from its Excel (catalog + exact slots). Where the Excel has
    no time (it lags), recover timing from the live search results. Then overlay
    the selected live metrics. Excel timing always overrides the search-derived
    timing."""
    db.upsert_term(conn, term, year, label or f"{year} {term}")
    if progress:
        progress({"phase": "excel", "term": term, "label": label,
                  "slot_label": "엑셀 다운로드"})
    content = excel.fetch_excel(client, year, term)
    recs = excel.parse_excel(content, year, term) if content else []

    classes = slot_rows = timeless = 0
    timeless_map: dict[tuple, int] = {}   # (sbjt_cd, lt_no) -> cid, for timing recovery
    for i, c in enumerate(recs):
        cid = db.upsert_class(conn, c)
        classes += 1
        if c["slots"]:
            for s in c["slots"]:
                if db.add_slot(conn, cid, s):
                    slot_rows += 1
        else:
            timeless += 1
            timeless_map[(c["sbjt_cd"], c["lt_no"])] = cid
        if progress and (i % 250 == 0 or i + 1 == len(recs)):
            progress({"term": term, "label": label, "slot_index": i + 1,
                      "slot_total": len(recs), "slot_label": "강좌 파싱",
                      "classes_so_far": classes, "slots_so_far": slot_rows})
    conn.commit()

    # Excel timing lags: if it has times for too few classes (timetable not yet
    # published, e.g. fall), recover the missing timing from the live search results.
    recovered = 0
    timed_ratio = (classes - timeless) / classes if classes else 1.0
    if (search_timing and timeless and timed_ratio < SEARCH_TIMING_MAX_RATIO):
        times = _search_times(client, year, term, label=label, progress=progress)
        for key, cid in timeless_map.items():
            blocks = times.get(key)
            if not blocks:
                continue
            for block in blocks:
                if db.add_slot(conn, cid, block):
                    slot_rows += 1
            recovered += 1
        timeless -= recovered
        conn.commit()

    counts = {"fetched": 0, "updated": 0}
    if live_counts and (collect_cart or collect_enrollment):
        try:
            counts = refresh_counts(conn, client, year, term,
                                    label=label, progress=progress,
                                    collect_cart=collect_cart,
                                    collect_enrollment=collect_enrollment)
        except Exception as e:  # noqa: BLE001 - counts are best-effort, Excel already landed
            counts = {"fetched": 0, "updated": 0, "error": str(e)}
    # 평가방식 tags are wiped with the rows on every rebuild, so re-sweep them here.
    # Best-effort like counts: a sweep failure must not fail an otherwise good crawl.
    grading = {"tagged": 0}
    if collect_grading:
        try:
            grading = refresh_grading(conn, client, year, term,
                                      label=label, progress=progress)
        except Exception as e:  # noqa: BLE001
            grading = {"tagged": 0, "error": str(e)}
    return {"term": term, "classes": classes, "slots": slot_rows,
            "timeless": timeless, "recovered": recovered,
            "counts_updated": counts["updated"],
            "grading_tagged": grading["tagged"]}


def refresh_all(conn, years: list[str], terms: list[str] | None = None, *,
                mint: Callable[[], dict] = snu_session.mint_session,
                live_counts: bool = True, search_timing: bool = True,
                force: bool = False, progress: ProgressFn | None = None,
                collect_cart: bool = False, collect_enrollment: bool = True,
                collect_grading: bool = True) -> dict:
    """Rebuild the selected terms and run the selected catalog/live/grading steps."""
    if not live_counts:
        collect_cart = False
        collect_enrollment = False
    client = SnuClient(mint=mint)
    wanted = set(terms or [])
    plan: list[dict] = []
    for y in years:
        for t in client.fetch_terms(y):
            if not wanted or t["term"] in wanted:
                plan.append(t)

    run_id = db.start_run(conn, [p["term"] for p in plan])
    year_terms = [(p["year"], p["term"]) for p in plan]
    # A catalog rebuild deletes and recreates class rows. Preserve the last
    # cart values when this pass deliberately excludes cart collection, so an
    # hourly full update cannot erase the newest bounded-window observation.
    saved_carts = (db.snapshot_cart_counts(conn, year_terms)
                   if not collect_cart else {})
    total_classes = total_slots = total_timeless = total_recovered = 0
    try:
        # Snapshot the OLD rows before the wipe so we can diff after the rebuild.
        old_snap = changelog.snapshot(conn, year_terms)
        db.clear_terms(conn, year_terms)
        conn.commit()
        for p in plan:
            if progress:
                progress({"phase": "term-start", "term": p["term"], "label": p["label"]})
            stats = crawl_term(conn, client, p["year"], p["term"],
                               label=p["label"], live_counts=live_counts,
                               collect_cart=collect_cart,
                               collect_enrollment=collect_enrollment,
                               collect_grading=collect_grading,
                               search_timing=search_timing,
                               progress=progress)
            total_classes += stats["classes"]
            total_slots += stats["slots"]
            total_timeless += stats["timeless"]
            total_recovered += stats["recovered"]
        if saved_carts:
            db.restore_cart_counts(conn, saved_carts)
        db.finish_run(conn, run_id, "done", "ok", total_classes, total_slots)
    except Exception as e:  # noqa: BLE001 - record failure for the admin UI
        db.finish_run(conn, run_id, "error", str(e), total_classes, total_slots)
        raise
    # Diff + change log is a non-critical side effect: the catalog is already
    # rebuilt and the run marked done, so never let a logging hiccup fail it.
    changes = logpath = None
    try:
        rows, changes = changelog.diff(old_snap, changelog.snapshot(conn, year_terms))
        logpath = changelog.write_run_log(run_id, rows, changes)
        log.info("update run %s: %d classes · %s",
                 run_id, total_classes, changelog.summary_line(changes, logpath))
    except Exception:  # noqa: BLE001 - change log must not break a good refresh
        log.exception("update run %s: change log failed", run_id)
    try:
        db.sample_counts(conn, year_terms,
                         **_sample_options(collect_cart=collect_cart,
                                           collect_enrollment=collect_enrollment,
                                           force=force))   # 인원 추이 sample
        if force:
            for (y, t) in year_terms:
                db.mark_closed(conn, y, t)
    except Exception:  # noqa: BLE001 - sampling must not break a good refresh
        log.exception("count sampling failed")
    return {"classes": total_classes, "slots": total_slots,
            "timeless": total_timeless, "recovered": total_recovered,
            "terms": [p["term"] for p in plan], "run_id": run_id,
            "changes": changes, "log": str(logpath) if logpath else None}


def refresh_counts_all(conn, years: list[str], terms: list[str] | None = None, *,
                       mint: Callable[[], dict] = snu_session.mint_session,
                       force: bool = False, progress: ProgressFn | None = None,
                       collect_cart: bool = True, collect_enrollment: bool = True,
                       cart_only: bool | None = None,
                       windowed: bool = False) -> dict:
    """Live-metric pass for the fast timer: no Excel download, just re-poll the
    search endpoint per stored term and overlay the selected metrics.

    A windowed pass exits before creating a session when its configured metric
    window is inactive. This keeps a generic timer from polling SNU off-season.
    """
    if cart_only is not None:
        collect_cart = True
        collect_enrollment = not cart_only
    if not collect_cart and not collect_enrollment:
        return {"updated": 0, "samples": 0, "terms": [],
                "skipped": "no live metrics selected"}
    if windowed and not _window_active(collect_cart=collect_cart,
                                      collect_enrollment=collect_enrollment):
        return {"updated": 0, "samples": 0, "terms": [],
                "skipped": "outside collection window"}
    client = SnuClient(mint=mint)
    wanted = set(terms or [])
    plan = [p for y in years for p in client.fetch_terms(y)
            if not wanted or p["term"] in wanted]
    total = 0
    for p in plan:
        out = refresh_counts(conn, client, p["year"], p["term"],
                             label=p["label"], progress=progress,
                             collect_cart=collect_cart,
                             collect_enrollment=collect_enrollment)
        total += out["updated"]
    # append one enrollment sample per class for the 인원 추이 time-series
    samples = 0
    try:
        sample_kwargs = _sample_options(collect_cart=collect_cart,
                                        collect_enrollment=collect_enrollment,
                                        force=force)
        samples = db.sample_counts(conn, [(p["year"], p["term"]) for p in plan],
                                   **sample_kwargs)
        if force:   # forced = past semester captured -> mark it closed (마감)
            for p in plan:
                db.mark_closed(conn, p["year"], p["term"])
    except Exception:  # noqa: BLE001 - sampling must not break a good counts pass
        log.exception("count sampling failed")
    return {"updated": total, "samples": samples, "terms": [p["term"] for p in plan]}


def refresh_grading_all(conn, years: list[str], terms: list[str] | None = None, *,
                        mint: Callable[[], dict] = snu_session.mint_session,
                        progress: ProgressFn | None = None) -> dict:
    """Grading-only pass over terms ALREADY in the DB: tag 평가방식/전환가능여부
    without touching the catalog, counts, or 인원 추이 samples. Backfills past
    semesters (the search endpoint serves old terms too)."""
    client = SnuClient(mint=mint)
    wanted_years = set(years or [])
    wanted_terms = set(terms or [])
    plan = [t for t in db.list_terms(conn)
            if (not wanted_years or t["year"] in wanted_years)
            and (not wanted_terms or t["term"] in wanted_terms)]
    out = []
    for t in plan:
        stats = refresh_grading(conn, client, t["year"], t["term"],
                                label=t["label"], progress=progress)
        log.info("grading %s: %d tagged, %d switchable",
                 t["label"], stats["tagged"], stats["switchable"])
        out.append(stats)
    return {"terms": [t["term"] for t in plan],
            "tagged": sum(s["tagged"] for s in out),
            "switchable": sum(s["switchable"] for s in out)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Crawl SNU sugang into the DB")
    ap.add_argument("--years", default="2026")
    ap.add_argument("--terms", default="", help="comma cmmnCd subset; blank=all")
    ap.add_argument(
        "--collect",
        action="append",
        metavar="COMPONENTS",
        help="comma-separated components: catalog, enrollment, cart, grading, or all; "
             "may be repeated",
    )
    ap.add_argument("--counts-only", action="store_true",
                    help="skip Excel, only refresh live enrollment counts")
    ap.add_argument("--cart-only", action="store_true",
                    help="counts-only mode: update and sample 장바구니 only")
    ap.add_argument("--windowed", action="store_true",
                    help="counts-only mode: skip when the configured metric window is inactive")
    ap.add_argument("--grading-only", action="store_true",
                    help="only (re)tag 평가방식/전환가능여부 for terms already in the DB "
                         "(backfill; touches nothing else)")
    ap.add_argument("--no-counts", action="store_true",
                    help="Excel only, skip the live count overlay")
    ap.add_argument("--no-search-timing", action="store_true",
                    help="skip recovering timing from search results when Excel lags")
    ap.add_argument("--force", action="store_true",
                    help="forced past-term update: ignore collection windows, "
                         "sample 수강 인원 only (no 장바구니)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the re-collect confirmation when a term is already 마감(closed)")
    return ap


def parse_args(argv: list[str] | None = None):
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    legacy_mode = (args.counts_only or args.cart_only or args.grading_only
                   or args.no_counts)
    if args.collect is not None and legacy_mode:
        ap.error("--collect cannot be combined with legacy mode flags")

    if args.collect is not None:
        try:
            selected = parse_collection_spec(args.collect)
        except ValueError as exc:
            ap.error(str(exc))
    elif args.grading_only:
        selected = frozenset(("grading",))
    elif args.counts_only:
        selected = frozenset(("cart",)) if args.cart_only else LIVE_COMPONENTS
    else:
        selected = DEFAULT_FULL_COMPONENTS
        if args.no_counts:
            selected = selected - LIVE_COMPONENTS

    if args.cart_only and not args.counts_only:
        ap.error("--cart-only requires --counts-only")
    if args.windowed and args.collect is None and not args.counts_only:
        ap.error("--windowed requires --counts-only")
    if args.windowed and not selected <= LIVE_COMPONENTS:
        ap.error("--windowed can only be used with enrollment/cart collection")
    if args.cart_only and args.force:
        ap.error("--cart-only cannot be combined with --force")
    if args.collect is not None and args.force and "cart" in selected:
        ap.error("--force cannot be combined with explicit cart collection")
    args.collections = frozenset(selected)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    lock = (nullcontext() if os.environ.get(process_lock.LOCK_HELD_ENV) == "1"
            else process_lock.ProcessLock())
    with lock:
        conn = db.connect()
        try:
            db.init_schema(conn)
            years = args.years.split(",")
            terms = [x for x in args.terms.split(",") if x]
            prog = lambda p: print(
                p.get("slot_label", p.get("phase", "")), p.get("label", ""),
                f"{p.get('slot_index', '')}/{p.get('slot_total', '')}",
                f"cls={p.get('classes_so_far', '')}", flush=True)
            if args.force and terms and not args.yes:
                closed = [(y, t) for y in years for t in terms if db.is_closed(conn, y, t)]
                if closed:
                    msg = ", ".join(f"{y}/{t}" for y, t in closed)
                    if sys.stdin.isatty():
                        ans = input(f"이미 마감(강제수집)된 학기: {msg}. 다시 강제 수집할까요? [y/N] ").strip().lower()
                        if ans not in ("y", "yes"):
                            print("취소했습니다."); return 0
                    else:
                        print(f"이미 마감된 학기: {msg}. 재수집하려면 --yes 를 붙이세요. 취소.", file=sys.stderr)
                        return 1
            selected = args.collections
            live = selected & LIVE_COMPONENTS
            if "catalog" in selected:
                out = refresh_all(
                    conn,
                    years,
                    terms=terms,
                    live_counts=bool(live),
                    collect_cart="cart" in live,
                    collect_enrollment="enrollment" in live,
                    collect_grading="grading" in selected,
                    search_timing=not args.no_search_timing,
                    force=args.force,
                    progress=prog,
                )
            elif live and "grading" in selected:
                out = {
                    "counts": refresh_counts_all(
                        conn,
                        years,
                        terms=terms,
                        force=args.force,
                        progress=prog,
                        collect_cart="cart" in live,
                        collect_enrollment="enrollment" in live,
                        windowed=args.windowed,
                    ),
                    "grading": refresh_grading_all(
                        conn, years, terms=terms, progress=prog
                    ),
                }
            elif live:
                out = refresh_counts_all(
                    conn,
                    years,
                    terms=terms,
                    force=args.force,
                    progress=prog,
                    collect_cart="cart" in live,
                    collect_enrollment="enrollment" in live,
                    windowed=args.windowed,
                )
            else:
                out = refresh_grading_all(conn, years, terms=terms, progress=prog)
            print("DONE:", out)
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
