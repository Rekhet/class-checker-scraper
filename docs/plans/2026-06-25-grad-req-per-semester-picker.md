# 졸업요건 per-semester timetable picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the graduation-requirements flat multi-sheet picker with a per-semester picker where the user adds semesters and chooses exactly one timetable per semester, auditing the union of those picks.

**Architecture:** Change `_gradState`'s selection field from a flat `sheets: [ids]` array to a `picks: { [semKey]: sheetId }` map, migrate old state to blank, derive the audit id list from the map, and rebuild `_renderGradSheets` as an add-a-row UI (per-semester rows + a `+ 학기 추가` dropdown). All in `web/app.js`; row styling in `web/styles.css`. The audit pipeline (`_gradTaken`, `renderGrad`, `partials/grad.html`) is untouched.

**Tech Stack:** Vanilla browser JS (classic `<script>`, all top-level `function`/`let` are global on `window`). No bundler, no JS test runner. Dev server is `web/serve.sh`. DOM helper `el(tag, props, ...kids)` ([app.js:48](web/app.js:48)) assigns `props` as element properties via `Object.assign`.

---

## Conventions for this plan (read first)

**No test runner exists.** "Tests" here are `preview_eval` snippets run against the live page. The hidden-preview memory applies: the preview tab is backgrounded, so rAF-coalesced renders may not fire and `getBoundingClientRect` is degenerate — **verify by calling functions directly and inspecting state/DOM strings, never by screenshot or awaited rAF.**

**Start the dev server once** (before Task 1 verification) with `preview_start` (runs `web/serve.sh`, serves `index.html`). Reuse it for every task.

**Verification harness.** Every snippet must be self-contained and non-destructive: snapshot the globals it touches, build a throwaway `#gradSheetPick` if absent, run the check, then restore. Template used throughout:

```js
// SNAPSHOT
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
try {
  // ... seed meta/_gradState, call functions, build result string ...
} finally { _restore(); }
```

**Commits: do NOT commit.** Per the user's standing directive, changes stay in the working tree — no `git commit` in any task. Each task ends at a passing `preview_eval` check. Reviewers diff the working tree with `git diff` (repo root is `web/`).

**Term codes in snippets.** Use real SNU term codes from `TERM_ORDER` ([app.js:240](web/app.js:240)): Fall = `U000200002U000300001`, Spring = `U000200001U000300001`. A semKey is `"2026|U000200002U000300001"`.

---

## Task 1: Data model + migration (`picks` replaces `sheets`)

**Files:**
- Modify: `web/app.js:2179` (state-shape comment)
- Modify: `web/app.js:2183` (`_gradState` init + migration guard)

- [ ] **Step 1: Verify the pre-change state fails the target**

Start the dev server (`preview_start`), then `preview_eval`:

```js
JSON.stringify({ hasPicks: !!_gradState.picks, hasSheetsField: ('sheets' in _gradState) });
```

Expected BEFORE change: `{"hasPicks":false,"hasSheetsField":true}` (picks absent; legacy field present).

- [ ] **Step 2: Update the state-shape comment**

In `web/app.js:2179`, replace:

```js
const GRAD_STATE_KEY = "snu_grad_state";    // {sheets:[ids], list:[{type,major,year}], eng:{idx:bool}}
```

with:

```js
const GRAD_STATE_KEY = "snu_grad_state";    // {picks:{semKey:sheetId}, list:[{type,major,year}], eng:{idx:bool}}
```

- [ ] **Step 3: Change the default object and add the migration guard**

In `web/app.js:2183`, replace this single line:

```js
let _gradState = _gradLoad(GRAD_STATE_KEY, { sheets: null, list: null, eng: {} });
```

with these three lines:

```js
let _gradState = _gradLoad(GRAD_STATE_KEY, { picks: {}, list: null, eng: {} });
if (!_gradState.picks || typeof _gradState.picks !== "object") _gradState.picks = {};
delete _gradState.sheets;   // drop legacy flat selection → blank start (spec §4)
```

- [ ] **Step 4: Verify the new shape + migration of an old persisted object**

`preview_eval`:

```js
// simulate a pre-feature persisted state, then reload to re-run init
localStorage.setItem("snu_grad_state", JSON.stringify({ sheets: [1, 2], list: null, eng: {} }));
window.location.reload();
```

After the reload completes, `preview_eval`:

```js
JSON.stringify({ picks: _gradState.picks, hasSheetsField: ('sheets' in _gradState) });
```

Expected AFTER change: `{"picks":{},"hasSheetsField":false}` (legacy `sheets:[1,2]` dropped, `picks` is an empty object).

---

## Task 2: Derive the audit id list from `picks` (`_gradSelectedIds`)

**Files:**
- Modify: `web/app.js:2221-2224` (`_gradSelectedIds`)

- [ ] **Step 1: Verify the old behavior (fallback to active) is present**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let out;
try {
  _gradState.picks = {};                 // nothing picked
  out = JSON.stringify(_gradSelectedIds());
} finally { _restore(); }
out;
```

Expected BEFORE change: a non-empty array (old code falls back to `[activeId()]`), e.g. `[1]` or `[null]`.

- [ ] **Step 2: Rewrite `_gradSelectedIds` to read `picks`**

In `web/app.js:2221-2224`, replace:

```js
function _gradSelectedIds() {
  const ids = (_gradState.sheets || []).filter((id) => meta.ids.includes(id));
  return ids.length ? ids : [activeId()];
}
```

with:

```js
function _gradSelectedIds() {
  // ids the audit runs against: one picked sheet per selected semester (spec §5).
  // Dangling picks (sheet deleted) are filtered out; no active-sheet fallback.
  return Object.values(_gradState.picks).filter((id) => meta.ids.includes(id));
}
```

- [ ] **Step 3: Verify it derives from picks and drops dangling ids**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let out;
try {
  meta.ids = [1, 2];
  _gradState.picks = { "2026|U000200002U000300001": 2, "2025|U000200002U000300001": 99 };
  out = JSON.stringify(_gradSelectedIds());   // 99 not in meta.ids → dropped; 2 kept
} finally { _restore(); }
out;
```

Expected AFTER change: `[2]`. Also confirm empty when blank:

```js
const _snap = structuredClone(_gradState);
let out2;
try { _gradState.picks = {}; out2 = JSON.stringify(_gradSelectedIds()); }
finally { _gradState.picks = _snap.picks || {}; }
out2;
```

Expected: `[]`.

---

## Task 3: Add-a-row picker (`_renderGradSheets` + `_gradSetPick` + `_gradDropPick`)

**Files:**
- Modify: `web/app.js:2329-2347` (replace `_renderGradSheets`; add two mutators directly above it)

- [ ] **Step 1: Verify the old flat picker renders a flat sheet pool**

`preview_eval` (builds a throwaway container, seeds two semesters, renders):

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let html;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [1, 2];
  meta.names = { 1: "A", 2: "B" };
  meta.counts = { 1: 3, 2: 4 };
  meta.sems = { 1: "2026|U000200002U000300001", 2: "2025|U000200002U000300001" };
  _gradState.picks = {};
  _renderGradSheets();
  html = document.querySelector("#gradSheetPick").innerHTML;
} finally { _restore(); }
html;
```

Expected BEFORE change: markup containing `gsheet on` chips (old flat model). After Task 3 the same snippet returns add-a-row markup (Step 4).

- [ ] **Step 2: Add the two mutators above `_renderGradSheets`**

Immediately before the `function _renderGradSheets() {` line at `web/app.js:2329`, insert:

```js
// write one pick (semKey → sheetId), persist, re-audit
function _gradSetPick(sem, id) { _gradState.picks[sem] = id; _gradSave(GRAD_STATE_KEY, _gradState); renderGrad(); }
function _gradDropPick(sem) { delete _gradState.picks[sem]; _gradSave(GRAD_STATE_KEY, _gradState); renderGrad(); }
```

- [ ] **Step 3: Replace `_renderGradSheets` with the add-a-row build**

Replace the whole function `web/app.js:2329-2347`:

```js
function _renderGradSheets() {
  const box = $("#gradSheetPick"); if (!box) return;
  const chosen = _gradSelectedIds();
  const setChosen = (arr) => { _gradState.sheets = arr.length ? arr : chosen.slice(0, 1); _gradSave(GRAD_STATE_KEY, _gradState); renderGrad(); };
  const chips = chosen.map((id) =>
    el("span", { className: "gsheet on" },
      el("span", { className: "gs-name" }, `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})`),
      chosen.length > 1
        ? el("button", { type: "button", className: "gs-del", title: "목록에서 제외",
            onclick: () => setChosen(chosen.filter((x) => x !== id)) }, "×")
        : document.createTextNode("")));
  const unchosen = meta.ids.filter((id) => !chosen.includes(id));
  if (unchosen.length)
    chips.push(el("select", { className: "gsheet-add",
      onchange: (e) => { if (e.target.value) setChosen([...chosen, Number(e.target.value)]); } },
      el("option", { value: "" }, "+ 시간표 추가"),
      ...unchosen.map((id) => el("option", { value: String(id) }, `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})`))));
  box.replaceChildren(...chips);
}
```

with:

```js
function _renderGradSheets() {
  const box = $("#gradSheetPick"); if (!box) return;
  const picks = _gradState.picks;
  // prune dangling picks (picked sheet deleted) so its semester is re-addable (spec §6, §8)
  for (const s of Object.keys(picks))
    if (!meta.ids.includes(picks[s])) delete picks[s];

  const byRank = (a, b) => semRankKey(b) - semRankKey(a);   // newest semester first
  const semsWithSheets = (excludePicked) => {
    const set = new Set();
    for (const id of meta.ids) { const s = meta.sems[id]; if (s) set.add(s); }
    let keys = [...set];
    if (excludePicked) keys = keys.filter((s) => !(s in picks));
    return keys.sort(byRank);
  };
  const label = (id) => `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})`;

  const rows = Object.keys(picks).sort(byRank).map((sem) => {
    const inSem = meta.ids.filter((id) => meta.sems[id] === sem);
    const pick = el("select", { className: "gs-pick",
      onchange: (e) => _gradSetPick(sem, Number(e.target.value)) },
      ...inSem.map((id) => el("option", { value: String(id) }, label(id))));
    pick.value = String(picks[sem]);
    return el("div", { className: "gsheet-row" },
      el("span", { className: "gs-sem" }, semLabel(sem)),
      pick,
      el("button", { type: "button", className: "gs-del", title: "목록에서 제외",
        onclick: () => _gradDropPick(sem) }, "×"));
  });

  const addable = semsWithSheets(true);
  if (addable.length)
    rows.push(el("select", { className: "gsheet-add",
      onchange: (e) => { const s = e.target.value; if (s) _gradSetPick(s, meta.ids.find((id) => meta.sems[id] === s)); } },
      el("option", { value: "" }, "+ 학기 추가"),
      ...addable.map((s) => el("option", { value: s }, semLabel(s)))));
  else if (!semsWithSheets(false).length)
    rows.push(el("div", { className: "grad-note" }, "시간표를 먼저 만드세요"));

  box.replaceChildren(...rows);
}
```

- [ ] **Step 4: Verify blank → only the add dropdown, with both semesters newest-first**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let r;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [1, 2];
  meta.names = { 1: "A", 2: "B" };
  meta.counts = { 1: 3, 2: 4 };
  meta.sems = { 1: "2026|U000200002U000300001", 2: "2025|U000200002U000300001" };
  _gradState.picks = {};
  _renderGradSheets();
  const box = document.querySelector("#gradSheetPick");
  const sel = box.querySelector("select.gsheet-add");
  r = JSON.stringify({
    rows: box.querySelectorAll(".gsheet-row").length,
    addOpts: [...sel.options].map((o) => o.text),
  });
} finally { _restore(); }
r;
```

Expected: `{"rows":0,"addOpts":["+ 학기 추가","2026 ...","2025 ..."]}` — 0 rows, add dropdown lists 2026 before 2025 (label text comes from `semLabel`, exact suffix depends on catalog/`SEMESTER_LABEL`).

- [ ] **Step 5: Verify adding a semester yields one row with a pre-selected timetable**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let r;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [1, 2];
  meta.names = { 1: "A", 2: "B" };
  meta.counts = { 1: 3, 2: 4 };
  meta.sems = { 1: "2026|U000200002U000300001", 2: "2026|U000200001U000300001" };  // both 2026, Fall + Spring
  _gradState.picks = { "2026|U000200002U000300001": 1 };
  _renderGradSheets();
  const box = document.querySelector("#gradSheetPick");
  const row = box.querySelector(".gsheet-row");
  const pickSel = row.querySelector("select.gs-pick");
  r = JSON.stringify({
    sem: row.querySelector(".gs-sem").textContent,
    pickValue: pickSel.value,
    pickOpts: [...pickSel.options].map((o) => o.value),   // only sheets in that semester
    hasDel: !!row.querySelector(".gs-del"),
    addStillThere: !!box.querySelector("select.gsheet-add"),  // Spring 2026 still addable
  });
} finally { _restore(); }
r;
```

Expected: `{"sem":"2026 ...","pickValue":"1","pickOpts":["1"],"hasDel":true,"addStillThere":true}` — row for Fall 2026, its select pre-set to sheet 1 and offering only sheet 1 (sheet 2 is Spring), a × button, and the add dropdown still present for Spring 2026.

- [ ] **Step 6: Verify dangling-pick prune makes the semester re-addable**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let r;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [2];                                   // sheet 1 was deleted
  meta.names = { 2: "B" };
  meta.counts = { 2: 4 };
  meta.sems = { 2: "2026|U000200002U000300001" };   // semester still has sheet 2
  _gradState.picks = { "2026|U000200002U000300001": 1 };  // pick points at deleted sheet 1
  _renderGradSheets();
  const box = document.querySelector("#gradSheetPick");
  const sel = box.querySelector("select.gsheet-add");
  r = JSON.stringify({
    prunedKey: ("2026|U000200002U000300001" in _gradState.picks),  // should be false
    rows: box.querySelectorAll(".gsheet-row").length,
    addOpts: sel ? [...sel.options].map((o) => o.text) : null,     // semester back in dropdown
  });
} finally { _restore(); }
r;
```

Expected: `{"prunedKey":false,"rows":0,"addOpts":["+ 학기 추가","2026 ..."]}` — the dangling pick is gone, no row renders, and the semester is offered again.

- [ ] **Step 7: Verify the no-sheets hint**

`preview_eval`:

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState) };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {}; };
let r;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [];
  meta.names = {}; meta.counts = {}; meta.sems = {};
  _gradState.picks = {};
  _renderGradSheets();
  const box = document.querySelector("#gradSheetPick");
  r = JSON.stringify({
    note: (box.querySelector(".grad-note") || {}).textContent || null,
    add: !!box.querySelector("select.gsheet-add"),
  });
} finally { _restore(); }
r;
```

Expected: `{"note":"시간표를 먼저 만드세요","add":false}`.

---

## Task 4: Row styling

**Files:**
- Modify: `web/styles.css` (insert after `web/styles.css:496`, the `.gsheet-add` block)

- [ ] **Step 1: Verify the row class is currently unstyled**

`preview_eval`:

```js
const d = document.createElement("div");
d.className = "gsheet-row"; document.body.appendChild(d);
const v = getComputedStyle(d).display; d.remove(); v;
```

Expected BEFORE change: `"block"` (no rule yet).

- [ ] **Step 2: Add the row rules**

In `web/styles.css`, immediately after line 496 (the close of the `.gsheet-add { ... }` rule) and before `.grad-pick { ... }`, insert:

```css
.gsheet-row { display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--cp-line);
  border-radius: 8px; padding: 4px 6px 4px 11px; font-size: 12px; color: var(--cp-ink);
  font-variant-numeric: tabular-nums; }
.gsheet-row .gs-sem { font-weight: 600; }
.gsheet-row .gs-pick { border: none; background: none; color: inherit; font: inherit; cursor: pointer;
  font-variant-numeric: tabular-nums; }
.gsheet-row .gs-del { background: none; border: none; color: inherit; cursor: pointer; font-size: 14px;
  line-height: 1; padding: 0 2px; opacity: .55; }
.gsheet-row .gs-del:hover { opacity: 1; }
```

- [ ] **Step 3: Verify the rule applies**

Reload (`preview_eval`: `window.location.reload()`), then `preview_eval`:

```js
const d = document.createElement("div");
d.className = "gsheet-row"; document.body.appendChild(d);
const v = getComputedStyle(d).display; d.remove(); v;
```

Expected AFTER change: `"inline-flex"`.

---

## Task 5: End-to-end + dead-code sweep

**Files:**
- Verify only: `web/app.js`, `web/styles.css`

- [ ] **Step 1: Confirm no stale references remain**

Run (Bash, repo root `web/`):

```bash
grep -n "_gradState.sheets\|setChosen\|gsheet on\|+ 시간표 추가" app.js
```

Expected: **no output**. (The `v1.sheets` at `app.js:755` is the unrelated META-v1 import and must remain — confirm grep does not flag it; it won't, since the pattern is `_gradState.sheets`.)

- [ ] **Step 2: Full add → pick → remove → re-audit round trip**

`preview_eval` (drives the real mutators, which persist and call `renderGrad`):

```js
const _snap = { meta: structuredClone(meta), gs: structuredClone(_gradState),
  ls: localStorage.getItem("snu_grad_state") };
const _restore = () => { Object.assign(meta, _snap.meta); _gradState.picks = _snap.gs.picks || {};
  if (_snap.ls == null) localStorage.removeItem("snu_grad_state"); else localStorage.setItem("snu_grad_state", _snap.ls); };
let r;
try {
  if (!document.querySelector("#gradSheetPick"))
    document.body.insertAdjacentHTML("beforeend", '<div id="gradSheetPick"></div>');
  meta.ids = [1, 2];
  meta.names = { 1: "A", 2: "B" };
  meta.counts = { 1: 3, 2: 4 };
  meta.sems = { 1: "2026|U000200002U000300001", 2: "2025|U000200002U000300001" };
  _gradState.picks = {};
  _gradSetPick("2026|U000200002U000300001", 1);   // add Fall 2026 → sheet 1
  _gradSetPick("2025|U000200002U000300001", 2);   // add Fall 2025 → sheet 2
  const afterAdd = JSON.stringify(_gradSelectedIds());        // [1,2] (order by Object.values)
  const persisted = JSON.parse(localStorage.getItem("snu_grad_state")).picks;
  _gradDropPick("2025|U000200002U000300001");      // remove Fall 2025
  const afterDrop = JSON.stringify(_gradSelectedIds());       // [1]
  r = JSON.stringify({ afterAdd, persistedKeys: Object.keys(persisted), afterDrop });
} finally { _restore(); }
r;
```

Expected: `afterAdd` contains both `1` and `2`; `persistedKeys` has both semKeys (writes hit localStorage); `afterDrop` is `[1]`.

- [ ] **Step 3: Verify the live 졸업요건 tab renders without error**

`preview_eval`: navigate to the grad view the way the app does (the tab button triggers the partial load + `renderGrad`). If a direct hook exists, call it; otherwise click the nav:

```js
(document.querySelector('[data-view="grad"]') || document.querySelector('#navGrad') || {}).click?.();
```

Then `preview_eval` after a moment:

```js
const box = document.querySelector("#gradSheetPick");
JSON.stringify({ present: !!box, childCount: box ? box.children.length : -1 });
```

Expected: `present:true`. With no real picks yet, `childCount` is `1` (just the `+ 학기 추가` dropdown) when sheets exist, or shows the `시간표를 먼저 만드세요` note when none do. Confirm `preview_console_logs` shows no exceptions from `renderGrad`.

---

## Self-review notes (already applied)

- **Spec §4 (data+migration)** → Task 1. **§5 (selection/union)** → Task 2 (`_gradTaken` unchanged, confirmed in Task 5 round trip). **§6 (picker UI, prune, mutators, empty states)** → Task 3. **§8 (edge cases)** → Task 3 Steps 5-7 + Task 5. **Row styling** → Task 4.
- **Naming consistency:** `picks`, `_gradSetPick`, `_gradDropPick`, `semsWithSheets`, classes `gsheet-row`/`gs-sem`/`gs-pick`/`gs-del`/`gsheet-add` used identically across tasks and spec.
- **No new test files** (no runner; verification is `preview_eval`). **No commits** (user directive).
