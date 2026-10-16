# Semester toggle for timetables — design

Date: 2026-06-24
Area: `web/` (static SNU Class Checker app — `app.js`, `partials/main.html`, `styles.css`)

## 1. Goal

Add a page-level **semester toggle** so timetables are organized by semester (year +
term, e.g. "2026 2학기"). The toggle scopes which timetables are visible. When a user
adds a course that does not belong to the active timetable's semester, confirm before
adding.

## 2. Current state (what exists today)

- A "semester" is identified by **`year`** (string, e.g. `"2026"`) + **`term`** (20-char
  SNU code). Term codes map to a label via `SEMESTER_LABEL` (`app.js:16-21`). Term codes
  are year-independent by design (`app.js:14-15`).
- Catalog data: `data/classes/index.json` has `terms[]` of `{year, term, label, count,
  file}` (`app.js:65-76`). Each class record carries `year` and `term`.
- Timetables ("sheets") are tracked in one metadata object
  `meta = {active, ids[], names{}, counts{}, nextId, seen{}}` (`app.js:36`), persisted at
  `META_KEY = "snu_sheets_v2"`. Each sheet's class list lives in its own
  `snu_sheet_{id}` key, loaded on demand into an LRU `liveSheets` map (`app.js:30-40`).
  `meta.active` is an **index into `meta.ids[]`**; `activeId()` returns
  `meta.ids[meta.active]`. Global `timetable` = active sheet's class array.
- Sheet ops: `addSheet()` (`app.js:723`), `renameSheet(i)` (`app.js:770`),
  `deleteSheet(i)` (`app.js:753`), `_removeSheets(idSet)` (`app.js:738`),
  `switchSheet(i)` (`app.js:~717`). All take a **positional index** into `meta.ids`.
- Tab bar: `#ttSheets` (`partials/main.html:15`), rendered by `renderSheetsNow()`
  (`app.js:818`) → `_buildTab(id, i)` (`app.js:783`) / `_buildSheetPicker()`
  (`app.js:801`). The `＋ 시간표 추가` node is appended in `renderSheetsNow`.
- Add path: **`addToTT(c)`** (`app.js:835`) is the single add-to-timetable function
  (called from search card `app.js:596` and detail drawer `app.js:1156`). It already
  copies `year`/`term` onto the stored entry (`app.js:842-846`). Dup check + optional
  overlap check precede the push.
- Search panel has its own `#year` / `#term` selectors; `updateScope()` (`app.js:532`)
  builds the header line `"{label} · {count}개 강좌"` from them.
- No modal helper — the app uses native `confirm()` / `alert()`. The mismatch prompt
  reuses `confirm()`.

Invariant today: there is **always ≥1 sheet** and always an active sheet
(`deleteSheet` blocks deleting the last one; `_removeSheets` keeps one alive). This
feature **relaxes** that invariant.

## 3. Model & terminology

- **semester key** = `"${year}|${term}"` (the `liveSheets`/`termRows` convention,
  `app.js:70`).
- **current semester** (`meta.cur`) = the page toggle value. Single source of truth for
  which timetable group is shown.
- Each sheet **belongs to** exactly one semester, stored in `meta.sems[id]`, set at
  creation time to whatever `meta.cur` is then. Belonging is permanent (moving a sheet
  between semesters is out of scope).
- The tab bar shows only sheets where `meta.sems[id] === meta.cur`. A semester group may
  be **empty** (zero sheets).

## 4. State & persistence changes

Extend `meta` (still under `META_KEY = "snu_sheets_v2"`, additive → backward compatible):

```
meta = {
  active,           // index into meta.ids, or -1 when no active sheet
  ids[], names{}, counts{}, nextId, seen{},
  cur:  "year|term",        // NEW: current page semester (toggle)
  sems: { [id]: "year|term" } // NEW: semester each sheet belongs to
}
```

**Migration (on metadata load).** When reading an old `meta` that lacks the new fields:
- `newest` = the newest catalog term as a `"year|term"` key (from `index.json` `terms[]`;
  newest by year desc then term order). If catalog not yet loaded at migration time, defer
  the sems backfill until `dataIndex()` resolves, or use the first available term.
- For every `id` in `meta.ids` without `meta.sems[id]`, set `meta.sems[id] = newest`.
- If `meta.cur` is missing, set `meta.cur = newest` (or the sem of the current active
  sheet, if one exists).

`meta.active` may now be **-1** (no active sheet). `activeId()` returns
`meta.active >= 0 ? meta.ids[meta.active] : null`. Global `timetable` is `[]` when there
is no active sheet (read-only empty), so `renderTT`, search, `refreshCardStates`, undo,
etc. never dereference null.

## 5. Toggle UI

- **Placement:** right end of the `#ttSheets` tab row (the spot circled in the mock).
  Single `<select>` (one entry per semester), not two dropdowns. Reuse existing select
  styling (`.sheet-select` or a new `.sem-toggle` class).
- **Options:** the union of (a) semesters present in `index.json` `terms[]` and (b) every
  distinct value in `meta.sems` (so a group never becomes unreachable), sorted newest
  first. Display label = the catalog `term.label` ("2026 2학기") when available, else built
  from `year` + `SEMESTER_LABEL[term]`.
- **On change** (`switchSemester(key)`):
  1. `saveTT()` the current active sheet.
  2. Set `meta.cur = key`.
  3. Sync search: set `#year`/`#term` to the toggle's year/term and re-run the search
     refresh path (the function at `app.js:~525` that calls `searchLocal` + `renderResults`
     + `updateScope`). After this, the user may change `#year`/`#term` again independently.
  4. Set `meta.active` = global index of the first sheet with `sems===cur`, or `-1` if the
     group is empty.
  5. `timetable = activeId() ? _loadSheet(activeId()) : []`.
  6. `_saveMeta(); renderSheets(); renderTT(); refreshCardStates(); updateUndoButtons();`

## 6. Behaviors

### 6.1 Tab rendering (`renderSheetsNow`, `_buildTab`, `_buildSheetPicker`)
- Build the visible list as `meta.ids.map((id,i)=>({id,i})).filter(x => meta.sems[x.id] === meta.cur)`.
  Each tab still passes its **global index `i`** to `switchSheet/renameSheet/deleteSheet`
  (their signatures are unchanged), so only the *list being iterated* changes.
- `SHEET_TAB_LIMIT` picker logic applies to the **filtered** count.
- Append the semester toggle `<select>` and the `＋ 시간표 추가` node to the row.
- `updateHero()`: when `meta.active === -1`, show `시간표 없음` / empty subtitle.

### 6.2 Create sheet (`addSheet`)
- New id gets `meta.sems[id] = meta.cur` in addition to name/count.
- Name default `시간표 N` should count **within the current semester group** (N = number of
  sheets already in `cur` + 1), not the global sheet count.

### 6.3 Empty group / delete-all (relax invariant)
- `deleteSheet(i)`: **remove** the `if (meta.ids.length <= 1)` guard (`app.js:754`).
  Deleting the last sheet (of a group or overall) is allowed.
- `_removeSheets(idSet)` (`app.js:738`): when the active sheet is removed, pick the new
  active **from the same `cur` group** first; if the group is now empty, set
  `meta.active = -1` and `timetable = []`. Never auto-jump to a different semester's sheet.
- Switching to an empty group shows just the toggle + `＋ 시간표 추가`; the timetable area
  shows the existing empty state. No auto-create on switch.

### 6.4 Add a class (`addToTT(c)`)
Order of checks at the top of `addToTT`:
1. **No active sheet** (`meta.active === -1`): auto-create a sheet under `meta.cur`
   (reuse `addSheet`), which sets it active, then continue.
2. Existing duplicate check (`app.js:836`).
3. Existing overlap check (`app.js:837`).
4. **Semester mismatch:** if `${c.year}|${c.term} !== meta.sems[activeId()]`, show
   `confirm("이 강좌는 {curLabel} 강좌가 아닙니다. 추가할까요?")` where `{curLabel}` is the active
   sheet's semester label. If the user cancels, return without adding. If confirmed, add
   anyway — the sheet's semester is **unchanged** (the class is an off-semester outlier).
5. Existing push + saves + re-render (`app.js:841-848`).

### 6.5 Search sync vs. independence
- Toggling the semester **sets** `#year`/`#term` (one-way sync at switch time).
- The user can then change `#year`/`#term` freely to browse other semesters. Adding a
  result from a different semester into the active sheet is exactly what triggers 6.4.4.

## 7. Edge cases

- **All sheets deleted (every group empty):** valid. `meta.active = -1`, empty timetable
  view, hero "시간표 없음". `＋ 시간표 추가` and adding a card still work (auto-create).
- **Manual class entry** while no active sheet: same auto-create path as 6.4.1.
- **Sheet whose `term` has no `index.json` entry** (e.g. data dropped): still listed in the
  toggle via the `meta.sems` union; label falls back to `SEMESTER_LABEL`.
- **`updateScope()`** unchanged — it reflects the search `#year`/`#term`, which may differ
  from `meta.cur` after the user re-points search. (Header scope line ≠ toggle by design.)
- **Undo/redo, `seen`, `_sheetChanges`** are keyed by sheet id — unaffected by grouping.

## 8. Code-change map (touchpoints)

| Concern | Location |
|---|---|
| `meta` shape + migration | `app.js:36`, metadata load in `initSheets()` (`app.js:40`) |
| `activeId()` returns null when `active<0` | `app.js` (activeId def) |
| Toggle markup container | `partials/main.html:15` (`#ttSheets` row) |
| Toggle build + `switchSemester` | new code near `renderSheetsNow` (`app.js:818`) |
| Tab filtering | `renderSheetsNow` (`app.js:818`), `_buildSheetPicker` (`app.js:801`) |
| New-sheet semester + group-scoped name | `addSheet` (`app.js:723`) |
| Drop last-sheet guard | `deleteSheet` (`app.js:754`) |
| Same-group active fallback / -1 | `_removeSheets` (`app.js:738`) |
| Auto-create-when-empty + mismatch confirm | `addToTT` (`app.js:835`) |
| Hero empty state | `updateHero` (`app.js:828`) |
| Search sync on toggle | search refresh path (`app.js:~525`) |
| Toggle styling | `styles.css` (`.tt-sheets` `styles.css:77`) |

## 9. Out of scope (YAGNI)

- Moving a timetable from one semester to another.
- Editing a single class's semester.
- Auto-creating a sheet when *switching* to an empty group (only on add).

## 10. Verification notes

Per the preview-verification memory: this preview tab is hidden — use `preview_snapshot`
/ `preview_eval`, not screenshots/awaited rAF. Manual checks:
- Switch toggle → tabs refilter; search year/term follow; header updates.
- Create sheets under two different semesters; confirm each group shows only its own.
- Delete every sheet in a group → empty state, no crash; `＋` add auto-creates.
- Search a different term than the active sheet's semester, add → confirm dialog; cancel
  aborts, OK adds an off-semester class without changing the sheet's semester.
- Reload → `meta.cur`, `meta.sems`, and group membership persist; old pre-feature metas
  migrate (all sheets land in newest term).
