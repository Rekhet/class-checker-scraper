# Cross-Semester Explore (Core) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **강의탐색 (Explore)** tab right of 시간표 that fuzzy-searches classes across all terms and drills into a per-`sbjt_cd` code page showing that code's full offering history, newest-first, each row annotated with professor + semester.

**Architecture:** A new build artifact `web/data/explore-index.json` (string-interned tables + integer-coded offering rows grouped by `sbjt_cd`) is emitted by `scraper/export_json.py`. A new client module in `web/app.js` lazy-loads it once, fuzzy-searches over per-code strings, and renders a code detail sub-view. A new partial `web/partials/explore.html` hosts both sub-views (search + detail); the hash router is extended to parse `route/param` so `#code/<sbjt_cd>` deep-links resolve to the detail view while nav highlighting stays on the Explore tab.

**Tech Stack:** Vanilla browser JS (classic `<script>`; all top-level `function`/`const` are globals on `window`; declarations hoist so module placement is free). No bundler, no JS test runner. Python 3 stdlib for the build (`scraper/export_json.py`, run via `make export-json`). DOM helpers in `web/app.js`: `$`/`$$` ([app.js:46](web/app.js:46)), `el(tag, props, ...kids)` ([app.js:48](web/app.js:48)), fuzzy scorer `nameScore(name, q)` ([app.js:107](web/app.js:107)).

## Global Constraints

- **No git commits.** This project is not a git repo; all changes stay in the working tree. Every task ends at a passing `preview_eval` and/or `python3 -m json.tool` check — a **working-tree checkpoint**, never a commit.
- **Verification is `preview_eval` + `python3 -m json.tool` only.** The preview tab is backgrounded (hidden): rAF-coalesced renders may not fire and `getBoundingClientRect` is degenerate. **Verify by calling functions directly and inspecting returned state/DOM strings — never by screenshot or awaited rAF.**
- **CAVEMAN prose, normal code.** User-facing chat prose is terse; all code, JSON, and file content is written normally.
- **Never hide an offering.** When a dedupe or unknown-term-code decision is ambiguous, keep the row. Explore is read-only; over-showing is safe.
- **Tab label is `강의탐색`, placed immediately after `시간표`** (i.e. after `partials/main.html` in the `data-partials` order).
- **Row schema is fixed:** `[termIdx, nameId, profId, deptId, credits, ltNo]`. Position 2 (`profId`) is emitted as a **single int** in Core but the client **normalizes it to an array on load** (Spec 2 will emit arrays for co-professors — no reshape).
- **Sort is ascending `termIdx`, then `ltNo`.** Because `terms[]` is newest-first (termIdx 0 = newest), ascending termIdx renders newest-offering-first with no client re-sort.
- **Artifact path is `web/data/explore-index.json`** (the `OUT` root, a cross-term artifact — **not** under `classes/`).

---

## Conventions for this plan (read first)

**No test runner exists.** "Tests" are (a) `python3` assertion snippets over the built JSON and (b) `preview_eval` snippets against the live page.

**Start the dev server once** before Task 2's first `preview_eval`, using `preview_start` (serves `web/index.html`). Reuse it for every later task. If a task changes `app.js`/partials/css, reload with `preview_eval` → `window.location.reload()` (or `preview_start` if not yet running) before checking.

**Build once per JSON change.** After any `export_json.py` edit, rebuild with `make export-json` (this needs the dev DB; it runs `.venv/bin/python scraper/export_json.py`). The build writes `web/data/explore-index.json` alongside the existing per-term files.

**`preview_eval` harness.** Every UI snippet must be self-contained and non-destructive: snapshot any global it mutates, run the check, restore in a `finally`. The Explore module state (`_EX`, `_exLoading`) is load-once cache — snippets may read it freely but must not null it out unless they restore it.

**Working-tree checkpoints, NOT commits.** Per Global Constraints, no task runs `git commit`. Each task's final step is its passing verification. Reviewers diff with `git diff` (repo root is the project dir; `web/` and `scraper/` are the touched trees).

**Term codes in snippets.** Real SNU term codes: `1학기` = `U000200001U000300001`, `여름학기` = `U000200001U000300002`, `2학기` = `U000200002U000300001`, `겨울학기` = `U000200002U000300002`.

---

## File Structure

- **Modify** `scraper/export_json.py` — add intern tables + code grouping accumulated during the existing term loop; emit `explore-index.json` after the loop. (Responsibility: build the cross-term artifact.)
- **Create** `web/partials/explore.html` — the `.page` shell for Explore: search sub-view + detail sub-view containers. (Responsibility: static markup only; all content built in JS.)
- **Modify** `web/index.html` — insert `partials/explore.html` into `#app[data-partials]` after `partials/main.html`.
- **Modify** `web/index-dev.html` — same insert (dev shell).
- **Modify** `web/app.js` — (1) router extension: `parseHash`/route dispatch; (2) new Explore module: lazy load + decode, fuzzy search render, code detail render.
- **Modify** `web/styles.css` — Explore search + detail styling.

The Makefile's `export-json` target already runs `scraper/export_json.py` unchanged — **no Makefile edit needed.**

---

## Task 1: Build the `explore-index.json` artifact

**Files:**
- Modify: `scraper/export_json.py:18-28` (imports + constants)
- Modify: `scraper/export_json.py:71-104` (`main()` — accumulate in the term loop, emit after it)
- Test: `python3` assertion snippet over `web/data/explore-index.json`

**Interfaces:**
- Consumes: `db.search(conn, year, term, limit=None)` rows — dicts with keys `sbjt_cd, name, professor, department, credits, lt_no` ([db.py:565](scraper/db.py:565), `SELECT *`); `db.list_terms(conn)` → `[{term, year, label}]` newest-first ([db.py:462](scraper/db.py:462)); existing `_write(path, obj)` ([export_json.py:65](scraper/export_json.py:65)).
- Produces: `web/data/explore-index.json` with top-level keys `version, generated, strings{names,profs,depts}, terms, codes[]`; each code `{c, names[], o[]}`; each offering row `[termIdx, nameId, profId, deptId, credits, ltNo]`.

- [ ] **Step 1: Write the failing acceptance check**

Create this snippet as the task's acceptance test and run it now (before the edit). Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import json, os, sys
p = "web/data/explore-index.json"
assert os.path.exists(p), "explore-index.json not built yet"
ex = json.load(open(p, encoding="utf-8"))
assert ex["version"] == 1
S = ex["strings"]; nN, nP, nD = len(S["names"]), len(S["profs"]), len(S["depts"])
nT = len(ex["terms"])
codes = ex["codes"]
assert len(codes) > 10000, f"too few codes: {len(codes)}"
total = sum(len(c["o"]) for c in codes)
assert total > 100000, f"too few offerings: {total}"
for c in codes[:2000]:
    assert isinstance(c["c"], str) and c["names"] and c["o"]
    prev = None
    for o in c["o"]:
        assert len(o) == 6, o
        ti, ni, pi, di, cr, lt = o
        assert 0 <= ti < nT and 0 <= ni < nN and 0 <= pi < nP and 0 <= di < nD
        assert isinstance(lt, str)
        if prev is not None: assert ti >= prev, "offerings not ascending-termIdx"   # newest-first
        prev = ti
    assert c["names"][0] == c["o"][0][1], "names[0] must be newest offering's nameId"
print(f"OK codes={len(codes)} offerings={total} names={nN} profs={nP} depts={nD} terms={nT}")
PY
```

Expected BEFORE the edit: `AssertionError: explore-index.json not built yet` (or `FileNotFoundError`).

- [ ] **Step 2: Add imports + an ISO-timestamp helper**

In `scraper/export_json.py`, the import block is at [export_json.py:18-24](scraper/export_json.py:18). Replace:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import db
```

with:

```python
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import db
```

Then, immediately after the existing `_write` function (ends at [export_json.py:68](scraper/export_json.py:68)), add:

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _export_explore(conn, terms, classes_dir_writer) -> None:
    """Build the cross-term Explore index: interned string tables + integer-coded
    offering rows grouped by sbjt_cd. Newest-first term order (matches list_terms)."""
    name_intern: dict[str, int] = {}
    prof_intern: dict[str, int] = {}
    dept_intern: dict[str, int] = {}

    def intern(tbl: dict[str, int], s: str) -> int:
        s = (s or "").strip()
        i = tbl.get(s)
        if i is None:
            i = tbl[s] = len(tbl)
        return i

    def keys_in_order(tbl: dict[str, int]) -> list[str]:
        out = [""] * len(tbl)
        for s, i in tbl.items():
            out[i] = s
        return out

    term_list: list[list] = []          # [[year, term-code], ...] newest-first
    codes: dict[str, dict] = {}         # sbjt_cd -> {"seen": {(termIdx, ltNo): rowIdx}, "o": [rows]}

    for term_idx, t in enumerate(terms):
        term_list.append([t["year"], t["term"]])
        rows = db.search(conn, year=t["year"], term=t["term"], limit=None)
        for r in rows:
            cd = r["sbjt_cd"]
            bucket = codes.get(cd)
            if bucket is None:
                bucket = codes[cd] = {"seen": {}, "o": []}
            row = [term_idx,
                   intern(name_intern, r.get("name", "")),
                   intern(prof_intern, r.get("professor", "")),
                   intern(dept_intern, r.get("department", "")),
                   r.get("credits") or 0,
                   r.get("lt_no", "")]
            key = (term_idx, row[5])                 # dedupe same (term, section) re-scrapes
            j = bucket["seen"].get(key)
            if j is None:
                bucket["seen"][key] = len(bucket["o"])
                bucket["o"].append(row)
            else:
                bucket["o"][j] = row                 # keep latest

    code_objs = []
    for cd, bucket in codes.items():
        offs = bucket["o"]
        offs.sort(key=lambda o: (o[0], o[5]))        # ascending termIdx (= newest-first), then section
        seen_names, ordered_names = set(), []
        for o in offs:
            if o[1] not in seen_names:
                seen_names.add(o[1]); ordered_names.append(o[1])
        code_objs.append({"c": cd, "names": ordered_names, "o": offs})

    explore = {
        "version": 1,
        "generated": _now_iso(),
        "strings": {"names": keys_in_order(name_intern),
                    "profs": keys_in_order(prof_intern),
                    "depts": keys_in_order(dept_intern)},
        "terms": term_list,
        "codes": code_objs,
    }
    size = classes_dir_writer(explore)
    print(f"  explore-index.json: {len(code_objs)} codes, "
          f"{sum(len(c['o']) for c in code_objs)} offerings ({size // 1024} KB)")
```

- [ ] **Step 3: Call the builder from `main()`**

In `main()`, the loop ends and `index.json` is written at [export_json.py:102-104](scraper/export_json.py:102). Right after the `_write(classes_dir / "index.json", meta)` line and its `print(...)`, add:

```python
    _export_explore(conn, terms, lambda obj: _write(OUT / "explore-index.json", obj))
```

(`terms` and `conn` are already in scope from `main()`; `OUT` is the module constant `web/data`. The `_write` lambda writes to the `OUT` root, not `classes_dir`.)

- [ ] **Step 4: Build**

Run:

```bash
cd /home/toxiclemon/project/class-checker && make export-json
```

Expected: the run prints per-term lines as before, then a final `  explore-index.json: <N> codes, <M> offerings (<K> KB)` line with N ≈ 13,521 and M ≈ 109,343.

- [ ] **Step 5: Validate JSON + run the acceptance check**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 -m json.tool web/data/explore-index.json > /dev/null && echo "JSON OK"
```

Expected: `JSON OK`.

Then re-run the Step 1 snippet. Expected AFTER: `OK codes=~13521 offerings=~109343 names=... profs=... depts=... terms=28` (no AssertionError). **Working-tree checkpoint.**

---

## Task 2: Mount the Explore partial + nav tab

**Files:**
- Create: `web/partials/explore.html`
- Modify: `web/index.html:10` (`#app[data-partials]`)
- Modify: `web/index-dev.html:14` (`#app[data-partials]`)
- Test: `preview_eval`

**Interfaces:**
- Consumes: `loader.js` mounts each partial listed in `#app[data-partials]` (comma-separated, in order), then loads `app.js`; `setupNav` ([app.js:2762](web/app.js:2762)) builds a nav link per `.page` whose `data-nav !== "false"`.
- Produces: a `.page` with `data-page="explore"`, `data-title="강의탐색"`, containing `#exploreSearch` (with `#exploreQ` input + `#exploreResults`) and `#exploreDetail`.

- [ ] **Step 1: Verify the tab is absent before the change**

Start the dev server (`preview_start`). Then `preview_eval`:

```js
JSON.stringify({
  hasExplorePage: !!document.querySelector('.page[data-page="explore"]'),
  navLabels: [...document.querySelectorAll('#topnav .nav-link')].map(n => n.textContent)
});
```

Expected BEFORE: `{"hasExplorePage":false,"navLabels":["시간표","인원 추이","졸업요건"]}` (no 강의탐색).

- [ ] **Step 2: Create the Explore partial**

Create `web/partials/explore.html`:

```html
<!-- 강의탐색 (cross-semester Explore): fuzzy class/code search over the prebuilt
     data/explore-index.json, plus a per-sbjt_cd code detail page. One .page with two
     sub-views (#exploreSearch / #exploreDetail) toggled by the router. All content is
     built in app.js (renderExplore / renderExploreSearch / renderCodeDetail). -->
<div class="page" data-page="explore" data-title="강의탐색">
<div class="tt-wrap explore-wrap">

  <div id="exploreSearch" class="explore-view">
    <div class="explore-head">
      <input id="exploreQ" class="explore-q" type="search" autocomplete="off"
             placeholder="과목명 또는 과목코드로 전체 학기 검색" aria-label="강의탐색">
      <span id="exploreCount" class="explore-count"></span>
    </div>
    <div id="exploreResults" class="explore-results"></div>
  </div>

  <div id="exploreDetail" class="explore-view hidden"></div>

</div>
</div>
```

- [ ] **Step 3: Insert the partial into both shells**

In `web/index.html:10`, replace:

```html
<div id="app" data-partials="partials/main.html,partials/trend.html,partials/grad.html,partials/legal.html"></div>
```

with:

```html
<div id="app" data-partials="partials/main.html,partials/explore.html,partials/trend.html,partials/grad.html,partials/legal.html"></div>
```

In `web/index-dev.html:14`, replace:

```html
<div id="app" data-partials="partials/main.html,partials/dev.html,partials/trend.html,partials/grad.html,partials/legal.html"></div>
```

with:

```html
<div id="app" data-partials="partials/main.html,partials/explore.html,partials/dev.html,partials/trend.html,partials/grad.html,partials/legal.html"></div>
```

(Explore goes right after `main.html` in both — immediately after 시간표, before dev/trend.)

- [ ] **Step 4: Verify the tab mounts in the right position**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
JSON.stringify({
  hasExplorePage: !!document.querySelector('.page[data-page="explore"]'),
  navLabels: [...document.querySelectorAll('#topnav .nav-link')].map(n => n.textContent),
  hasSearchBox: !!document.querySelector('#exploreQ'),
  hasDetail: !!document.querySelector('#exploreDetail')
});
```

Expected AFTER: `{"hasExplorePage":true,"navLabels":["시간표","강의탐색","인원 추이","졸업요건"],"hasSearchBox":true,"hasDetail":true}` — 강의탐색 sits immediately after 시간표. **Working-tree checkpoint.**

---

## Task 3: Router extension (route/param split)

**Files:**
- Modify: `web/app.js:2752-2775` (`showPage` + `setupNav`)
- Test: `preview_eval`

**Interfaces:**
- Consumes: `location.hash`; existing `showPage(name)` behavior (toggles `.active`, highlights nav, lazy-inits trend/grad).
- Produces: `parseHash()` → `{route, param}`; a `route()` dispatcher wired to `hashchange` + initial load; a global `renderExplore(route, param)` **stub** in this task (real render lands in Tasks 5-6). `#code/<sbjt_cd>` shows the `explore` page with nav highlighted.

- [ ] **Step 1: Verify current router treats `#code/x` as unknown**

`preview_eval`:

```js
location.hash = "#code/M3502.019800";
const active = [...document.querySelectorAll('.page')].find(p => p.classList.contains('active'));
const out = JSON.stringify({ activePage: active?.dataset.page });
location.hash = "#timetable";   // restore
out;
```

Expected BEFORE: `{"activePage":"timetable"}` — the unknown `code/...` hash falls back to the first page. (Confirms no param routing yet.)

- [ ] **Step 2: Add `parseHash` + a route map above `showPage`**

In `web/app.js`, immediately before `function showPage(name) {` ([app.js:2752](web/app.js:2752)), insert:

```js
// Hash routing: a bare "#trend" is route "trend"/no-param; "#code/M3502.019800"
// is route "code"/param "M3502.019800". Param-routes render a sub-view inside a
// host page (code -> explore). Spec 2 adds { prof: "explore" }.
const PAGE_FOR_ROUTE = { code: "explore" };
function parseHash() {
  const raw = (location.hash || "").slice(1);
  const i = raw.indexOf("/");
  if (i === -1) return { route: raw, param: "" };
  return { route: raw.slice(0, i), param: decodeURIComponent(raw.slice(i + 1)) };
}
```

- [ ] **Step 3: Add the `route()` dispatcher and a `renderExplore` stub**

Immediately after `showPage`'s closing brace ([app.js:2761](web/app.js:2761), the `}` before `function setupNav`), insert:

```js
function route() {
  const { route: r, param } = parseHash();
  const page = PAGE_FOR_ROUTE[r] || r || (($$(".page")[0] || {}).dataset?.page);
  showPage(page);
  if (page === "explore") renderExplore(r, param);
}
// Replaced by the real renderer in the Explore module (Tasks 5-6); declared here so
// route() resolves during Task 3. Function declarations hoist, so the later
// definition wins regardless of file order.
function renderExplore(_route, _param) { /* stub: real render added in Task 5 */ }
```

- [ ] **Step 4: Wire `route()` into `setupNav`**

In `setupNav`, replace these two lines ([app.js:2773-2774](web/app.js:2773)):

```js
  window.addEventListener("hashchange", () => showPage((location.hash || "").slice(1)));
  showPage((location.hash || "").slice(1) || ($$(".page")[0] || {}).dataset?.page);
```

with:

```js
  window.addEventListener("hashchange", route);
  route();
```

- [ ] **Step 5: Verify `#code/x` now resolves to the Explore page + nav highlight**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
location.hash = "#code/M3502.019800";
const active = [...document.querySelectorAll('.page')].find(p => p.classList.contains('active'));
const navOn = [...document.querySelectorAll('#topnav .nav-link.active')].map(n => n.textContent);
const parsed = parseHash();
const out = JSON.stringify({ activePage: active?.dataset.page, navOn, parsed });
location.hash = "#timetable";
out;
```

Expected AFTER: `{"activePage":"explore","navOn":["강의탐색"],"parsed":{"route":"code","param":"M3502.019800"}}`.

Also verify bare routes still work — `preview_eval`:

```js
location.hash = "#trend";
const a = [...document.querySelectorAll('.page')].find(p => p.classList.contains('active'))?.dataset.page;
location.hash = "#timetable";
a;
```

Expected: `"trend"`. **Working-tree checkpoint.**

---

## Task 4: Explore data load + decode (`_EX` module state)

**Files:**
- Modify: `web/app.js` — append the Explore module after `flushUI` (before `addEventListener("pagehide", flushMeta);`, [app.js:2867](web/app.js:2867))
- Test: `preview_eval`

**Interfaces:**
- Consumes: `fetch("data/explore-index.json")` (mirrors `dataIndex()` at [app.js:65](web/app.js:65)); the artifact from Task 1.
- Produces: `ensureExploreData()` → Promise resolving to `_EX = { version, generated, names, profs, depts, terms, codes, byCode }` where `byCode` is `Map(sbjt_cd → codeObj)`, each `codeObj` gains `.q` (lowercased searchable string) and `.o` rows keep raw form; `exProfs(row)` → array of profId (normalizes int-or-array); `exSemLabel(termIdx)` → Korean term label.

- [ ] **Step 1: Verify the module is absent**

`preview_eval`:

```js
JSON.stringify({ hasEnsure: typeof window.ensureExploreData, hasEX: typeof window._EX });
```

Expected BEFORE: `{"hasEnsure":"undefined","hasEX":"undefined"}`.

- [ ] **Step 2: Append the Explore data module**

In `web/app.js`, immediately before the line `addEventListener("pagehide", flushMeta);` ([app.js:2867](web/app.js:2867)), insert:

```js
// ==================== 강의탐색 (Explore) ====================
// Cross-semester class/code search + per-sbjt_cd code page, built from the prebuilt
// data/explore-index.json (interned string tables + integer-coded offering rows).
let _EX = null;             // decoded index (load-once cache)
let _exLoading = null;      // in-flight fetch promise (dedupes concurrent callers)

// Row position 2 is an int OR an array of ints (Spec 2 co-professors). Always read
// as an array so the schema never reshapes.
function exProfs(row) {
  const p = row[2];
  return Array.isArray(p) ? p : [p];
}

// Known SNU term codes -> Korean short label (reuses the app's SEMESTER_LABEL map).
function exSemLabel(termIdx) {
  const t = _EX && _EX.terms[termIdx];
  if (!t) return "";
  const [year, code] = t;
  const lbl = (SEMESTER_LABEL[code] || "").split(" ")[0] || code;
  return `${year} ${lbl}`;
}

async function ensureExploreData() {
  if (_EX) return _EX;
  if (_exLoading) return _exLoading;
  _exLoading = (async () => {
    const raw = await fetch("data/explore-index.json").then((r) => {
      if (!r.ok) throw new Error(`explore-index ${r.status}`);
      return r.json();
    });
    const byCode = new Map();
    for (const c of raw.codes) {
      // searchable string built once: all distinct names for this code + the code itself
      c.q = (c.names.map((i) => raw.strings.names[i]).join(" ") + " " + c.c).toLowerCase();
      byCode.set(c.c, c);
    }
    _EX = {
      version: raw.version, generated: raw.generated,
      names: raw.strings.names, profs: raw.strings.profs, depts: raw.strings.depts,
      terms: raw.terms, codes: raw.codes, byCode,
    };
    return _EX;
  })();
  return _exLoading;
}
```

- [ ] **Step 3: Verify the data loads + decodes**

`preview_eval`:

```js
(async () => {
  const ex = await ensureExploreData();
  const c0 = ex.codes[0];
  return JSON.stringify({
    version: ex.version,
    nCodes: ex.codes.length,
    nTerms: ex.terms.length,
    byCodeIsMap: ex.byCode instanceof Map,
    byCodeHit: ex.byCode.get(c0.c) === c0,
    c0HasQ: typeof c0.q === "string" && c0.q.length > 0,
    firstProfsArr: JSON.stringify(exProfs(c0.o[0])),
    firstSem: exSemLabel(c0.o[0][0]),
    profName: ex.profs[exProfs(c0.o[0])[0]]
  });
})();
```

Expected AFTER: a JSON object with `version:1`, `nCodes` ≈ 13521, `nTerms:28`, `byCodeIsMap:true`, `byCodeHit:true`, `c0HasQ:true`, `firstProfsArr` like `"[3]"` (a one-element array), `firstSem` like `"2026 2학기"`, and `profName` a non-empty string (or `"정보 없음"`-eligible empty — see Task 6). **Working-tree checkpoint.**

---

## Task 5: Search view (fuzzy) + result rows

**Files:**
- Modify: `web/app.js` — replace the `renderExplore` stub (from Task 3) and add search helpers, inside/after the Explore module
- Test: `preview_eval`

**Interfaces:**
- Consumes: `ensureExploreData()`, `exProfs`, `exSemLabel`, `nameScore(name, q)` ([app.js:107](web/app.js:107)), `el`/`$` helpers, `#exploreSearch`/`#exploreDetail`/`#exploreQ`/`#exploreResults`/`#exploreCount`.
- Produces: real `renderExplore(route, param)` (dispatches search vs detail); `exSearch(q)` → ranked array of codeObjs; `renderExploreSearch()` binds the input + renders result rows. Clicking a row sets `location.hash = "#code/" + encodeURIComponent(sbjt_cd)`.

- [ ] **Step 1: Verify the stub renders nothing**

`preview_eval`:

```js
location.hash = "#explore";
await ensureExploreData();
const out = document.querySelector('#exploreResults')?.childElementCount ?? -1;
location.hash = "#timetable";
JSON.stringify({ resultCount: out });
```

Expected BEFORE: `{"resultCount":0}` — the stub does not populate results.

- [ ] **Step 2: Replace the stub with the real dispatcher + search render**

In `web/app.js`, delete the Task 3 stub block:

```js
// Replaced by the real renderer in the Explore module (Tasks 5-6); declared here so
// route() resolves during Task 3. Function declarations hoist, so the later
// definition wins regardless of file order.
function renderExplore(_route, _param) { /* stub: real render added in Task 5 */ }
```

Then, at the end of the Explore module (after `ensureExploreData`, before the `addEventListener("pagehide"...` line), append:

```js
const EX_MAX_RESULTS = 50;
let _exSearchBound = false;

// Rank codes for a query: fuzzy over the prebuilt per-code searchable string, with a
// strong boost when the query is a case-insensitive prefix/exact of the sbjt_cd.
function exSearch(q) {
  if (!_EX) return [];
  const query = (q || "").trim();
  const ql = query.toLowerCase();
  const scored = [];
  for (const c of _EX.codes) {
    let s = nameScore(c.q, query);
    if (ql && c.c.toLowerCase().startsWith(ql)) s += 6;   // exact/prefix code match wins
    if (s > 0) scored.push([s, c]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, EX_MAX_RESULTS).map((x) => x[1]);
}

function exResultRow(c) {
  const name = _EX.names[c.names[0]] || "(이름 없음)";
  const top = c.o[0];                                   // newest offering
  const dept = _EX.depts[top[3]] || "정보 없음";
  const sem = exSemLabel(top[0]);
  const profs = exProfs(top).map((i) => _EX.profs[i] || "정보 없음").join(", ");
  const row = el("a", { className: "ex-row", href: "#code/" + encodeURIComponent(c.c) },
    el("span", { className: "ex-name" }, name),
    el("span", { className: "ex-code" }, c.c),
    el("span", { className: "ex-dept" }, dept),
    el("span", { className: "ex-meta" }, `${sem} · ${profs}`));
  row.onclick = (e) => { e.preventDefault(); location.hash = "#code/" + encodeURIComponent(c.c); };
  return row;
}

function renderExploreResults() {
  const q = $("#exploreQ")?.value || "";
  const box = $("#exploreResults"); if (!box) return;
  const hits = exSearch(q);
  box.replaceChildren(...hits.map(exResultRow));
  const count = $("#exploreCount");
  if (count) count.textContent = q.trim() ? `${hits.length}개 결과${hits.length >= EX_MAX_RESULTS ? "+" : ""}` : "";
}

function renderExploreSearch() {
  $("#exploreSearch")?.classList.remove("hidden");
  $("#exploreDetail")?.classList.add("hidden");
  if (!_exSearchBound) {
    const input = $("#exploreQ");
    if (input) {
      let t = 0;
      input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(renderExploreResults, 120); });
      _exSearchBound = true;
    }
  }
  renderExploreResults();
}

async function renderExplore(route, param) {
  await ensureExploreData();
  if (route === "code" && param) return renderCodeDetail(param);
  return renderExploreSearch();
}
```

(`renderCodeDetail` is defined in Task 6; it's a hoisted function declaration, so `renderExplore` resolves it. If executing Task 5 before Task 6 and testing the `code` route, add a temporary no-op — but the search tests below only exercise `#explore`.)

- [ ] **Step 3: Verify search returns ranked results and code-prefix wins**

`preview_eval`:

```js
(async () => {
  location.hash = "#explore";
  await ensureExploreData();
  // pick a real code + its current name from the loaded index
  const sample = _EX.codes.find(c => (_EX.names[c.names[0]] || "").length >= 3);
  const name = _EX.names[sample.names[0]];
  const byName = exSearch(name).slice(0, 10).map(c => c.c);
  const byCode = exSearch(sample.c);
  const res = JSON.stringify({
    nameQueryFindsCode: byName.includes(sample.c),
    codeQueryRanksExactFirst: byCode[0]?.c === sample.c
  });
  location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `{"nameQueryFindsCode":true,"codeQueryRanksExactFirst":true}`.

- [ ] **Step 4: Verify the DOM renders result rows with name + code + dept**

`preview_eval`:

```js
(async () => {
  location.hash = "#explore";
  await ensureExploreData();
  const sample = _EX.codes.find(c => (_EX.names[c.names[0]] || "").length >= 3);
  $("#exploreQ").value = _EX.names[sample.names[0]];
  renderExploreResults();                    // call directly (debounce bypass)
  const rows = [...document.querySelectorAll('#exploreResults .ex-row')];
  const first = rows[0];
  const res = JSON.stringify({
    rowCount: rows.length,
    firstHasName: !!first?.querySelector('.ex-name')?.textContent,
    firstHasCode: !!first?.querySelector('.ex-code')?.textContent,
    firstHasDept: !!first?.querySelector('.ex-dept')?.textContent,
    firstHref: first?.getAttribute('href')
  });
  $("#exploreQ").value = ""; renderExploreResults(); location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `rowCount` ≥ 1, all `firstHas*` true, `firstHref` starts with `#code/`. **Working-tree checkpoint.**

---

## Task 6: Code detail page (`#code/<sbjt_cd>`)

**Files:**
- Modify: `web/app.js` — add `renderCodeDetail` in the Explore module
- Test: `preview_eval`

**Interfaces:**
- Consumes: `_EX.byCode`, `exProfs`, `exSemLabel`, `el`/`$`; `#exploreDetail`/`#exploreSearch`.
- Produces: `renderCodeDetail(sbjt_cd)` — toggles to the detail sub-view; renders header (current name, code, depts), rename notice, newest-first offering table (semester, professor(s), dept, section, credits), a **reserved empty container** `#exCodeLinks` for Spec 3, and a not-found state. Called by `renderExplore` for the `code` route.

- [ ] **Step 1: Verify detail is unimplemented**

`preview_eval`:

```js
JSON.stringify({ hasDetailFn: typeof window.renderCodeDetail });
```

Expected BEFORE: `{"hasDetailFn":"undefined"}`.

- [ ] **Step 2: Add `renderCodeDetail`**

In `web/app.js`, at the end of the Explore module (after `renderExplore`, before the `addEventListener("pagehide"...` line), append:

```js
function exProfNames(row) {
  const names = exProfs(row).map((i) => _EX.profs[i] || "").filter(Boolean);
  return names.length ? names.join(", ") : "정보 없음";
}

function renderCodeDetail(sbjt_cd) {
  $("#exploreSearch")?.classList.add("hidden");
  const box = $("#exploreDetail"); if (!box) return;
  box.classList.remove("hidden");

  const c = _EX.byCode.get(sbjt_cd);
  if (!c) {
    box.replaceChildren(
      el("div", { className: "ex-empty" },
        el("p", {}, "해당 과목코드를 찾을 수 없습니다."),
        el("a", { className: "ex-back", href: "#explore" }, "← 강의탐색으로")));
    return;
  }

  const curName = _EX.names[c.names[0]] || "(이름 없음)";
  const depts = [...new Set(c.o.map((o) => _EX.depts[o[3]]).filter(Boolean))].join(", ") || "정보 없음";

  const header = el("div", { className: "ex-detail-head" },
    el("a", { className: "ex-back", href: "#explore" }, "← 강의탐색"),
    el("h2", { className: "ex-title" }, curName),
    el("div", { className: "ex-sub" }, `${sbjt_cd} · ${depts}`));

  // rename notice: older distinct names (newest-first, skipping current)
  const older = c.names.slice(1).map((i) => _EX.names[i]).filter(Boolean);
  const rename = older.length
    ? el("div", { className: "ex-rename" }, "이전 명칭: " + older.join(", "))
    : el("div", { className: "ex-rename hidden" });

  // reserved slot for Spec 3 prev/next code links — rendered empty in Core.
  const links = el("div", { id: "exCodeLinks", className: "ex-code-links" });

  const table = el("div", { className: "ex-hist" });
  for (const o of c.o) {                              // already newest-first from build
    table.append(el("div", { className: "ex-hrow" },
      el("span", { className: "ex-h-sem" }, exSemLabel(o[0])),
      el("span", { className: "ex-h-prof" }, exProfNames(o)),
      el("span", { className: "ex-h-dept" }, _EX.depts[o[3]] || "정보 없음"),
      el("span", { className: "ex-h-sec" }, o[5] || "-"),
      el("span", { className: "ex-h-cr" }, (o[4] || 0) + "학점")));
  }

  box.replaceChildren(header, rename, links, table);
  window.scrollTo(0, 0);
}
```

- [ ] **Step 3: Verify a known code renders header + newest-first history**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  // pick a code with >=2 offerings so ordering is testable
  const c = _EX.codes.find(x => x.o.length >= 2);
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const d = document.querySelector('#exploreDetail');
  const sems = [...d.querySelectorAll('.ex-hrow .ex-h-sem')].map(s => s.textContent);
  const termIdxOrder = c.o.map(o => o[0]);
  const ascending = termIdxOrder.every((v, i, a) => i === 0 || a[i - 1] <= v);
  const res = JSON.stringify({
    title: d.querySelector('.ex-title')?.textContent,
    codeShown: d.querySelector('.ex-sub')?.textContent.includes(c.c),
    rowCount: d.querySelectorAll('.ex-hrow').length,
    offeringCount: c.o.length,
    hasReservedSlot: !!d.querySelector('#exCodeLinks'),
    newestFirst: ascending,       // ascending termIdx == newest-first
    firstSem: sems[0]
  });
  location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `title` = the code's current name, `codeShown:true`, `rowCount === offeringCount`, `hasReservedSlot:true`, `newestFirst:true`, `firstSem` a valid label.

- [ ] **Step 4: Verify rename notice + not-found state**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const renamed = _EX.codes.find(x => x.names.length >= 2);
  location.hash = "#code/" + encodeURIComponent(renamed.c);
  await renderExplore("code", renamed.c);
  const renameShown = !document.querySelector('#exploreDetail .ex-rename')?.classList.contains('hidden');
  // not-found
  await renderExplore("code", "___NO_SUCH_CODE___");
  const notFound = !!document.querySelector('#exploreDetail .ex-empty');
  location.hash = "#timetable";
  return JSON.stringify({ renameShown, notFound });
})();
```

Expected AFTER: `{"renameShown":true,"notFound":true}`.

- [ ] **Step 5: Verify cold deep-link load**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  return _EX.codes.find(x => x.o.length >= 2).c;   // grab a real code string
})();
```

Take the returned code, then `preview_eval` to simulate a cold load:

```js
location.hash = "#code/PASTE_CODE_HERE";
window.location.reload();
```

After reload, `preview_eval`:

```js
JSON.stringify({
  activePage: [...document.querySelectorAll('.page')].find(p => p.classList.contains('active'))?.dataset.page,
  detailRendered: !!document.querySelector('#exploreDetail .ex-title'),
  navOn: [...document.querySelectorAll('#topnav .nav-link.active')].map(n => n.textContent)
});
```

Expected AFTER: `{"activePage":"explore","detailRendered":true,"navOn":["강의탐색"]}` — the detail view renders on cold load without visiting `#explore` first. Then `preview_eval` → `location.hash = "#timetable"`. **Working-tree checkpoint.**

---

## Task 7: Explore styles

**Files:**
- Modify: `web/styles.css` (append at end, after [styles.css:581](web/styles.css:581))
- Test: `preview_eval`

**Interfaces:**
- Consumes: existing CSS variables (`--cp-line`, `--cp-accent`, `--cp-ink`, `--cp-muted`) already used throughout `styles.css`.
- Produces: layout for `.explore-wrap`, `.explore-head`, `.explore-q`, `.ex-row`, `.ex-hist`, `.ex-hrow`, `.ex-rename`, `.ex-empty`.

- [ ] **Step 1: Append Explore styles**

At the end of `web/styles.css`, append:

```css
/* ==================== 강의탐색 (Explore) ==================== */
.explore-wrap { max-width: 860px; margin: 0 auto; }
.explore-view.hidden { display: none; }
.explore-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.explore-q { flex: 1; padding: 9px 13px; border: 1px solid var(--cp-line); border-radius: 8px;
  font-size: 14px; background: transparent; color: var(--cp-ink); }
.explore-q:focus { outline: none; border-color: var(--cp-accent); }
.explore-count { font-size: 12px; color: var(--cp-muted); white-space: nowrap; }

.explore-results { display: flex; flex-direction: column; gap: 2px; }
.ex-row { display: grid; grid-template-columns: 1fr auto; grid-template-areas: "name code" "dept meta";
  gap: 1px 10px; padding: 9px 11px; border-radius: 8px; text-decoration: none; color: var(--cp-ink);
  border: 1px solid transparent; }
.ex-row:hover { border-color: var(--cp-line); background: color-mix(in srgb, var(--cp-accent) 6%, transparent); }
.ex-name { grid-area: name; font-weight: 600; }
.ex-code { grid-area: code; font-size: 12px; color: var(--cp-muted); font-variant-numeric: tabular-nums; }
.ex-dept { grid-area: dept; font-size: 12px; color: var(--cp-muted); }
.ex-meta { grid-area: meta; font-size: 12px; color: var(--cp-muted); text-align: right; }

.ex-detail-head { margin-bottom: 10px; }
.ex-back { font-size: 12px; color: var(--cp-accent); text-decoration: none; }
.ex-back:hover { text-decoration: underline; }
.ex-title { font-size: 19px; margin: 8px 0 2px; color: var(--cp-ink); }
.ex-sub { font-size: 13px; color: var(--cp-muted); font-variant-numeric: tabular-nums; }
.ex-rename { font-size: 12.5px; color: var(--cp-muted); margin: 6px 0; }
.ex-rename.hidden { display: none; }
.ex-code-links:empty { display: none; }

.ex-hist { margin-top: 12px; display: flex; flex-direction: column; gap: 1px; }
.ex-hrow { display: grid; grid-template-columns: 110px 1fr auto 52px 60px; gap: 10px; align-items: baseline;
  padding: 6px 8px; border-top: 1px solid var(--cp-line); font-variant-numeric: tabular-nums; }
.ex-hrow:first-child { border-top: none; }
.ex-h-sem { font-weight: 600; color: var(--cp-ink); }
.ex-h-prof { color: var(--cp-ink); }
.ex-h-dept { color: var(--cp-muted); font-size: 12.5px; }
.ex-h-sec { color: var(--cp-muted); font-size: 12px; text-align: right; }
.ex-h-cr { color: var(--cp-muted); font-size: 12px; text-align: right; }

.ex-empty { padding: 40px 0; text-align: center; color: var(--cp-muted); }
.ex-empty .ex-back { display: inline-block; margin-top: 8px; }

@media (max-width: 620px) {
  .ex-hrow { grid-template-columns: 90px 1fr auto; }
  .ex-hrow .ex-h-sec, .ex-hrow .ex-h-cr { display: none; }
}
```

- [ ] **Step 2: Verify styles apply**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const c = _EX.codes.find(x => x.o.length >= 2);
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const hrow = document.querySelector('#exploreDetail .ex-hrow');
  const disp = hrow ? getComputedStyle(hrow).display : null;
  location.hash = "#timetable";
  return JSON.stringify({ hrowDisplay: disp });   // grid layout applied
})();
```

Expected AFTER: `{"hrowDisplay":"grid"}` (the `.ex-hrow` rule is in effect). **Working-tree checkpoint.**

---

## Final verification (whole feature)

- [ ] **Build integrity:** `python3 -m json.tool web/data/explore-index.json > /dev/null` passes; the Task 1 acceptance snippet passes (counts + id ranges + newest-first ordering).
- [ ] **Nav:** `강의탐색` appears immediately after `시간표`; highlighted on both `#explore` and `#code/...`.
- [ ] **Search:** typing a known class name surfaces its code; typing a `sbjt_cd` ranks the exact code first.
- [ ] **Code page:** `#code/<known>` renders header + newest-first history (row count == offering count); rename notice shows for a ≥2-name code; reserved `#exCodeLinks` container present (empty).
- [ ] **Deep-link cold load:** reloading on `#code/<known>` renders the detail view directly.
- [ ] **No regressions:** `#timetable`, `#trend`, `#grad`, `#legal` still route and render.

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (preview tab hidden — no screenshots).

## Handoff to Spec 2 / Spec 3

- **Spec 2** swaps `strings.profs` (raw names) for a professor-identity table and begins emitting arrays at row position 2; it extends `PAGE_FOR_ROUTE` with `{ prof: "explore" }`, adds professor result rows to `exSearch`/render, makes `exProfNames` link to `#prof/<id>`, and reuses `exProfs` (already array-normalized).
- **Spec 3** fills the reserved `#exCodeLinks` container on the code page and adds `prev`/`next` arrays to each `codes[]` object in the build.
