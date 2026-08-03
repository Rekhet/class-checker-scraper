# Cross-Semester Explore — Core Design

**Date:** 2026-07-01
**Status:** Design (approved decisions from brainstorming locked below)
**Related specs:**
- `2026-07-01-professor-identity-design.md` (Spec 2 — layers professor search + professor pages onto this)
- `2026-07-01-code-change-linking-design.md` (Spec 3 — layers prev/next code links onto the code page)

---

## Goal

Add a new **강의탐색 (Explore)** tab, placed immediately right of the 시간표 tab, that lets a user search for classes across **all** terms at once (not one semester at a time like the main timetable search), fuzzy-matched, and drill into a **per-course-code page** showing that code's full offering history — sorted newest-first, each row annotated with professor and semester.

This Core spec delivers: **class/code search + per-`sbjt_cd` class pages.** Professor search results and professor pages are explicitly **deferred to Spec 2**; prev/next code links are **deferred to Spec 3**. The Core data artifact and UI are designed so both later specs bolt on without reshaping anything.

## Non-goals (Core v1)

- No professor result type in search, no `#prof/...` pages (Spec 2).
- No prev/next code linking on the code page (Spec 3) — but the code page reserves a slot for it.
- No new scraping. Core is built entirely from data already in the DB / term JSON.
- No change to the existing per-term timetable search.

---

## Locked decisions (from brainstorming)

1. **Three separate specs, all written now.** This is Spec 1 of 3.
2. **Data index = grouped/normalized prebuilt artifact** (string tables + integer-coded offering rows grouped by code). Target ~4–6 MB raw, ~1 MB gzip. Emitted as a **new** build artifact by `scraper/export_json.py`.
3. **Professor pages deferred** to Spec 2. Core stores professor strings only for row annotation.
4. **Deep-linkable hash URLs** — e.g. `#code/M3502.019800`.
5. **New tab right after 시간표** (i.e. after `partials/main.html` in nav order).

---

## Data scale (measured)

- 28 term files, 109,343 offerings total, 58 MB raw across per-term JSON.
- Distinct `sbjt_cd` = 13,521; distinct professor name strings = 5,569; distinct class-name strings = 16,496; distinct (professor, department) pairs = 10,142.
- 28 terms = 2020–2026 × {1학기, 여름학기, 2학기, 겨울학기}.

Loading all 28 term files (58 MB) on the client to build a cross-semester view is unacceptable. Hence the prebuilt compact index below.

---

## Architecture overview

```
Build time (Python)            Runtime (browser)
─────────────────              ─────────────────
export_json.py                 user clicks 강의탐색 tab
  ├─ existing: per-term JSON      └─ lazy fetch data/explore-index.json  (once)
  ├─ existing: index.json            └─ decode string tables + code groups into memory
  └─ NEW: explore-index.json         └─ fuzzy search over code name(s)+code string
        (grouped/normalized)         └─ result click → #code/<sbjt_cd>
                                          └─ render offering history from in-memory index
```

Two moving parts:
1. **New build artifact** `web/data/explore-index.json`, emitted by `export_json.py`.
2. **New client module** in `web/app.js` (Explore page render + code page render + fuzzy search), a **new partial** `web/partials/explore.html`, and a **router extension** for parameterized `#code/<id>` routes.

---

## Part A — Build artifact: `web/data/explore-index.json`

### Shape (grouped/normalized)

```jsonc
{
  "version": 1,
  "generated": "2026-07-01T09:00:00Z",
  "strings": {
    "names": ["미적분학1", "일반물리학", "..."],   // distinct class-name strings, index = nameId
    "profs": ["홍길동", "김철수", "..."],           // distinct professor strings, index = profId
    "depts": ["수학과", "물리학과", "..."]          // distinct department strings, index = deptId
  },
  "terms": [                                        // index = termIdx; newest-first (termIdx 0 = newest)
    [2026, "U000200002U000300001"],                 // [year, term-code] — terms[0] = 2026 2학기
    [2026, "U000200001U000300002"],
    "..."
  ],
  "codes": [
    {
      "c": "M3502.019800",        // sbjt_cd (the course code — the page key)
      "names": [12, 47],          // distinct nameId(s) this code has used, newest-first
      "o": [                      // offerings for this code, one row per (term, section)
        // [termIdx, nameId, profId, deptId, credits, ltNo]
        [0, 12, 3, 5, 3, "001"],
        [4, 47, 9, 5, 3, "001"]
      ]
    }
  ]
}
```

**Row schema (fixed positional array):** `[termIdx, nameId, profId, deptId, credits, ltNo]`
- `termIdx` → index into `terms`.
- `nameId` → index into `strings.names` (per-offering, so renames within a code are visible).
- `profId` (position 2) → **int _or_ array of ints**. A single `profId` when one professor; an array `[profId, …]` when co-taught. Core emits a single int today (only one professor is captured); **Spec 2 enables co-professors and begins emitting arrays** — the client always normalizes position 2 to an array on load, so the schema never reshapes. Values index `strings.profs`; **Spec 2 swaps that table for a stable professor table** without changing row arity.
- `deptId` → index into `strings.depts`.
- `credits` → small integer.
- `ltNo` → section string (kept for uniqueness and to allow a future deep-link back into per-term timetable search).

**Grouping:** rows are grouped under their `sbjt_cd`. `codes[].names` lists the distinct nameIds the code has carried (newest-first); `codes[].names[0]` is the current/most-recent display name.

**Why per-offering nameId (not just per-code):** a course code can be renamed over time. Showing "was 미적분학, now 미분적분학1" requires per-offering names, and Spec 3's inference (group by normalized name+dept) reuses exactly this.

**Ordering:** because `terms` is newest-first (termIdx 0 = newest), `codes[].o` is sorted **ascending termIdx, then ltNo** at build time — which is newest-offering-first — so the client renders history without re-sorting.

**Co-professors (LOCKED requirement: multi):** `parse.py` (~158) currently captures only the first professor span (`spans[0]`), so today's data has one professor per offering. Multi-professor is a **locked requirement** — Spec 2 changes `parse.py` to capture every co-professor. To avoid a later reshape, position 2 is provisioned as **int-or-array from the start**: Core emits a single int (only one professor available now), Spec 2 emits arrays. The client normalizes position 2 to an array on load, so no schema change is needed when co-professors arrive.

### Size sanity check

109,343 rows × ~6 small fields. With `separators=(",",":")` (already used by `_write`), each row serializes to roughly 25–40 bytes → ~3–4.5 MB for rows, plus string tables (16,496 names + 5,569 profs + a few hundred depts ≈ 0.5–1 MB). Total ~4–6 MB raw, well under 1 MB gzip. Served static; browsers gzip on the wire.

### Build implementation sketch (`scraper/export_json.py`)

The existing `main()` already loops every term and pulls full rows via `db.search(conn, year, term, limit=None)` (`export_json.py:80-83`). Extend that loop to accumulate into intern tables + a `dict[sbjt_cd] -> list[row]`, then emit after the loop, before/after the `index.json` write (`export_json.py:102`).

```python
# accumulate during the existing `for t in terms:` loop
name_intern, prof_intern, dept_intern = {}, {}, {}
def intern(tbl, s):
    i = tbl.get(s)
    if i is None:
        i = tbl[s] = len(tbl)
    return i

term_list = []          # [[year, term], ...] in the same newest-first order as index_terms
codes = {}              # sbjt_cd -> list of offering rows
# ... inside the loop, term_idx = len(term_list); term_list.append([t["year"], t["term"]])
# for each row r in rows:
#   codes.setdefault(r["sbjt_cd"], []).append([
#       term_idx, intern(name_intern, r["name"]), intern(prof_intern, r["professor"]),
#       intern(dept_intern, r["department"]), r["credits"], r["lt_no"]])

# after the loop: invert interns to ordered lists, group names per code, sort offerings newest-first
def keys_in_order(tbl):
    out = [None] * len(tbl)
    for s, i in tbl.items():
        out[i] = s
    return out

code_objs = []
for cd, offs in codes.items():
    offs.sort(key=lambda o: (o[0], o[5]))           # ascending termIdx = newest-first (terms[] is newest-first), then section
    seen, ordered_names = set(), []
    for o in offs:                                   # distinct nameIds, newest-first
        if o[1] not in seen:
            seen.add(o[1]); ordered_names.append(o[1])
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
_write(OUT / "explore-index.json", explore)   # OUT root (web/data/), a cross-term artifact — not per-term, so not under classes/
```

(Exact intern/loop wiring is the writing-plans phase's job; this sketch fixes the shape and the emit location.)

---

## Part B — Router extension (parameterized hash routes)

**Current state:** the hash router (`showPage` / `setupNav`, `app.js` ~2751) maps a bare hash (`#timetable`, `#trend`, `#grad`, `#legal`) to a `.page` partial by `data-page`. It has no notion of a parameter.

**Change:** parse `location.hash` into `route` + `param` by splitting on the first `/`:
- `#explore` → route `explore`, no param → show the Explore partial (search view).
- `#code/M3502.019800` → route `code`, param `M3502.019800` → show the Explore partial but render the **code detail** sub-view for that `sbjt_cd`.
- (Spec 2 later: `#prof/<id>` → route `prof`.)

The code-detail view lives **inside** the Explore partial (one `.page`, two sub-views: search vs. detail), toggled by presence of a param. This keeps nav highlighting on the 강의탐색 tab while viewing a code page. `sbjt_cd` values contain `.` and digits (e.g. `M3502.019800`) — safe in a hash; `decodeURIComponent` on read, `encodeURIComponent` on write to be safe.

Back-compat: bare routes with no `/` behave exactly as today.

---

## Part C — Client: Explore page + code page (`web/app.js`, `web/partials/explore.html`)

### Nav + partial wiring

- **`index.html`**: insert `partials/explore.html` right after `partials/main.html` in the `#app[data-partials]` list:
  `data-partials="partials/main.html,partials/explore.html,partials/trend.html,partials/grad.html,partials/legal.html"`
- **`web/partials/explore.html`**: a `.page` with `data-page="explore"`, `data-title="강의탐색"`, `data-nav` (so `setupNav` renders the tab). Contains two containers: `#explore-search` (search box + results) and `#explore-detail` (code page), one shown at a time.

### Lazy data load

`explore-index.json` is fetched **once**, on first activation of the Explore tab (or first `#code/...` deep-link hit), then cached in a module-level variable. Decode into:
- `EX.names`, `EX.profs`, `EX.depts`, `EX.terms` (arrays as-is),
- `EX.codes` (array), plus `EX.byCode = Map(sbjt_cd -> codeObj)` for O(1) code-page lookup.

Show a lightweight loading state while fetching (~1 MB gzip, one request).

### Search

- Reuse the existing **`nameScore`** fuzzy scorer (substring / word-initials / subsequence) already in `app.js`.
- Build a per-code searchable string once after load: the code's distinct names joined + the code string itself, lowercased. Store alongside `EX.codes`.
- On input (debounced ~120 ms): score the query against every code's searchable string; also give a strong boost when the query is a case-insensitive prefix/exact of the `sbjt_cd`. 13,521 codes per keystroke is fine with debounce.
- Render top N (e.g. 50) results. Each result row shows: **current name** (`names[0]`), **`sbjt_cd`**, **department** (latest), **latest term label**, **latest professor(s)** (annotation). Clicking a result sets `location.hash = "#code/" + encodeURIComponent(sbjt_cd)`.

### Semester label helper

Map `terms[termIdx] = [year, termCode]` → Korean label. The four `termCode`s are known:
- `U000200001U000300001` → `1학기`
- `U000200001U000300002` → `여름학기`
- `U000200002U000300001` → `2학기`
- `U000200002U000300002` → `겨울학기`

Label = `` `${year} ${SHTM[termCode]}` `` → e.g. `2026 2학기`. If `index.json` already exposes a term `label`, prefer reusing that mapping to stay consistent with the rest of the app.

### Code detail page (`#code/<sbjt_cd>`)

Rendered from `EX.byCode.get(param)`:
- **Header:** current name (`names[0]`), `sbjt_cd`, department(s) seen.
- **Rename notice:** if `names.length > 1`, show "이전 명칭: …" listing older names (newest-first, skipping `names[0]`).
- **Offering history table** (already newest-first from build): one row per offering — **semester label**, **professor(s)** (resolved from position-2 int/array via `strings.profs`), **department**, **section** (`ltNo`), **credits**. Professor(s) plain text in Core; **Spec 2 makes each a link to `#prof/<id>`** and annotates co-taught rows.
- **Reserved slot** for Spec 3's "이전/이후 과목코드" links — render nothing in Core, but leave the container + a documented insertion point.
- **Deep-link:** the page is fully addressable by `#code/<sbjt_cd>`; loading that URL cold triggers the lazy fetch then renders.
- **Not-found:** if `param` isn't in `EX.byCode`, show a "해당 과목코드를 찾을 수 없습니다" empty state with a link back to `#explore`.

---

## Data flow (end to end)

1. `refresh.sh` → `export_json.py` regenerates per-term JSON, `index.json`, **and** `explore-index.json`.
2. User opens 강의탐색 → client lazy-fetches `explore-index.json`, decodes to `EX.*`.
3. User types → debounced fuzzy search over code names/codes → ranked results.
4. User clicks a result → `#code/<sbjt_cd>` → detail view renders offering history newest-first, each row annotated professor + semester.
5. A shared `#code/<sbjt_cd>` URL reproduces step 4 directly.

---

## Edge cases

- **Cold deep-link to `#code/...` before Explore ever opened:** lazy loader must run on route entry, not only on tab click.
- **Code with a single offering:** history table shows one row; no rename notice.
- **Missing/empty professor or department string:** intern `""`; render as "정보 없음" rather than a blank cell.
- **Duplicate offerings (same term, same section, re-scrapes):** dedupe at build by `(termIdx, ltNo)` within a code, keeping the latest, so history isn't doubled.
- **Very common query (e.g. single character):** cap results and debounce; never render thousands of rows.
- **`sbjt_cd` with regex-special chars in fuzzy match:** treat search text as literal, never as a regex.

## CARDINAL-RULE note (safety)

This feature is **read-only exploration** — it never gates a graduation audit, so the "never false-fail a student" rule isn't directly at stake. The relevant analogue: never **hide** an offering from history. When in doubt (ambiguous dedupe, unknown term code), **show** the row rather than drop it.

---

## Testing

- **Build:** after `export_json.py`, `python3 -m json.tool web/data/explore-index.json > /dev/null` (valid JSON); assert `len(codes)` ≈ 13,521 and `sum(len(c["o"]) for c in codes)` ≈ 109,343 (no offerings lost); assert every row's `nameId/profId/deptId/termIdx` are in range.
- **Search:** in-page `preview_eval` — type a known class name, assert its `sbjt_cd` appears in results; type a `sbjt_cd` prefix, assert exact code ranks first.
- **Code page:** `preview_eval` navigate `location.hash="#code/<known>"`, assert header name + row count match the DB for that code; assert rows are strictly newest-first.
- **Rename display:** pick a code known to have ≥2 distinct names, assert "이전 명칭" renders.
- **Deep-link cold load:** reload on `#code/<known>` (fresh page), assert it renders without visiting `#explore` first.
- **Nav:** assert 강의탐색 tab appears immediately after 시간표 and is highlighted on both `#explore` and `#code/...`.

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (preview tab hidden — no screenshots).

---

## Open dependencies

- Spec 2 will **redefine the `profId` space** (position 2 of each row) from raw-name-string ids to stable professor identities, and add `#prof/<id>` pages + professor search results. Core must not assume `profId` indexes `strings.profs` anywhere except the annotation lookup, so Spec 2 can swap the table.
- Spec 3 will add `prev`/`next` code arrays to each `codes[]` object and fill the reserved slot on the code page.
