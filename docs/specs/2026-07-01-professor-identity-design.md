# Professor Identity — Design

**Date:** 2026-07-01
**Status:** Design (approved decisions from brainstorming locked below)
**Depends on:** `2026-07-01-cross-semester-explore-core-design.md` (Spec 1). This spec layers professor search results + professor pages onto the Explore UI and index that Spec 1 builds.

---

## Goal

Give every professor a **stable identity** so the Explore feature can:
1. Return **professor** as a selectable search result type, and
2. Render a **professor page** (`#prof/<id>`) listing every class that professor has held, sorted newest-first, each row annotated with class name, code, and semester.

The hard part is identity: professor **names collide**. The same name appears across different departments (sometimes one person cross-listing, sometimes genuinely different people), and — rarely — the same name+department can be two different people over time (one retires, another is hired). We need identities that are stable across re-exports and correctable by a human.

## The core problem, stated precisely

- 5,569 distinct professor name strings, 10,142 distinct (name, department) pairs.
- A name alone is too coarse (merges different people). A (name, department) pair is a reasonable **default** unit but is both too coarse (same name+dept, different eras) and too fine (one person teaching in two departments becomes two identities).
- No professor ID currently flows through our pipeline: `parse.py` captures only the display name (`parse.py` ~158, `rec.professor = spans[0].get_text(strip=True)`); `db.py` stores `professor TEXT` with `idx_classes_prof`, **no** id column.

## Locked decisions (from brainstorming)

1. **Create a unique ID per professor (a UUID).** Initially generate one identity per **(name, department)** collection.
2. **Management page** to review same-name-across-different-department groups, so a manager can confirm whether they're the same person and **merge** them.
3. Merging needs care: same name+dept can be distinct people over time (retire → new hire); verifying every class by hand is infeasible. **"Maybe there is a professor ID on the sugang site" — investigate.**
4. Professor pages are the deliverable this spec unlocks; deep-linkable (`#prof/<id>`).

---

## Strategy: investigate first, then synthesize, then let a human correct

Three layers, in order of trust:

### Layer 1 — Investigate the authoritative sugang ID (`profPersNo`)

`sample_response.txt` from the sugang class-list endpoint contains the tokens `profPersNo` (×2), `profNm` (×9), `profEngNm`, `profEmail`, and a template row `"profNm">-</span><span id="deptKorNm">-</span>`. This strongly suggests sugang has a **personnel number** concept for professors. If `profPersNo` is populated per-offering in the payload we already fetch (or in a cheap detail/layer call), it is the **precise, authoritative** identity — far better than any synthesis.

**Task 1 (investigation, gated):**
- Re-inspect a **fresh** raw class-list response (not just the cached `sample_response.txt`) to determine whether `profPersNo` is populated per class row or is only an empty hidden-input template (`t_profPersNo` / `layer_t_profPersNo`).
- If populated per row: thread it through — `parse.py` captures `prof_pers_no` alongside `professor`; `db.py` adds a `professor_id TEXT` column + index; `crawl.py`/scrape path stores it. This becomes the identity and **Layers 2–3 become a fallback / co-existing correction layer only.**
- If **not** available in the list payload: check whether the per-class detail popup (the endpoint behind the class detail layer) returns it at acceptable cost. If still no: fall through to Layer 2.

**Decision gate:** the outcome of Task 1 (authoritative id available? at what fetch cost?) determines how much weight Layers 2–3 carry. Document the finding in the plan before building the synthesis layer, so we don't build a UUID scheme we don't need.

### Layer 2 — Synthesized identity (deterministic, stable)

If no authoritative id (or only partially populated), synthesize:

- **Unit:** one identity per **(normalized-name, department)** pair by default (matches the locked decision).
- **ID:** a **deterministic** UUID — `uuid5(NAMESPACE, f"{normalized_name}|{department}")` — **not** random `uuid4`. Determinism is essential: every re-export must reproduce the same id for the same (name, dept) so professor page URLs, merges, and bookmarks survive rebuilds. (Random UUIDs regenerated each export would break every `#prof/...` link on every refresh.)
- Normalization: trim, collapse internal whitespace; keep Hangul as-is. (Keep conservative — over-normalizing merges distinct names.)

### Layer 3 — Human correction (management page): merge & split

Synthesis is coarse; a person is the final arbiter. Two operations, persisted in a **curated JSON** that survives re-exports:

- **Merge** (the common case): manager sees name X exists under 수학과 and 통계학과, decides it's one person cross-listing, and merges the two synthesized ids into one canonical id. Persisted as `merged_id -> canonical_id`.
- **Split** (the rare, hard case): same name+dept that is actually two people across eras. Manager splits the identity by **term boundary** (offerings ≤ term T = person A, > T = person B). Persisted as a split rule keyed by (name, dept, cutoff-term). Flag as **advanced / v2-optional** — ship merge first; splits only if a real case is found.

**Curated file:** `scraper/prof_identity.json` — written by the dev backend's admin endpoints (below) or hand-editable as a fallback, read by `export_json.py`, e.g.:
```jsonc
{
  "merges": [
    { "canonical": "<uuid-or-persNo>", "members": ["<uuid2>", "<uuid3>"], "note": "홍길동 수학과=통계학과, confirmed same person 2026-07-01" }
  ],
  "splits": [
    { "name": "김철수", "dept": "물리학과", "cutoffTerm": [2022, "U000200002U000300001"],
      "before": "<uuidA>", "after": "<uuidB>", "note": "retired 2022, new hire same name" }
  ]
}
```
Re-export reads this file and applies merges/splits deterministically on top of Layer 1/2 ids, so **human decisions persist across every rebuild** with no DB migration.

---

## Data model changes

### Build artifact — extend `explore-index.json` (from Spec 1)

Spec 1 reserved **position 2** of each offering row (`profId`) and promised its id-space could be swapped. Spec 2 realizes that:

- Replace `strings.profs` (raw distinct name strings) with a **professor table** keyed by stable identity:
  ```jsonc
  "profs": [                      // index = profId; offering-row position 2 holds a profId or an array of profIds (co-taught)
    { "id": "<uuid-or-persNo>", "name": "홍길동", "depts": [5, 8] }
  ]
  ```
  where `id` is the stable identity (persNo or synth uuid, post-merge/split), `name` the display name, `depts` the department ids this identity spans.
- Each offering row's position-2 value (a `profId` or array of `profId`s) now indexes this professor table (resolving to **merged identities**, not raw names). Row arity is unchanged.
- Add a top-level `profById` need not be emitted — the client builds `Map(id -> profId)` after load.

**Co-professors (LOCKED: multi).** Capture **all** co-professors, not just the primary. `parse.py` (~158) currently takes `spans[0]` only — change it to capture every professor for an offering. Offering-row position 2 becomes an **array of profIds** (Spec 1 provisions position 2 as int-or-array from the start, so this adds no reshape). Consequences: each co-professor's page lists the co-taught class; the class page shows a "공동담당: …" annotation; a class counts on **every** co-professor's page. **Investigation still required (Task 1):** confirm how the payload delimits multiple professors — separate `<span>`s vs. a comma/`,`-joined single span — since that determines the parse change.

### Pipeline changes (if Layer 1 succeeds)

- `parse.py`: capture `prof_pers_no` (new `ClassRecord` field).
- `db.py`: `ALTER`/new column `professor_id TEXT`, index `idx_classes_prof_id`.
- `crawl.py` / insert path: persist the new field.
- `export_json.py`: prefer `professor_id` when present; else synth uuid; then apply `prof_identity.json`.

---

## UI changes (layered on Spec 1)

### Router
Add route `#prof/<id>` (Spec 1's router already splits `route/param`; this adds a `prof` case rendering a professor sub-view inside the Explore partial, nav stays on 탐색).

### Search
Extend Spec 1's fuzzy search to **also** index professor identities (display name → profId). Results become **two types**: class results (as in Spec 1) and professor results, visually distinguished (e.g. a "교수" tag). Each professor result row shows **name + department(s)** (from `profs[profId].depts`) so same-name professors in different departments are distinguishable at a glance. Selecting a professor result → `#prof/<id>`.

### Professor page (`#prof/<id>`)
- **Header:** professor name; department(s) they span (from `profs[profId].depts`); if this identity is a **merge** of several (name,dept) units, optionally note the departments.
- **Class list:** every offering this professor taught, gathered by scanning `codes[].o` for rows whose position-2 value **is or contains** this `profId`, sorted **newest-first**. Each row: semester label + class name (`names[nameId]`) + department + `sbjt_cd` (link to `#code/<sbjt_cd>`); co-taught rows note the other professor(s).
- **Deep-linkable**, cold-load safe (lazy fetch like Spec 1).

### Per-professor resolution with shared (co-taught) rows

A co-taught offering is **one shared row** with position 2 = `[profId_A, profId_B, …]` — not duplicated per professor. This does **not** impede per-professor search or pages:

- **Professor search** indexes the distinct professor **table** (`profs[]`), where every identity is its own entry regardless of how offerings pack co-professors. Searching "A" matches the `profs[]` entry for A → `#prof/<A.id>`. Co-teaching never hides A from search, because search never looks at offering rows — it looks at the professor table.
- **Professor page & result counts** resolve by **membership**, not equality: an offering belongs to professor P iff its position-2 value **is** `P` (single) **or contains** `P` (array). To avoid rescanning all ~109k rows on every page open, the client builds a **reverse index once on load** — a single O(N) pass that expands each row's prof value into `Map(profId → [offering refs])`. Both A's and B's pages then list the shared offering (correctly appearing on both), each annotating "공동담당: <the other prof(s)>".

This is precisely **why** co-professors live in one array-valued row rather than as duplicated rows: no dedup problem, and membership + the reverse index yield correct, complete per-professor views.

### Management page (professor identity review) — DEV-ONLY

Lives in the **dev shell only** (`web/index-dev.html` / `web/partials/dev.html`), never in the deployed static `index.html`. It talks to the existing dev backend `scraper/server.py` via new **admin-gated** endpoints (guarded by `ADMIN_TOKEN` / `X-Admin-Token`, exactly like the existing `POST /api/refresh`):
- `POST /api/prof-merge` — write/append a merge into `scraper/prof_identity.json`.
- `POST /api/prof-split` — write a split rule (advanced/v2).

Presents:
- **Same-name-across-departments groups:** for each name string mapping to ≥2 (name,dept) identities, list them with per-identity term ranges + sample classes + a **merge** control.
- **Split control** (advanced/v2): for a chosen identity, pick a cutoff term to split eras.

The endpoints write **only** the curated JSON in the working tree; they never rewrite the DB. `export_json.py` reads `prof_identity.json` on the next build and applies merges/splits deterministically.

### Persistence & deploy model (resolves the "backend store" question)

Locked answer — **dev-only backend, serverless deploy** (reuses the project's existing split):
- **Dev / maintenance:** `server.py` (DB + `/api`, admin panels; served via `index-dev.html`) is where a maintainer reviews and **saves** identity decisions — the backend writes `prof_identity.json` live, no manual file download.
- **Deploy:** the published site is the **static** shell (`index.html` + `web/data/*.json`; `/api` 404s — `server.py`'s existing `SERVE_STATIC` GitHub-Pages-parity mode). It consumes only the exported `explore-index.json`. **No backend ships.**
- Net: maintenance stays clean/consistent (real endpoints, no hand-edited files) while deployment stays fully serverless. Manager decisions live in `prof_identity.json`, applied on every export, so a rebuild never loses a confirmed merge.

---

## Edge cases

- **Same name, same dept, different eras:** default = treated as one identity (over-merge). Only the manual **split** rule separates them. Documented limitation; acceptable because Explore is informational, not an audit.
- **One person, two departments:** default = two identities until a manager **merges**. The professor page for the un-merged id shows only that department's classes — accurate but partial; merge fixes it.
- **Partial `profPersNo` coverage:** some rows have the authoritative id, some don't. Use persNo where present; synth-uuid the rest; a merge rule can bind a synth id to a persNo identity.
- **Name string changes (typo fixed upstream, spacing):** normalization catches spacing; a genuine respelling creates a new synth id — a merge rule reunites them.
- **Co-professors** (if pursued): a class counts on **each** co-professor's page.

## CARDINAL-RULE note (safety)

Informational feature; no audit gating. The safety analogue: **prefer showing a professor's class even under an uncertain identity** over hiding it. Over-merge/over-split errors are cosmetic and human-correctable; never drop offerings because identity is ambiguous.

---

## Testing

- **Investigation (Task 1):** capture a fresh raw response; assert whether `profPersNo` is populated per offering; record the verdict. (This is the gate — no synth code before it's answered.)
- **Determinism:** run `export_json.py` twice; assert every professor `id` and every offering's position-2 `profId` are **identical** across runs (no random UUID drift).
- **Merge applies:** add a merge to `prof_identity.json`; re-export; assert the two source ids collapse to one `profs[]` entry and both sets of offerings resolve to it.
- **Professor page:** `preview_eval` navigate `#prof/<known>`; assert class count matches the DB count for that identity and rows are newest-first; assert each `sbjt_cd` links to `#code/...`.
- **Search:** type a professor name; assert a 교수-tagged result appears and routes to `#prof/<id>`.
- **JSON validity:** `python3 -m json.tool web/data/explore-index.json` and `prof_identity.json`.

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (no screenshots).

---

## Open questions to resolve in the plan

1. **Does `profPersNo` populate per-offering?** (Task 1 gate — determines whether Layers 2–3 are primary or fallback.)
2. **Co-professors: multi (LOCKED).** Remaining detail for Task 1: confirm how the payload delimits multiple professors (separate `<span>`s vs. comma-joined single span) so `parse.py` captures them correctly.
3. **Where does the management page persist decisions** — RESOLVED: **dev-only backend** (`server.py` admin endpoints write `scraper/prof_identity.json` in the working tree; deploy stays static). See "Persistence & deploy model" above.
