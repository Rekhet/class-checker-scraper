# Semester Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a page-level semester toggle that scopes which timetables are shown, and warn (via `confirm()`) when a class from a different semester is added to the active timetable.

**Architecture:** Extend the existing `meta` object (persisted at `snu_sheets_v2`) with `cur` (current semester `"year|term"`) and `sems` (per-sheet semester). The tab bar renders only sheets whose `sems[id] === cur`. A `<select>` in the tab row switches semester, syncing the search `#year/#term` selects. Empty semester groups and deleting all sheets are allowed (`meta.active === -1` → `timetable = []`). The add path `addToTT` auto-creates a sheet when the group is empty and confirms on semester mismatch.

**Tech Stack:** Vanilla browser JS (classic script, globals reachable from the page), localStorage. No JS test runner exists, so each task is verified with `preview_eval` assertions against live `meta`/`timetable`/localStorage in the running preview.

**Spec:** `docs/superpowers/specs/2026-06-24-semester-toggle-design.md`

**Repo note:** the git repository root is `web/` (the project root is not a repo). All paths below are relative to `web/`. Commit with `git -C web …` from the project root, or `git …` from inside `web/`.

**Preview note (from project memory):** the Claude_Preview tab is hidden — verify with `preview_snapshot` / `preview_eval`, never screenshots or awaited `requestAnimationFrame`. The dev server config is `class-checker` (port 8040). Some render functions are rAF-coalesced (`renderTT`, `renderSheets`, `refreshCardStates`); in `preview_eval` call the `*Now()` variant (`renderTTNow()`, `renderSheetsNow()`) when you need a synchronous result, or read state directly.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `web/app.js` | All sheet/semester logic + rendering | Modify |
| `web/styles.css` | `.sem-toggle` control styling | Modify (append) |

No HTML change: the toggle is injected by JS into the existing `#ttSheets` row (`partials/main.html:15`).

---

## Task 1: Semester helpers + meta schema fields

Pure helpers (data-derived) plus the two new `meta` fields. No behavior change yet.

**Files:**
- Modify: `web/app.js:36` (default `meta` literal)
- Modify: `web/app.js:237` (add `semKey` next to `classKey`)
- Modify: `web/app.js:680-709` (`initSheets` — preserve `cur`/`sems` on load)
- Modify: `web/app.js:365-378` (`loadTerms` — call migration + helpers exist)

- [ ] **Step 1: Add the two new fields to the default `meta` literal**

`web/app.js:36` currently:
```js
let meta = { active: 0, ids: [], names: {}, counts: {}, nextId: 1, seen: {} };
```
Replace with:
```js
let meta = { active: 0, ids: [], names: {}, counts: {}, nextId: 1, seen: {}, cur: null, sems: {} };
```

- [ ] **Step 2: Add `semKey` + semester ordering/label helpers**

Immediately after `web/app.js:237` (`function classKey(c) {...}`), add:
```js
function semKey(c) { return `${c.year}|${c.term}`; }
// chronological order within a year: 1학기 < 여름 < 2학기 < 겨울
const TERM_ORDER = [
  "U000200001U000300001", // 1학기 Spring
  "U000200001U000300002", // 여름학기 Summer
  "U000200002U000300001", // 2학기 Fall
  "U000200002U000300002", // 겨울학기 Winter
];
function termRank(t) { const i = TERM_ORDER.indexOf(t); return i < 0 ? 0 : i; }
function semRankKey(key) { const [y, t] = String(key).split("|"); return Number(y) * 10 + termRank(t); }
// newest semester present in the catalog (index.json terms), as a "year|term" key
function catalogNewest() {
  const terms = _dataIndex?.terms || [];
  let best = "", bestR = -Infinity;
  for (const t of terms) {
    const k = `${t.year}|${t.term}`, r = semRankKey(k);
    if (r > bestR) { bestR = r; best = k; }
  }
  return best;
}
// every selectable semester: catalog terms ∪ semesters any sheet belongs to, newest first
function availableSemesters() {
  const set = new Set();
  for (const t of (_dataIndex?.terms || [])) set.add(`${t.year}|${t.term}`);
  for (const k of Object.values(meta.sems)) if (k) set.add(k);
  if (meta.cur) set.add(meta.cur);
  return [...set].sort((a, b) => semRankKey(b) - semRankKey(a));
}
function semLabel(key) {
  const [y, t] = String(key).split("|");
  const hit = (_dataIndex?.terms || []).find((x) => x.year === y && x.term === t);
  if (hit && hit.label) return hit.label;                 // "2026 2학기"
  const s = (SEMESTER_LABEL[t] || t).split(" ")[0];       // "2학기"
  return `${y} ${s}`;
}
// backfill sems for pre-feature sheets + pick a default cur (runs after catalog loads)
function migrateSemesters() {
  if (!_dataIndex) return;
  const newest = catalogNewest();
  for (const id of meta.ids) if (!meta.sems[id]) meta.sems[id] = newest;
  if (!meta.cur) {
    const a = activeId();
    meta.cur = (a != null && meta.sems[a]) || newest;
  }
  _saveMeta();
}
```

- [ ] **Step 3: Preserve `cur`/`sems` when loading stored meta in `initSheets`**

In `web/app.js:684-686`, the load branch currently builds `meta` without the new fields:
```js
      meta = { active: Math.min(Math.max(0, m.active | 0), m.ids.length - 1),
               ids: m.ids, names: m.names || {}, counts: m.counts || {}, seen: m.seen || {},
               nextId: m.nextId || (m.ids.reduce((mx, x) => (x > mx ? x : mx), 0) + 1) };
```
Replace with (adds `cur`/`sems`; `active` may stay 0+ here — the `-1` empty state comes later):
```js
      meta = { active: Math.min(Math.max(0, m.active | 0), m.ids.length - 1),
               ids: m.ids, names: m.names || {}, counts: m.counts || {}, seen: m.seen || {},
               nextId: m.nextId || (m.ids.reduce((mx, x) => (x > mx ? x : mx), 0) + 1),
               cur: m.cur || null, sems: m.sems || {} };
```

- [ ] **Step 4: Call `migrateSemesters()` + sync selects at startup, in `loadTerms`**

`web/app.js:376-377` currently ends `loadTerms` with:
```js
  applySearchDefaults();
  updateScope();
}
```
Replace with:
```js
  applySearchDefaults();
  migrateSemesters();                       // backfill sems + default cur now that catalog is loaded
  const [cy, ct] = String(meta.cur || "").split("|");
  if (cy && $("#year")) $("#year").value = cy;     // toggle is the source of truth for scope
  if (ct && $("#term")) $("#term").value = ct;
  updateScope();
  renderSheets();                           // re-render now that sems/cur are known
}
```

- [ ] **Step 5: Verify in the preview**

Start/refresh the `class-checker` server, then run `preview_eval`:
```js
(() => {
  const r = { cur: meta.cur, semsCount: Object.keys(meta.sems).length,
              allSheetsHaveSem: meta.ids.every(id => !!meta.sems[id]),
              newest: catalogNewest(),
              available: availableSemesters(),
              label: meta.cur ? semLabel(meta.cur) : null };
  return JSON.stringify(r);
})()
```
Expected: `cur` is a non-empty `"year|term"` (e.g. `"2026|U000200002U000300001"`), `allSheetsHaveSem` is `true`, `available` contains the catalog term(s), `label` reads like `"2026 2학기"`. No console errors (`preview_logs level:error`).

- [ ] **Step 6: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): add semester helpers + meta cur/sems fields"
```

---

## Task 2: Null-safe active sheet (allow `meta.active === -1`)

Make the codebase tolerate "no active sheet" so deleting all sheets (Task 5) and empty groups don't crash. No empty state is reachable yet; this just hardens the plumbing.

**Files:**
- Modify: `web/app.js:635` (`activeId`)
- Modify: `web/app.js:710-716` (`saveTT`)
- Modify: `web/app.js:828-833` (`updateHero`)

- [ ] **Step 1: Make `activeId()` null-safe**

`web/app.js:635`:
```js
function activeId() { return meta.ids[meta.active]; }
```
Replace with:
```js
function activeId() { return meta.active >= 0 ? meta.ids[meta.active] : null; }
```

- [ ] **Step 2: Guard `saveTT()` when there is no active sheet**

`web/app.js:710-716`:
```js
function saveTT() {
  const id = activeId();
  liveSheets.set(id, timetable);        // keep the active array in the cache
  meta.counts[id] = timetable.length;
  _writeSheet(id, timetable);           // O(active sheet) — not every sheet
  _saveMeta();
}
```
Replace with:
```js
function saveTT() {
  const id = activeId();
  if (id == null) return;               // no active sheet (empty group) — nothing to persist
  liveSheets.set(id, timetable);        // keep the active array in the cache
  meta.counts[id] = timetable.length;
  _writeSheet(id, timetable);           // O(active sheet) — not every sheet
  _saveMeta();
}
```

- [ ] **Step 3: Show an empty-state hero when there is no active sheet**

`web/app.js:828-833`:
```js
function updateHero() {
  const id = activeId();
  const n = meta.counts[id] ?? (timetable ? timetable.length : 0);
  if ($("#activeName")) $("#activeName").textContent = (meta.names[id] || "시간표");
  if ($("#activeSub")) $("#activeSub").textContent = n ? `${n}개 강좌` : "비어 있음";
}
```
Replace with:
```js
function updateHero() {
  const id = activeId();
  if (id == null) {
    if ($("#activeName")) $("#activeName").textContent = "시간표 없음";
    if ($("#activeSub")) $("#activeSub").textContent = "＋ 시간표 추가로 시작하세요";
    return;
  }
  const n = meta.counts[id] ?? (timetable ? timetable.length : 0);
  if ($("#activeName")) $("#activeName").textContent = (meta.names[id] || "시간표");
  if ($("#activeSub")) $("#activeSub").textContent = n ? `${n}개 강좌` : "비어 있음";
}
```

- [ ] **Step 4: Verify the no-active state renders without throwing**

`preview_eval` (saves, forces the empty state, re-renders, then restores):
```js
(() => {
  const savedActive = meta.active, savedTT = timetable;
  try {
    saveTT();
    meta.active = -1; timetable = [];
    renderTTNow(); updateHero();
    const hero = document.querySelector("#activeName")?.textContent;
    const credit = document.querySelector("#creditSum")?.textContent;
    const overlay = !!document.querySelector(".tt-empty-overlay");
    return JSON.stringify({ activeId: activeId(), hero, credit, overlay });
  } finally {
    meta.active = savedActive; timetable = savedTT;
    renderTTNow(); updateHero();
  }
})()
```
Expected: `{"activeId":null,"hero":"시간표 없음","credit":"총 0학점","overlay":true}` and no thrown error. Check `preview_logs level:error` is clean.

- [ ] **Step 5: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): tolerate no active sheet (meta.active === -1)"
```

---

## Task 3: Semester toggle control + `switchSemester`

Add the `<select>` to the tab row and the switch handler that syncs search and re-filters.

**Files:**
- Modify: `web/app.js:818-825` (`renderSheetsNow` — append toggle)
- Modify: `web/app.js` (add `_buildSemToggle` + `switchSemester` near `renderSheetsNow`)

- [ ] **Step 1: Add `_buildSemToggle()` and `switchSemester()`**

Add immediately before `function renderSheetsNow()` (`web/app.js:818`):
```js
function _buildSemToggle() {
  const sel = el("select", {
    className: "sem-toggle", title: "학기 전환",
    onchange: (e) => switchSemester(e.target.value),
  });
  for (const key of availableSemesters()) {
    const o = el("option", { value: key }, semLabel(key));
    if (key === meta.cur) o.selected = true;
    sel.append(o);
  }
  return sel;
}
function switchSemester(key) {
  if (!key || key === meta.cur) return;
  saveTT();
  meta.cur = key;
  const [y, t] = key.split("|");
  if ($("#year")) $("#year").value = y;          // sync search scope to the toggle…
  if ($("#term")) $("#term").value = t;          // …user may change it again afterward
  const idx = meta.ids.findIndex((id) => meta.sems[id] === key);   // first sheet of this group
  meta.active = idx;                              // -1 when the group is empty
  timetable = idx >= 0 ? _loadSheet(activeId()) : [];
  _saveMeta();
  renderSheets(); renderTT(); refreshCardStates(); updateUndoButtons();
  doSearch();                                     // re-run search for the new year/term
}
```

- [ ] **Step 2: Append the toggle in `renderSheetsNow`**

`web/app.js:818-825`:
```js
function renderSheetsNow() {
  const box = $("#ttSheets"); if (!box) return;
  box.replaceChildren();
  if (meta.ids.length > SHEET_TAB_LIMIT) box.append(_buildSheetPicker());
  else meta.ids.forEach((id, i) => box.append(_buildTab(id, i)));
  box.append(el("div", { className: "tt-sheet add", title: "시간표 추가", onclick: addSheet }, "＋ 시간표 추가"));
  updateHero();
}
```
Replace with (filtering is added in Task 4; here we only append the toggle):
```js
function renderSheetsNow() {
  const box = $("#ttSheets"); if (!box) return;
  box.replaceChildren();
  if (meta.ids.length > SHEET_TAB_LIMIT) box.append(_buildSheetPicker());
  else meta.ids.forEach((id, i) => box.append(_buildTab(id, i)));
  box.append(el("div", { className: "tt-sheet add", title: "시간표 추가", onclick: addSheet }, "＋ 시간표 추가"));
  box.append(_buildSemToggle());
  updateHero();
}
```

- [ ] **Step 3: Verify the toggle renders and switching syncs the search selects**

`preview_snapshot` should now show a combobox near the tab row whose value reads like "2026 2학기".

Then `preview_eval` (only meaningful if ≥2 semesters exist; otherwise it asserts the single-option case):
```js
(() => {
  const opts = availableSemesters();
  const before = meta.cur;
  if (opts.length < 2) return JSON.stringify({ single: true, cur: meta.cur, year: $("#year").value, term: $("#term").value });
  const other = opts.find(k => k !== meta.cur);
  switchSemester(other);
  const out = { cur: meta.cur, year: $("#year").value, term: $("#term").value, expected: other };
  switchSemester(before);                 // restore
  return JSON.stringify(out);
})()
```
Expected (multi-semester): `cur === expected`, and `year|term` equals the switched key. Single-semester data today is acceptable — record `single:true` and move on. No errors in `preview_logs`.

- [ ] **Step 4: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): add semester toggle control + switchSemester"
```

---

## Task 4: Filter tabs by current semester + scope new sheets

Tabs (and the picker) show only the current group; `addSheet` stamps `sems` and names within the group.

**Files:**
- Modify: `web/app.js:801-817` (`_buildSheetPicker` — take a group list)
- Modify: `web/app.js:818-826` (`renderSheetsNow` — build + filter group)
- Modify: `web/app.js:723-735` (`addSheet` — set `sems`, group-scoped name)

- [ ] **Step 1: Make `_buildSheetPicker` operate on a filtered group**

`web/app.js:801-817`:
```js
function _buildSheetPicker() {
  const sel = el("select", { className: "sheet-select",
    onchange: (e) => switchSheet(Number(e.target.value)) });
  meta.ids.forEach((id, i) => {
    const o = el("option", { value: String(i) },
      `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})${_sheetChanges[id] ? " ⚠" : ""}`);
    if (i === meta.active) o.selected = true;
    sel.append(o);
  });
  return el("div", { className: "sheet-picker" },
    sel,
    el("button", { type: "button", className: "sheet-mini", title: "이름 변경",
      onclick: () => renameSheet(meta.active) }, "✎"),
    el("button", { type: "button", className: "sheet-mini", title: "삭제",
      onclick: () => deleteSheet(meta.active) }, "×"),
    el("span", { className: "sheet-total" }, `${meta.ids.length}개`));
}
```
Replace with:
```js
function _buildSheetPicker(group) {
  const sel = el("select", { className: "sheet-select",
    onchange: (e) => switchSheet(Number(e.target.value)) });
  group.forEach(({ id, i }) => {
    const o = el("option", { value: String(i) },
      `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})${_sheetChanges[id] ? " ⚠" : ""}`);
    if (i === meta.active) o.selected = true;
    sel.append(o);
  });
  return el("div", { className: "sheet-picker" },
    sel,
    el("button", { type: "button", className: "sheet-mini", title: "이름 변경",
      onclick: () => renameSheet(meta.active) }, "✎"),
    el("button", { type: "button", className: "sheet-mini", title: "삭제",
      onclick: () => deleteSheet(meta.active) }, "×"),
    el("span", { className: "sheet-total" }, `${group.length}개`));
}
```

- [ ] **Step 2: Filter the visible group in `renderSheetsNow`**

`web/app.js:818-826` (post-Task-3 version):
```js
function renderSheetsNow() {
  const box = $("#ttSheets"); if (!box) return;
  box.replaceChildren();
  if (meta.ids.length > SHEET_TAB_LIMIT) box.append(_buildSheetPicker());
  else meta.ids.forEach((id, i) => box.append(_buildTab(id, i)));
  box.append(el("div", { className: "tt-sheet add", title: "시간표 추가", onclick: addSheet }, "＋ 시간표 추가"));
  box.append(_buildSemToggle());
  updateHero();
}
```
Replace with:
```js
function renderSheetsNow() {
  const box = $("#ttSheets"); if (!box) return;
  box.replaceChildren();
  const group = meta.ids
    .map((id, i) => ({ id, i }))
    .filter((x) => meta.sems[x.id] === meta.cur);    // only sheets of the current semester
  if (group.length > SHEET_TAB_LIMIT) box.append(_buildSheetPicker(group));
  else group.forEach(({ id, i }) => box.append(_buildTab(id, i)));
  box.append(el("div", { className: "tt-sheet add", title: "시간표 추가", onclick: addSheet }, "＋ 시간표 추가"));
  box.append(_buildSemToggle());
  updateHero();
}
```

- [ ] **Step 3: Stamp `sems` + group-scoped name in `addSheet`**

`web/app.js:723-735`:
```js
function addSheet() {
  if (meta.ids.length >= MAX_SHEETS) {
    alert(`시간표는 최대 ${MAX_SHEETS}개까지 만들 수 있습니다. 사용하지 않는 시간표를 삭제해 주세요.`);
    return;
  }
  saveTT();
  const id = meta.nextId++;
  meta.ids.push(id); meta.names[id] = `시간표 ${meta.ids.length}`; meta.counts[id] = 0;
  meta.active = meta.ids.length - 1;
  timetable = []; liveSheets.set(id, timetable); _evict();
  _writeSheet(id, timetable); _saveMeta();
  renderSheets(); renderTT(); refreshCardStates();
}
```
Replace with:
```js
function addSheet() {
  if (meta.ids.length >= MAX_SHEETS) {
    alert(`시간표는 최대 ${MAX_SHEETS}개까지 만들 수 있습니다. 사용하지 않는 시간표를 삭제해 주세요.`);
    return;
  }
  saveTT();
  if (meta.cur == null) meta.cur = catalogNewest();        // defensive: should be set at init
  const id = meta.nextId++;
  const n = meta.ids.filter((x) => meta.sems[x] === meta.cur).length + 1;  // Nth sheet *in this group*
  meta.ids.push(id);
  meta.sems[id] = meta.cur;
  meta.names[id] = `시간표 ${n}`; meta.counts[id] = 0;
  meta.active = meta.ids.length - 1;
  timetable = []; liveSheets.set(id, timetable); _evict();
  _writeSheet(id, timetable); _saveMeta();
  renderSheets(); renderTT(); refreshCardStates();
}
```

- [ ] **Step 4: Verify tabs are grouped by semester**

`preview_eval` (creates a sheet under a second semester, asserts the visible tab set, cleans up):
```js
(() => {
  const opts = availableSemesters();
  const startCur = meta.cur, startIds = meta.ids.slice();
  // ensure two distinct semester keys to test with
  const other = opts.find(k => k !== meta.cur) || (meta.cur + "X");  // fabricated key still groups
  const visibleNow = () => [...document.querySelectorAll("#ttSheets .tt-sheet:not(.add)")].length;
  const a = addSheet, _ = a();                       // new sheet in startCur
  const visA = visibleNow();
  switchSemester(other);                             // empty/other group
  const visOther = visibleNow();                     // should not show startCur's sheets
  const out = { startCur, other, visibleInStart: visA, visibleInOther: visOther,
                newSheetSem: meta.sems[meta.ids[meta.ids.length-1]] };
  // cleanup: delete the sheet we created, restore cur
  meta.cur = startCur;
  const created = meta.ids.filter(id => !startIds.includes(id));
  if (created.length) _removeSheets(new Set(created));
  switchSemester(startCur);
  return JSON.stringify(out);
})()
```
Expected: `visibleInOther` does **not** include the sheets from `startCur` (it is `0` if `other` had no sheets), `newSheetSem === startCur`. Note: if `other` is a fabricated key it simply yields an empty group — still a valid grouping check. No errors in `preview_logs`.

- [ ] **Step 5: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): scope tab bar + new sheets to current semester"
```

---

## Task 5: Allow empty groups and deleting every sheet

Drop the last-sheet guard and make `_removeSheets` fall back within the current group (or to no active sheet).

**Files:**
- Modify: `web/app.js:753-758` (`deleteSheet` — remove guard)
- Modify: `web/app.js:738-752` (`_removeSheets` — drop `sems`, group-aware active)

- [ ] **Step 1: Remove the "last sheet" guard in `deleteSheet`**

`web/app.js:753-758`:
```js
function deleteSheet(i) {
  if (meta.ids.length <= 1) { alert("마지막 시간표는 삭제할 수 없습니다."); return; }
  const id = meta.ids[i];
  if (id == null) return;
  if (!confirm(`'${meta.names[id]}' 시간표를 삭제할까요?`)) return;
  _removeSheets(new Set([id]));
}
```
Replace with:
```js
function deleteSheet(i) {
  const id = meta.ids[i];
  if (id == null) return;
  if (!confirm(`'${meta.names[id]}' 시간표를 삭제할까요?`)) return;
  _removeSheets(new Set([id]));
}
```

- [ ] **Step 2: Make `_removeSheets` clean `sems` and pick a same-group successor (or none)**

`web/app.js:738-752`:
```js
function _removeSheets(idSet) {
  const keptActive = activeId();            // survives unless this is a single-delete of the active sheet
  const oldActive = meta.active;
  meta.ids = meta.ids.filter((id) => !idSet.has(id));
  for (const id of idSet) {
    delete meta.names[id]; delete meta.counts[id]; liveSheets.delete(id);
    if (meta.seen) delete meta.seen[id];
    delete _sheetChanges[id];
    try { localStorage.removeItem(_sheetKey(id)); } catch { /* ignore */ }
  }
  const ai = meta.ids.indexOf(keptActive);  // <0 only when the active sheet itself was deleted
  meta.active = ai >= 0 ? ai : Math.min(oldActive, meta.ids.length - 1);
  timetable = _loadSheet(activeId());
  _saveMeta(); renderSheets(); renderTT(); refreshCardStates();
}
```
Replace with:
```js
function _removeSheets(idSet) {
  const keptActive = activeId();            // survives unless this delete includes the active sheet
  meta.ids = meta.ids.filter((id) => !idSet.has(id));
  for (const id of idSet) {
    delete meta.names[id]; delete meta.counts[id]; delete meta.sems[id];
    liveSheets.delete(id);
    if (meta.seen) delete meta.seen[id];
    delete _sheetChanges[id];
    try { localStorage.removeItem(_sheetKey(id)); } catch { /* ignore */ }
  }
  const ai = meta.ids.indexOf(keptActive);  // >=0 when the active sheet survived
  if (ai >= 0) {
    meta.active = ai;
  } else {
    // active was deleted: prefer a surviving sheet in the current semester group, else none
    const next = meta.ids.find((id) => meta.sems[id] === meta.cur);
    meta.active = next != null ? meta.ids.indexOf(next) : -1;
  }
  timetable = meta.active >= 0 ? _loadSheet(activeId()) : [];
  _saveMeta(); renderSheets(); renderTT(); refreshCardStates();
}
```

- [ ] **Step 3: Verify deleting all sheets in a group yields the empty state (no crash)**

`preview_eval` (works on a throwaway semester so it never destroys the user's real sheets):
```js
(() => {
  const startCur = meta.cur, startIds = meta.ids.slice();
  const ghost = "1900|" + TERM_ORDER[0];          // a semester with zero existing sheets
  meta.cur = ghost;
  addSheet();                                      // 시간표 1 in ghost group
  addSheet();                                      // 시간표 2 in ghost group
  const ghostIds = meta.ids.filter(id => meta.sems[id] === ghost);
  _removeSheets(new Set(ghostIds));                // delete them all
  const out = {
    active: meta.active, activeId: activeId(),
    ttIsEmpty: Array.isArray(timetable) && timetable.length === 0,
    hero: document.querySelector("#activeName")?.textContent,
    visibleTabs: [...document.querySelectorAll("#ttSheets .tt-sheet:not(.add)")].length,
  };
  // cleanup any stragglers + restore
  const created = meta.ids.filter(id => !startIds.includes(id));
  if (created.length) _removeSheets(new Set(created));
  meta.cur = startCur; switchSemester(startCur);
  return JSON.stringify(out);
})()
```
Expected: `{"active":-1,"activeId":null,"ttIsEmpty":true,"hero":"시간표 없음","visibleTabs":0}`. No errors in `preview_logs`.

- [ ] **Step 4: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): allow empty semester groups + deleting all sheets"
```

---

## Task 6: Auto-create on empty add + semester-mismatch confirm

`addToTT` creates a sheet when the group is empty, then confirms before adding an off-semester class.

**Files:**
- Modify: `web/app.js:835-849` (`addToTT`)

- [ ] **Step 1: Update `addToTT`**

`web/app.js:835-849`:
```js
function addToTT(c) {
  if (timetable.some((x) => classKey(x) === classKey(c))) return;
  if ($("#blockOverlap")?.checked && overlapsBusy(c, timetableBusy())) {
    alert("이미 추가된 강좌와 시간이 겹쳐 추가하지 않았습니다.");
    return;
  }
  pushUndo();
  timetable.push({
    year: c.year, term: c.term, name: c.name, sbjt_cd: c.sbjt_cd, lt_no: c.lt_no,
    professor: c.professor, credits: c.credits, slots: c.slots || [],
    manual: c.manual || undefined,
  });
  saveTT(); renderSheets(); renderTT(); refreshCardStates();
  if (detailClass) renderDetail();
}
```
Replace with:
```js
function addToTT(c) {
  if (meta.active < 0) addSheet();          // empty group: create "시간표 1" under the current semester
  if (timetable.some((x) => classKey(x) === classKey(c))) return;
  if ($("#blockOverlap")?.checked && overlapsBusy(c, timetableBusy())) {
    alert("이미 추가된 강좌와 시간이 겹쳐 추가하지 않았습니다.");
    return;
  }
  const sheetSem = meta.sems[activeId()];   // semester this timetable belongs to
  if (sheetSem && semKey(c) !== sheetSem &&
      !confirm(`이 강좌는 ${semLabel(sheetSem)} 강좌가 아닙니다. 추가할까요?`)) return;
  pushUndo();
  timetable.push({
    year: c.year, term: c.term, name: c.name, sbjt_cd: c.sbjt_cd, lt_no: c.lt_no,
    professor: c.professor, credits: c.credits, slots: c.slots || [],
    manual: c.manual || undefined,
  });
  saveTT(); renderSheets(); renderTT(); refreshCardStates();
  if (detailClass) renderDetail();
}
```

- [ ] **Step 2: Verify the mismatch confirm fires and is obeyed**

`preview_eval` (stubs `window.confirm` both ways; uses a throwaway semester; restores state):
```js
(() => {
  const startCur = meta.cur, startIds = meta.ids.slice(), realConfirm = window.confirm;
  const sem = meta.cur;                              // active sheet's semester
  const [y, t] = sem.split("|");
  // ensure an active sheet in `sem`
  meta.cur = sem; addSheet();
  const before = timetable.length;
  const sameSem = { year: y, term: t, name: "TEST_SAME", sbjt_cd: "TST", lt_no: "001", credits: 3, slots: [] };
  const offSem  = { year: String(Number(y) + 1), term: t, name: "TEST_OFF", sbjt_cd: "TST", lt_no: "002", credits: 3, slots: [] };
  // 1) same-semester add: no confirm needed
  addToTT(sameSem);
  const afterSame = timetable.length;
  // 2) off-semester, user cancels -> not added
  window.confirm = () => false; addToTT(offSem);
  const afterCancel = timetable.length;
  // 3) off-semester, user confirms -> added; sheet semester unchanged
  window.confirm = () => true; addToTT(offSem);
  const afterConfirm = timetable.length;
  const semUnchanged = meta.sems[activeId()] === sem;
  window.confirm = realConfirm;
  // cleanup
  const created = meta.ids.filter(id => !startIds.includes(id));
  if (created.length) _removeSheets(new Set(created));
  meta.cur = startCur; switchSemester(startCur);
  return JSON.stringify({ before, afterSame, afterCancel, afterConfirm, semUnchanged });
})()
```
Expected: `afterSame === before + 1`, `afterCancel === afterSame` (cancel blocked the add), `afterConfirm === afterSame + 1`, `semUnchanged === true`. No errors in `preview_logs`.

- [ ] **Step 3: Commit**
```bash
git -C web add app.js
git -C web commit -m "feat(sheets): confirm on off-semester add; auto-create when group empty"
```

---

## Task 7: Toggle styling + full preview walkthrough

Style the `.sem-toggle` to match the tab row, then do an end-to-end manual verification.

**Files:**
- Modify: `web/styles.css` (append near the `.tt-sheets` rules, ~`styles.css:77`)

- [ ] **Step 1: Add `.sem-toggle` styles**

Append to `web/styles.css` (adjust palette vars only if the file uses different ones — check the existing `.tt-sheet`/`.sheet-select` rules above and reuse the same custom properties):
```css
/* semester toggle: pinned to the right of the timetable tab row */
.tt-sheets .sem-toggle {
  margin-left: auto;            /* push to the right end of the flex row */
  align-self: center;
  padding: 4px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #1f2430);
  background: var(--surface, #fff);
  border: 1px solid var(--border, #d8dce4);
  border-radius: 8px;
  cursor: pointer;
}
.tt-sheets .sem-toggle:hover { border-color: var(--accent, #376dc8); }
.tt-sheets .sem-toggle:focus-visible { outline: 2px solid var(--accent, #376dc8); outline-offset: 1px; }
```

- [ ] **Step 2: Confirm `.tt-sheets` lays the row out as flex (so `margin-left:auto` works)**

Use `preview_inspect` on `#ttSheets` for `display`. If it is **not** `flex`, the toggle won't right-align. In that case also append:
```css
.tt-sheets { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
```
(Only add this if the existing rule isn't already flex — check `styles.css:77` first to avoid clobbering existing layout.)

- [ ] **Step 3: Full walkthrough verification**

With the `class-checker` preview running:
1. `preview_snapshot` — the toggle shows at the right of the tab row, value like "2026 2학기".
2. `preview_eval`: `availableSemesters().length` — if `>1`, switch via the select and confirm tabs + `#year`/`#term` change. If `=1` (current data), note that grouping is exercised by the eval tests in Tasks 4–6 instead.
3. `preview_eval` reload-persistence check:
```js
JSON.stringify({ cur: meta.cur, sems: meta.sems, active: meta.active })
```
Then `preview_eval: window.location.reload()`, wait, and re-run the same expression — `cur`/`sems` must match (persistence through `snu_sheets_v2`).
4. `preview_logs level:error` — clean.

- [ ] **Step 4: Manual UX confirmation (report to user; cannot be auto-asserted)**

State explicitly in the summary which of these were observed via snapshot/eval vs. could not be exercised (e.g. native `confirm()` dialog cannot be screenshotted — it was verified by stubbing in Task 6):
- Switch semester → tab set changes, search scope follows.
- `＋ 시간표 추가` creates a sheet in the current semester only.
- Delete every sheet in a group → empty state, `＋` still works (auto-create).
- Add an off-semester class → confirm dialog; cancel aborts, OK adds without changing the sheet's semester.

- [ ] **Step 5: Commit**
```bash
git -C web add styles.css
git -C web commit -m "style(sheets): semester toggle control"
```

---

## Self-Review (completed during authoring)

**Spec coverage:**
- Page-level toggle + source of truth → Task 3 (`switchSemester`, `_buildSemToggle`), Task 1 (`meta.cur`).
- Per-sheet `sems` + creation stamping → Task 1 (field), Task 4 (`addSheet`).
- Migration of pre-feature sheets → Task 1 (`migrateSemesters`, `catalogNewest`).
- Tab filtering by `cur` → Task 4 (`renderSheetsNow`, `_buildSheetPicker`).
- Search sync on switch, independent afterward → Task 3 (`switchSemester` sets `#year`/`#term` then `doSearch`).
- Empty group / delete-all / no-active state → Task 2 (null-safe), Task 5 (`deleteSheet`, `_removeSheets`).
- Auto-create on empty add → Task 6 (`addToTT` first line).
- Mismatch confirm → Task 6.
- Toggle contents (catalog ∪ sheet sems) + labels → Task 1 (`availableSemesters`, `semLabel`).
- Placement (right of tab row, single dropdown) → Task 3 + Task 7 styling.

**Type/name consistency:** `semKey`, `semRankKey`, `termRank`, `catalogNewest`, `availableSemesters`, `semLabel`, `migrateSemesters`, `_buildSemToggle`, `switchSemester`, `meta.cur`, `meta.sems` are used consistently across tasks. `_buildSheetPicker` signature changed to `(group)` in Task 4 and its only caller (Task 4 `renderSheetsNow`) passes `group`. `activeId()` may return `null` (Tasks 2/5/6 all null-check before use).

**Placeholder scan:** none — every step contains concrete code and a runnable `preview_eval`.

**Out of scope (unchanged):** moving a sheet between semesters; per-class semester editing; auto-create on *switch* (only on add).
```
