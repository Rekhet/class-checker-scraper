# 졸업요건 per-semester timetable picker — design

Date: 2026-06-25
Area: `web/` (static SNU Class Checker app — `app.js`, `partials/grad.html`, `styles.css`)

## 1. Goal

Replace the graduation-requirements (졸업요건) **flat multi-sheet picker** with a
**per-semester picker**: the user selects semesters and chooses exactly **one timetable
per semester**, and the audit runs against the **union** of those picks (a whole-degree
transcript spanning multiple semesters).

## 2. Current state (what exists today)

- Grad-req keeps its own selection state separate from `meta`, persisted at
  `GRAD_STATE_KEY = "snu_grad_state"`: `_gradState = { sheets, list, eng }`
  (`app.js:2183`). `sheets` is a **flat array of sheet ids**.
- `_gradSelectedIds()` (`app.js:2221`) filters `_gradState.sheets` to ids still in
  `meta.ids`, and **defaults to `[activeId()]`** when the result is empty.
- `_gradTaken(ids)` (`app.js:2225`) unions classes from every chosen sheet, deduped by
  `classKey`. The active sheet reads the live in-memory `timetable`; others read
  `_readSheet(id)` (`app.js:697`).
- `_renderGradSheets()` (`app.js:2329`) renders chosen sheets as chips plus a
  `+ 시간표 추가` `<select>` whose options are `meta.ids.filter(id => !chosen.includes(id))`
  — **every sheet, all semesters, one flat pool**. It never reads `meta.sems` / `meta.cur`.
- `renderGrad()` (`app.js:2348`) calls `_renderGradSheets()` then
  `_gradTaken(_gradSelectedIds())` and audits the union against `_gradState.list`.
- Markup: `partials/grad.html` has `#gradSheetPick` under the "대상 시간표 목록" label.

**Why the by-semester split is invisible:** the picker enumerates `meta.ids` directly and
ignores `meta.sems`. Sheets from different semesters sit in one undifferentiated pool, and
the selection model is "any subset of all sheets," not "one per semester."

**Two warts from the new empty-group / `meta.active === -1` case:**
- `_gradSelectedIds()` default `[activeId()]` becomes `[null]` → a phantom `시간표 (0)`
  chip and an audit over an empty timetable.
- No crash (`_readSheet(null)` and `timetable` both yield `[]`), but it is sloppy.

## 3. Decisions (from brainstorming)

1. **Audit scope = union of selected semesters.** One timetable per semester; all picks
   union into a single whole-degree graduation check.
2. **Default = nothing pre-selected.** Fresh state and migration both start blank; the user
   builds the list up. The legacy flat `_gradState.sheets` is **dropped** on migration
   (existing users re-pick — low cost, and the old multi-per-semester list does not map
   cleanly to one-per-semester).
3. **Picker UI = add-a-row.** A `+ 학기 추가` dropdown lists semesters that have timetables
   and are not yet added; picking one adds a row `[학기]  [시간표 ▾]  [×]`.

## 4. Data model & persistence

`_gradState` (still `GRAD_STATE_KEY = "snu_grad_state"`):

```
_gradState = {
  picks: { [semKey]: sheetId },   // NEW — replaces `sheets`. semKey = "year|term".
  list,                           // unchanged (major/track entries)
  eng                             // unchanged (english-course overrides)
}
```

- A semester is **selected** iff it is a key of `picks`. The map structurally enforces the
  one-timetable-per-semester rule.
- `semKey` is the same `"${year}|${term}"` convention used by `meta.sems` and the helpers
  `semRankKey` / `semLabel` (`app.js:247`, `app.js:275`).

**Migration (on load).**

```js
_gradState = _gradLoad(GRAD_STATE_KEY, { picks: {}, list: null, eng: {} });
if (!_gradState.picks || typeof _gradState.picks !== "object") _gradState.picks = {};
delete _gradState.sheets;   // drop legacy flat selection → blank start
```

The default object passed to `_gradLoad` already supplies `picks: {}`. The guard handles an
old persisted object that has `sheets` but no `picks`. `list` / `eng` defaults unchanged.

## 5. Selection / union

- `_gradSelectedIds()` derives ids from `picks`, dropping dangling picks (sheet deleted):

  ```js
  function _gradSelectedIds() {
    return Object.values(_gradState.picks).filter((id) => meta.ids.includes(id));
  }
  ```

  No `[activeId()]` fallback → returns `[]` when nothing is picked (kills the phantom
  `시간표 (0)` chip when `meta.active === -1`).
- `_gradTaken(ids)` **unchanged**. Still special-cases `id === activeId()` to read the live
  `timetable` so unsaved edits to the active sheet are reflected in the audit.

## 6. Picker UI (`_renderGradSheets`, `#gradSheetPick`)

Rebuild the container as an add-a-row list.

**Prune dangling picks first** (so a semester whose picked sheet was deleted becomes
re-addable instead of stuck as an unrenderable key):

```js
for (const s of Object.keys(_gradState.picks))
  if (!meta.ids.includes(_gradState.picks[s])) delete _gradState.picks[s];
```

Helper for sheet-bearing semesters:

```js
const semsWithSheets = (excludePicked) => {
  const set = new Set();
  for (const id of meta.ids) { const s = meta.sems[id]; if (s) set.add(s); }
  let keys = [...set];
  if (excludePicked) keys = keys.filter((s) => !(s in _gradState.picks));
  return keys.sort((a, b) => semRankKey(b) - semRankKey(a));   // newest first
};
```

(Deliberately **not** `availableSemesters()` — that includes empty catalog terms with no
timetable to pick.)

**Rows** — one per `picks` entry, newest-semester first
(`Object.keys(picks).sort((a,b)=>semRankKey(b)-semRankKey(a))`), and only for keys whose
picked id is still in `meta.ids`:

```
[ 학기 라벨 ]   [ 시간표 ▾ ]   [ × ]
```

- 학기 라벨 = `semLabel(sem)` (e.g. "2026 2학기").
- 시간표 `<select>`: options = sheets in that semester
  `meta.ids.filter((id) => meta.sems[id] === sem)`, each labeled
  `` `${meta.names[id] || "시간표"} (${meta.counts[id] ?? 0})` ``; the current
  `picks[sem]` selected. `onchange` → `_gradSetPick(sem, Number(value))`.
- `×` → `_gradDropPick(sem)`.

**Add dropdown** — appended after the rows when `semsWithSheets(true).length > 0`:

```
+ 학기 추가 ▾   (options: each unpicked sheet-bearing semester, semLabel, newest first)
```

`onchange` → add the semester, defaulting its timetable to the **first sheet in that
semester**: `_gradSetPick(sem, firstSheetIdOf(sem))` where
`firstSheetIdOf(sem) = meta.ids.find((id) => meta.sems[id] === sem)`.

**Mutators** (write `picks`, persist, re-render):

```js
function _gradSetPick(sem, id) { _gradState.picks[sem] = id; _gradSave(GRAD_STATE_KEY, _gradState); renderGrad(); }
function _gradDropPick(sem)    { delete _gradState.picks[sem]; _gradSave(GRAD_STATE_KEY, _gradState); renderGrad(); }
```

**Empty / degenerate states:**
- No picks yet → render only the `+ 학기 추가` dropdown.
- No semester has any sheet (`semsWithSheets(false).length === 0`) → render a hint
  `시간표를 먼저 만드세요` and no dropdown.
- All sheet-bearing semesters already picked → hide the add dropdown.

## 7. Audit (`renderGrad`) — unchanged downstream

`renderGrad()` still calls `_renderGradSheets()` then `_gradTaken(_gradSelectedIds())` and
audits the union. With the blank default it shows an empty audit (0 credits everywhere)
until the user adds a semester; the `+ 학기 추가` dropdown is the call-to-action. No change
to the audit logic, `_gradState.list`, english overrides, or area mapping.

## 8. Edge cases

- **Picked sheet deleted:** the prune step at the top of `_renderGradSheets` removes the
  dangling key, and `_gradSelectedIds()` filters it from the union. No auto-substitution
  even if the semester still has other sheets — the semester returns to the `+ 학기 추가`
  dropdown for an explicit re-pick.
- **Semester's last sheet deleted:** that semester disappears from both the rows and the
  add dropdown (`semsWithSheets` no longer contains it).
- **Active sheet is a pick:** `_gradTaken` uses the live `timetable` (unsaved edits count).
- **`meta.active === -1`:** no special-casing; no phantom chip.
- **Renaming a sheet / changing its count:** labels read `meta.names` / `meta.counts` live
  on each render.

## 9. Code-change map (touchpoints)

| Concern | Location |
|---|---|
| `_gradState` init + migration (`picks`, drop `sheets`) | `app.js:2183` |
| `_gradSelectedIds()` → derive from `picks` | `app.js:2221` |
| `_gradTaken` | unchanged (`app.js:2225`) |
| `_renderGradSheets()` rewrite + `_gradSetPick`/`_gradDropPick`/`semsWithSheets` | `app.js:2329` |
| `renderGrad` | unchanged (`app.js:2348`) |
| Row layout styling (`.gsheet-row`) | `styles.css` |
| Markup container `#gradSheetPick` | `partials/grad.html` (unchanged, reused) |

## 10. Out of scope (YAGNI)

- More than one timetable per semester in the audit (the whole point is exactly one).
- Auto-selecting a default semester/sheet on first open (decision: start blank).
- Preserving the legacy flat `sheets` selection across migration (decision: drop it).
- Moving a sheet between semesters (owned by the semester-toggle feature, not here).

## 11. Verification notes

Per the preview-verification memory the preview tab is hidden — use `preview_eval`, not
screenshots / awaited rAF. Manual checks:
- Seed two semesters with sheets (`meta.sems`), open 졸업요건 → picker starts blank with a
  `+ 학기 추가` dropdown listing both semesters, newest first.
- Add a semester → row appears with that semester's first sheet; audit union updates.
- Add a second semester, pick a different sheet → audit unions both (deduped by `classKey`).
- Change a row's 시간표 dropdown → `picks[sem]` updates, audit re-runs.
- `×` a row → semester returns to the add dropdown; audit drops its classes.
- Delete a picked sheet elsewhere → row vanishes, no crash; semester returns to dropdown if
  it still has other sheets.
- Delete every sheet → picker shows `시간표를 먼저 만드세요`, empty audit, no crash.
- Reload → `_gradState.picks` persists; an old persisted `{sheets:[...]}` migrates to blank.
