# Professor Identity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every professor a stable, re-export-safe identity so Explore can return professors as a search result type and render a `#prof/<id>` page listing every class they held (co-taught classes included), newest-first, each row linking to its `#code/<sbjt_cd>`.

**Architecture:** The build (`scraper/export_json.py`, extending Spec 1's `_export_explore`) replaces the raw-professor-string table with a **professor-identity table** `profs: [{id, name, depts}]`. Identity is a **deterministic** `uuid5(PROF_NS, "<normalized-name>|<department>")` (never random), then a curated `scraper/prof_identity.json` (merges/splits) is applied on top so human corrections survive every rebuild. Co-professors are captured (multi) and each offering row's position 2 becomes an array of profIds. The client builds a `Map(profId → [offering refs])` reverse index once on load; professor search indexes the `profs[]` table, and the professor page resolves offerings by **membership** (position-2 is-or-contains the profId). A **dev-only** management panel (`partials/dev.html` → `server.py` admin endpoints) writes `prof_identity.json`; the deployed static site ships no backend.

**Tech Stack:** Python 3 stdlib (`uuid`, `re`, `json`) for the build; vanilla browser JS for the client; `scraper/server.py` stdlib HTTP server for the dev-only admin endpoints. Depends on **Spec 1's** `_export_explore` builder, `_EX` client module, `renderExplore`/`exProfs`/`exSemLabel` helpers, and the `PAGE_FOR_ROUTE` router map already in place.

## Global Constraints

- **No git commits.** All changes stay in the working tree; each task ends at a passing check — a **working-tree checkpoint**, never a commit.
- **Verification is `preview_eval` + `python3 -m json.tool` only.** Preview tab is hidden: verify by calling functions and inspecting returned strings/state — never screenshots or awaited rAF.
- **CAVEMAN prose, normal code.**
- **Deterministic identity only.** Use `uuid5` (content-addressed), never `uuid4`. Two consecutive exports MUST produce byte-identical professor ids and row position-2 values. A random-UUID scheme would break every `#prof/...` link on each refresh and is prohibited.
- **Co-professors: multi (LOCKED).** Every co-professor of an offering is captured; the offering counts on **each** professor's page. Row position 2 = array of profIds when >1, a single int when ==1.
- **Never hide a professor's class.** Prefer showing an offering under an uncertain identity over dropping it (CARDINAL-RULE analogue; Explore is informational, not an audit).
- **Management page is DEV-ONLY.** It lives in `index-dev.html` / `partials/dev.html` and talks to `server.py`. The deployed static `index.html` ships no backend; `/api` 404s there (`SERVE_STATIC`).
- **Human decisions persist via `scraper/prof_identity.json`**, applied deterministically on every export. Endpoints write only this working-tree JSON, never the DB.

---

## Conventions for this plan (read first)

**No test runner exists.** "Tests" are (a) `python3` assertion snippets over the built JSON, (b) `preview_eval` snippets against the live page, (c) for the dev backend, `curl` against a locally-run `server.py`.

**Dev server for the app:** reuse Spec 1's `preview_start` (serves `web/index.html`). Reload with `preview_eval` → `window.location.reload()` after `app.js`/partial/css edits.

**Dev backend for endpoint tests:** run `make dev` (starts `scraper/server.py` with `WEB_INDEX=index-dev.html`) in a background shell for Task 9; it binds `127.0.0.1:8000` by default. `ADMIN_TOKEN` is unset locally, so `_authed()` is permissive (matches the existing `/api/refresh`).

**Rebuild after any build change:** `make export-json` (runs `.venv/bin/python scraper/export_json.py`), then `python3 -m json.tool web/data/explore-index.json > /dev/null`.

**Working-tree checkpoints, NOT commits.** No task runs `git commit`.

**Term codes:** `1학기`=`U000200001U000300001`, `여름학기`=`U000200001U000300002`, `2학기`=`U000200002U000300001`, `겨울학기`=`U000200002U000300002`. Lexicographic order of the full 20-char code is chronological within a year.

---

## Investigation Findings (Task 1 fills this in — read before Tasks 2-3)

> **This block is a deliverable, not a placeholder.** Task 1 records its verdict here by editing this section. Tasks 2-3 branch on it.
>
> **VERDICT (recorded 2026-07-01):** Investigation done via DB probe (authoritative — `scraper/sample_response.txt` no longer exists; live fetch not needed because professor data does not come from search HTML at all).
>
> - **Professor SOURCE (key finding):** The DB `professor` field is populated by **`excel.parse_excel` from the Excel column `주담당교수`** ("주" = *main/primary* responsible professor), at [excel.py:143](scraper/excel.py:143) — NOT from search HTML. `crawl.py` uses `parse.parse_response` **only** for enrollment counts + timing recovery; the `rec.professor = spans[0]` value in `parse.py:160` is **discarded**. So the whole `profPersNo` / `spans[0]` premise is moot.
> - **`profPersNo` populated per-offering?** ☑ **no** (irrelevant — professor is Excel-sourced, `profPersNo` never enters the pipeline). Layer-2 synth (`uuid5`) is the sole identity source, as planned.
> - **Co-professor delimiter:** ☑ **neither.** The Excel `주담당교수` is a **single-professor** column. DB probe over all 28 terms / 107,505 non-empty professor rows: **0** slash `/`, **0** middot `·`, **0** CJK-comma `、`, **0** semicolon, **0** Korean `외`, **0** "and". Only **20** rows carry an ASCII comma, from exactly **3** distinct strings — all romanized `Last, First` English names (`Kim, Joon Kium`, `Kuitert, Wybe Paul`, `Lee, Sangwoo`), **0** with Hangul adjacent to the comma. The comma is **name-internal**, never a co-professor join.
> - **Consequence:** **No `parse.py` change** — and it wouldn't matter if there were, since parse.py isn't the professor source. `_split_profs` runs at export but the current data yields a single professor per row every time (so every offering row position-2 is a single int; the LOCKED multi/array schema stays supported but is simply never exercised by current data). ⚠️ **Task 2 CORRECTION (must apply): drop the ASCII comma `,` from `_split_profs`'s delimiter set** — keeping it would split `Kim, Joon Kium` into two phantom identities and fragment every English-named professor's page. Use `re.compile(r"\s*[/·、；;]\s*")` (slash / middot / CJK-comma / semicolons — real co-prof joins in Korean typography, none of which collide with real names). Adjust Task 2 Step 3's unit test to (a) use a **non-comma** delimiter for the multi case and (b) assert `_split_profs("Kim, Joon Kium") == ["Kim, Joon Kium"]` (comma preserved). Task 2 Step 2 (parse.py patch) is **SKIPPED**.

---

## File Structure

- **Modify** `scraper/export_json.py` — replace the professor-string interning in `_export_explore` with identity synthesis (`uuid5`) + `prof_identity.json` application; emit `profs: [{id,name,depts}]`; row position 2 → int-or-array of profIds. (New helpers: `_prof_key`, `_build_prof_identities`, `_load_prof_identity`.)
- **Create** `scraper/prof_identity.json` — curated `{"merges":[],"splits":[]}` (seed empty).
- **Modify (conditional)** `scraper/parse.py:156-161` — capture co-professors only if Task 1 finds separate spans.
- **Modify** `scraper/server.py:204-214` — add `POST /api/prof-merge` and `/api/prof-split` (admin-gated) writing `prof_identity.json`.
- **Modify** `web/app.js` — `PAGE_FOR_ROUTE += {prof:"explore"}`; professor reverse index; professor search results; `#prof/<id>` page; co-prof annotation + `#prof` links on the code page; dev panel wiring.
- **Modify** `web/partials/dev.html` — professor-identity review panel.
- **Modify** `web/styles.css` — professor result tag + professor page styles.

---

## Task 1: Investigation (GATE — `profPersNo` + co-prof delimiter)

**Files:**
- Read-only inspection; **write** the verdict into the "Investigation Findings" block above.
- Test: the recorded verdict itself + a saved raw response under `scraper/` (gitignored scratch, e.g. `scraper/_probe_response.html`).

**Interfaces:**
- Consumes: the sugang class-list endpoint the crawler already hits (see `scraper/crawl.py` for the URL + form params), `scraper/sample_response.txt` (cached reference).
- Produces: a filled-in Findings block that Tasks 2-3 branch on. **No synthesis code is written before this is answered** (spec decision gate).

- [ ] **Step 1: Inspect the cached sample for the professor markup shape**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import re
html = open("scraper/sample_response.txt", encoding="utf-8", errors="replace").read()
print("profPersNo occurrences:", len(re.findall(r"profPersNo", html)))
print("t_profPersNo (template):", len(re.findall(r"t_profPersNo|layer_t_profPersNo", html)))
# show the professor list-item markup: the <li class="txt"> spans parse.py reads
for m in re.finditer(r'<ul class="course-info">.*?</ul>', html, re.S):
    print(m.group(0)[:600]); print("----"); break
PY
```

Record: does `profPersNo` appear as a **populated value** or only as empty hidden-input templates (`t_profPersNo`)? How are professor names laid out in the first `li.txt` — one span with a comma-joined string, or multiple spans?

- [ ] **Step 2: Capture a FRESH raw response (not just the cache)**

Use the crawler's own request path to fetch one live class-list page and save it. Inspect `scraper/crawl.py` for the exact endpoint/params, then fetch a single page (any current term) into `scraper/_probe_response.html`. Re-run the Step 1 analysis against the fresh file:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import re, sys, os
p = "scraper/_probe_response.html"
if not os.path.exists(p):
    sys.exit("capture a fresh page into scraper/_probe_response.html first (see crawl.py for the endpoint)")
html = open(p, encoding="utf-8", errors="replace").read()
print("has populated profPersNo value:",
      bool(re.search(r'profPersNo"[^>]*value="\s*\d', html) or re.search(r'"profPersNo"\s*:\s*"\s*\d', html)))
print("prof spans sample:")
for m in re.finditer(r'<li class="txt">.*?</li>', html, re.S):
    print(m.group(0)[:400]); break
PY
```

- [ ] **Step 3: Record the verdict**

Edit the **"Investigation Findings"** block at the top of this plan: check the boxes for `profPersNo` availability and the co-professor delimiter, and note the consequence. This recorded verdict is the task's deliverable and gates Tasks 2-3.

Expected outcome (most likely, per `sample_response.txt`): `profPersNo` present only as empty templates (`t_profPersNo`) ⇒ **not authoritative per-offering** ⇒ Layer 2 synth is primary; co-professors comma-joined in `spans[0]` ⇒ **no `parse.py` change**, export splits. If the fresh capture contradicts this, branch accordingly in Tasks 2-3. **Working-tree checkpoint** (verdict recorded).

---

## Task 2: Capture co-professors (export-time split; conditional parse patch)

**Files:**
- Modify: `scraper/export_json.py` — add `_split_profs(s)` helper (used in Task 3)
- Modify (**only if** Task 1 found separate spans): `scraper/parse.py:156-161`
- Test: `python3` unit snippet on `_split_profs`

**Interfaces:**
- Consumes: the DB `professor` string (delimiter-joined when multiple, per Task 1).
- Produces: `_split_profs(s) -> list[str]` — splits a professor field into individual names, trimmed, empties dropped. Used by Task 3's identity builder.

- [ ] **Step 1: Add `_split_profs` to `export_json.py`**

In `scraper/export_json.py`, immediately after the `_now_iso` helper (added in Spec 1), add:

```python
import re   # add to the top-level import block (re is NOT imported by Spec 1)

_PROF_DELIMS = re.compile(r"\s*[,/·、；;]\s*")   # comma / slash / middot / CJK comma / semicolons

def _split_profs(s: str) -> list[str]:
    """A professor field may carry co-professors joined by a delimiter (e.g.
    '홍길동,김철수'). Split into individual names, trimmed; drop empties. A single
    name returns a one-element list. Order preserved, duplicates removed."""
    parts = [p.strip() for p in _PROF_DELIMS.split(s or "") if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p); out.append(p)
    return out
```

(Put `import re` in the top-level import block — `re` is not imported by Spec 1, which only added `from datetime import ...` there. Task 3's `_norm_name` and Spec 3's inference both reuse this same top-level `re`.)

- [ ] **Step 2: Conditional — patch `parse.py` ONLY for separate-span professors**

**If Task 1 found co-professors comma-joined in `spans[0]`: SKIP this step** — the DB already holds every name, and `_split_profs` handles it at export. Note the skip.

**If Task 1 found professors in separate spans**, in `scraper/parse.py:156-161` replace:

```python
    txts = item.select("ul.course-info li.txt")
    if txts:
        spans = txts[0].find_all("span")
        if len(spans) >= 3:
            rec.professor = spans[0].get_text(strip=True)
            rec.department = spans[1].get_text(strip=True)
```

with (capturing all leading professor spans, department is the last-but-one — **adjust indices to match the actual markup Task 1 documented**):

```python
    txts = item.select("ul.course-info li.txt")
    if txts:
        spans = [s.get_text(strip=True) for s in txts[0].find_all("span")]
        if len(spans) >= 3:
            # professor span(s) then department; join co-professors with "," so the
            # DB column stays a single TEXT value and export splits it deterministically.
            rec.professor = ",".join(p for p in spans[:-2] if p) or spans[0]
            rec.department = spans[-2]
```

Then a re-crawl is required to repopulate the DB with joined names (`make refresh` or the dev admin panel). Record that the re-crawl was run.

- [ ] **Step 3: Unit-test the splitter**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import sys; sys.path.insert(0, "scraper")
from export_json import _split_profs
assert _split_profs("홍길동") == ["홍길동"]
assert _split_profs("홍길동,김철수") == ["홍길동", "김철수"]
assert _split_profs("홍길동 / 김철수 / 홍길동") == ["홍길동", "김철수"]   # dedup, order preserved
assert _split_profs("") == []
assert _split_profs("  이영희  ") == ["이영희"]
print("OK _split_profs")
PY
```

Expected: `OK _split_profs`. **Working-tree checkpoint.**

---

## Task 3: Professor-identity build (uuid5 + apply `prof_identity.json`)

**Files:**
- Modify: `scraper/export_json.py` — add `PROF_NS`, `_prof_key`, `_norm_name`, `_load_prof_identity`, `_resolve_prof_id`; rewrite the professor handling inside `_export_explore`
- Test: `python3` determinism + shape snippet over `web/data/explore-index.json`

**Interfaces:**
- Consumes: `_split_profs` (Task 2); `db.search` rows (`professor`, `department`, term); `scraper/prof_identity.json` (Task 4 seeds it; absence is tolerated).
- Produces: in `explore-index.json`, `strings.profs` becomes an **array of objects** `{id, name, depts}` (index = profId); each offering row position 2 is a profId int (single prof) or array of profId ints (co-taught), resolving to **post-merge/split** identities.

- [ ] **Step 1: Write the failing determinism + shape check**

Run (before the edit — `strings.profs` is still a string array from Spec 1):

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import json
ex = json.load(open("web/data/explore-index.json", encoding="utf-8"))
p0 = ex["strings"]["profs"][0]
assert isinstance(p0, dict) and {"id","name","depts"} <= set(p0), f"profs not identity objects yet: {p0!r}"
print("OK identity table present")
PY
```

Expected BEFORE: `AssertionError: profs not identity objects yet: '홍길동'` (still raw strings).

- [ ] **Step 2: Add identity helpers to `export_json.py`**

After `_split_profs` (Task 2), add:

```python
import uuid   # top-level import

PROF_NS = uuid.uuid5(uuid.NAMESPACE_URL, "snu-class-checker/prof-identity")

def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _prof_key(name: str, dept: str) -> str:
    return f"{_norm_name(name)}|{(dept or '').strip()}"

def _synth_id(name: str, dept: str) -> str:
    return str(uuid.uuid5(PROF_NS, _prof_key(name, dept)))

def _load_prof_identity(path: Path) -> dict:
    """Curated merges/splits. Missing/empty file -> no-op rules."""
    if not path.exists():
        return {"merges": [], "splits": []}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return {"merges": data.get("merges", []), "splits": data.get("splits", [])}

def _term_key(term_pair) -> tuple:
    # [year, "U0002...U0003..."] — lexicographic term code is chronological within a year
    return (term_pair[0], term_pair[1])

def _build_resolver(identity: dict):
    """Return resolve(name, dept, term_pair) -> canonical id, applying splits then merges."""
    # merges: member id -> canonical id
    merge_map = {}
    for m in identity["merges"]:
        canon = m["canonical"]
        for mem in m.get("members", []):
            merge_map[mem] = canon
    # splits: (norm_name, dept) -> [(cutoff_key, before_id, after_id)]
    split_map = {}
    for s in identity["splits"]:
        key = (_norm_name(s["name"]), (s.get("dept") or "").strip())
        split_map.setdefault(key, []).append(
            (_term_key(s["cutoffTerm"]), s["before"], s["after"]))

    def resolve(name, dept, term_pair):
        base = _synth_id(name, dept)
        rules = split_map.get((_norm_name(name), (dept or "").strip()))
        if rules:
            tk = _term_key(term_pair)
            for cutoff, before, after in rules:
                base = before if tk <= cutoff else after   # <=cutoff = older era
                break
        return merge_map.get(base, base)   # remap merged members to canonical
    return resolve
```

- [ ] **Step 3: Rewrite professor handling in `_export_explore`**

Spec 1's `_export_explore` interns `r["professor"]` into `prof_intern` and stores a single `profId` at row position 2. Replace that with identity resolution + co-prof arrays. Specifically:

Remove the `prof_intern` usage for the row. Add, near the top of `_export_explore` (after the other interns):

```python
    identity = _load_prof_identity(Path("scraper/prof_identity.json"))
    resolve_prof = _build_resolver(identity)
    prof_by_id: dict[str, int] = {}     # canonical id -> profId (index into prof table)
    prof_rows: list[dict] = []          # [{id, name, depts:set}] parallel to prof_by_id
    def prof_index(pid, name, dept_id):
        i = prof_by_id.get(pid)
        if i is None:
            i = prof_by_id[pid] = len(prof_rows)
            prof_rows.append({"id": pid, "name": _norm_name(name), "depts": set()})
        prof_rows[i]["depts"].add(dept_id)
        return i
```

In the per-row loop, replace the single-professor interning with:

```python
            dept_id = intern(dept_intern, r.get("department", ""))
            names = _split_profs(r.get("professor", ""))
            if not names:
                names = [""]                                  # keep the row; "정보 없음" client-side
            term_pair = term_list[term_idx]
            pids = []
            for nm in names:
                cid = resolve_prof(nm, r.get("department", ""), term_pair)
                idx = prof_index(cid, nm, dept_id)
                if idx not in pids:
                    pids.append(idx)
            prof_field = pids[0] if len(pids) == 1 else pids   # int-or-array (Spec 1 schema)
            row = [term_idx,
                   intern(name_intern, r.get("name", "")),
                   prof_field,
                   dept_id,
                   r.get("credits") or 0,
                   r.get("lt_no", "")]
```

(Delete the old `intern(prof_intern, r.get("professor",""))` and the `prof_intern` table declaration — the professor table is now `prof_rows`.)

Finally, replace the `strings.profs` emission. Where Spec 1 wrote `"profs": keys_in_order(prof_intern)`, write:

```python
                "profs": [{"id": p["id"], "name": p["name"], "depts": sorted(p["depts"])}
                          for p in prof_rows],
```

- [ ] **Step 4: Build + validate**

Run:

```bash
cd /home/toxiclemon/project/class-checker && make export-json && python3 -m json.tool web/data/explore-index.json > /dev/null && echo "JSON OK"
```

Expected: build prints the explore line; `JSON OK`.

- [ ] **Step 5: Verify identity shape + determinism**

Run the Step 1 snippet (now expected to pass), then a determinism check:

```bash
cd /home/toxiclemon/project/class-checker && cp web/data/explore-index.json /tmp/ex_run1.json && make export-json > /dev/null && python3 - <<'PY'
import json
a = json.load(open("/tmp/ex_run1.json", encoding="utf-8"))
b = json.load(open("web/data/explore-index.json", encoding="utf-8"))
assert a["strings"]["profs"] == b["strings"]["profs"], "prof table changed across runs (non-deterministic!)"
assert [c["o"] for c in a["codes"]] == [c["o"] for c in b["codes"]], "offering rows (position-2 profIds) drifted"
# id format
import re
for p in b["strings"]["profs"][:50]:
    assert re.fullmatch(r"[0-9a-f-]{36}", p["id"]) or p["id"], p
print(f"OK deterministic; {len(b['strings']['profs'])} professor identities")
PY
```

Expected: `OK deterministic; <N> professor identities` (N should be close to the 10,142 distinct (name,dept) pairs, minus any merges — none yet). **Working-tree checkpoint.**

---

## Task 4: Seed `scraper/prof_identity.json` + merge-apply test

**Files:**
- Create: `scraper/prof_identity.json`
- Test: `python3` snippet (build with a temporary merge, assert collapse)

**Interfaces:**
- Consumes: `_load_prof_identity` / `_build_resolver` (Task 3).
- Produces: the curated file the dev endpoints (Task 9) append to; export reads it every build.

- [ ] **Step 1: Create the seed file**

Create `scraper/prof_identity.json`:

```json
{
  "merges": [],
  "splits": []
}
```

- [ ] **Step 2: Verify a merge collapses two identities**

Pick two real synth ids that share a name across departments, add a temporary merge, rebuild, assert collapse, then restore the empty seed:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import json, subprocess, sys
sys.path.insert(0, "scraper")
from export_json import _synth_id
ex = json.load(open("web/data/explore-index.json", encoding="utf-8"))
profs = ex["strings"]["profs"]
# find a name carried by >=2 identities (same name, different dept => merge candidate)
by_name = {}
for p in profs: by_name.setdefault(p["name"], []).append(p)
cand = next(v for v in by_name.values() if len(v) >= 2)
canon, member = cand[0]["id"], cand[1]["id"]
open("scraper/prof_identity.json", "w", encoding="utf-8").write(json.dumps(
    {"merges": [{"canonical": canon, "members": [member], "note": "test"}], "splits": []}, ensure_ascii=False))
subprocess.run(["make", "export-json"], check=True, capture_output=True)
ex2 = json.load(open("web/data/explore-index.json", encoding="utf-8"))
ids2 = {p["id"] for p in ex2["strings"]["profs"]}
assert member not in ids2, "merged member id should be gone"
assert canon in ids2, "canonical id should remain"
# restore empty seed + rebuild clean
open("scraper/prof_identity.json", "w", encoding="utf-8").write('{\n  "merges": [],\n  "splits": []\n}\n')
subprocess.run(["make", "export-json"], check=True, capture_output=True)
print("OK merge applied then reverted")
PY
```

Expected: `OK merge applied then reverted`. **Working-tree checkpoint** (seed file is the empty `{"merges":[],"splits":[]}`).

---

## Task 5: Client — professor table decode + reverse index

**Files:**
- Modify: `web/app.js` — extend `ensureExploreData` decode + add `_exProfIndex` builder in the Explore module
- Test: `preview_eval`

**Interfaces:**
- Consumes: `_EX.profs` (now identity objects); `exProfs(row)` (Spec 1, array-normalizing).
- Produces: `_EX.profs` kept as objects; `_EX.profById = Map(id → profId)`; `exProfIndex()` → `Map(profId → [{code, o}])` built lazily once (an offering ref = its code object + row).

- [ ] **Step 1: Verify no reverse index yet**

`preview_eval`:

```js
(async () => { await ensureExploreData(); return JSON.stringify({
  profShape: typeof _EX.profs[0],
  hasIndexFn: typeof window.exProfIndex
}); })();
```

Expected BEFORE: `{"profShape":"object","hasIndexFn":"undefined"}` (Task 3 already made `profs` objects; the index fn is missing).

- [ ] **Step 2: Add `profById` to the decode + a lazy reverse-index builder**

In `ensureExploreData` (Spec 1), inside the decode, after `byCode` is built, add a `profById` map. Change the `_EX = { ... }` assignment to include it:

```js
    const profById = new Map();
    raw.strings.profs.forEach((p, i) => profById.set(p.id, i));
```

and add `profById,` to the `_EX = { ... }` object literal (alongside `byCode`).

Then, at the end of the Explore module (after `exSemLabel`), add:

```js
let _exProfIndex = null;   // Map(profId -> [{ code, o }]) — built once, O(N) over all rows
function exProfIndex() {
  if (_exProfIndex) return _exProfIndex;
  const m = new Map();
  for (const code of _EX.codes) {
    for (const o of code.o) {
      for (const pid of exProfs(o)) {
        let arr = m.get(pid);
        if (!arr) m.set(pid, arr = []);
        arr.push({ code, o });
      }
    }
  }
  _exProfIndex = m;
  return m;
}
```

- [ ] **Step 3: Verify the reverse index resolves co-taught rows to every member**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const idx = exProfIndex();
  // find a co-taught row (position 2 is an array)
  let shared = null;
  outer: for (const c of _EX.codes) for (const o of c.o)
    if (Array.isArray(o[2])) { shared = o; break outer; }
  const res = { totalProfs: _EX.profs.length, indexSize: idx.size };
  if (shared) {
    res.coTaught = shared[2].every(pid => (idx.get(pid) || []).some(r => r.o === shared));
  } else { res.coTaught = "no co-taught rows in data (single-prof only until re-crawl)"; }
  // a single-prof spot check
  const anyPid = _EX.profs.length - 1;
  res.singleResolves = (idx.get(anyPid) || []).length >= 0;
  return JSON.stringify(res);
})();
```

Expected AFTER: `totalProfs` ≈ number of identities, `indexSize` > 0, `coTaught` === `true` (if co-taught rows exist) — the shared row appears under **every** member's profId. **Working-tree checkpoint.**

---

## Task 6: Client — professor search results (교수 tag + department)

**Files:**
- Modify: `web/app.js` — extend `exSearch`/result rendering to include professor results
- Test: `preview_eval`

**Interfaces:**
- Consumes: `_EX.profs`, `_EX.depts`, `nameScore`, `exProfIndex` (for a count), Spec 1's `exSearch`/`renderExploreResults`/`exResultRow`.
- Produces: search results of two kinds — class (code) and professor. Professor rows carry a `교수` tag + department(s) and route to `#prof/<id>`.

- [ ] **Step 1: Verify professors don't appear in results yet**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const p = _EX.profs.find(x => x.name && x.name.length >= 2);
  $("#exploreQ") && ($("#exploreQ").value = p.name);
  location.hash = "#explore"; renderExploreResults();
  const hasProfTag = !!document.querySelector('#exploreResults .ex-tag-prof');
  $("#exploreQ").value = ""; renderExploreResults(); location.hash = "#timetable";
  return JSON.stringify({ probeName: p.name, hasProfTag });
})();
```

Expected BEFORE: `{"probeName":"...","hasProfTag":false}`.

- [ ] **Step 2: Add professor scoring + a professor result row**

In the Explore module, add a professor search pass. After Spec 1's `exSearch`, add:

```js
function exSearchProfs(q) {
  if (!_EX) return [];
  const query = (q || "").trim();
  if (!query) return [];
  const scored = [];
  _EX.profs.forEach((p, i) => {
    const s = nameScore(p.name, query);
    if (s > 0) scored.push([s, i]);
  });
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, EX_MAX_RESULTS).map((x) => x[1]);   // profIds
}

function exProfResultRow(profId) {
  const p = _EX.profs[profId];
  const depts = (p.depts || []).map((d) => _EX.depts[d]).filter(Boolean).join(", ") || "정보 없음";
  const n = (exProfIndex().get(profId) || []).length;
  const row = el("a", { className: "ex-row ex-row-prof", href: "#prof/" + encodeURIComponent(p.id) },
    el("span", { className: "ex-name" }, p.name || "정보 없음"),
    el("span", { className: "ex-tag ex-tag-prof" }, "교수"),
    el("span", { className: "ex-dept" }, depts),
    el("span", { className: "ex-meta" }, `강의 ${n}개`));
  row.onclick = (e) => { e.preventDefault(); location.hash = "#prof/" + encodeURIComponent(p.id); };
  return row;
}
```

- [ ] **Step 3: Interleave professor results into `renderExploreResults`**

Replace Spec 1's `renderExploreResults` body so it renders professor hits first (tagged), then class hits:

```js
function renderExploreResults() {
  const q = $("#exploreQ")?.value || "";
  const box = $("#exploreResults"); if (!box) return;
  const profHits = exSearchProfs(q).map(exProfResultRow);
  const codeHits = exSearch(q).map(exResultRow);
  box.replaceChildren(...profHits, ...codeHits);
  const count = $("#exploreCount");
  if (count) count.textContent = q.trim()
    ? `교수 ${profHits.length} · 과목 ${codeHits.length}` : "";
}
```

- [ ] **Step 4: Verify professor results appear + route to `#prof/<id>`**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const p = _EX.profs.find(x => x.name && x.name.length >= 2);
  location.hash = "#explore";
  $("#exploreQ").value = p.name; renderExploreResults();
  const profRow = document.querySelector('#exploreResults .ex-row-prof');
  const res = JSON.stringify({
    hasProfTag: !!document.querySelector('.ex-tag-prof'),
    firstProfHref: profRow?.getAttribute('href'),
    hasDept: !!profRow?.querySelector('.ex-dept')?.textContent
  });
  $("#exploreQ").value = ""; renderExploreResults(); location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `hasProfTag:true`, `firstProfHref` starts with `#prof/`, `hasDept:true`. **Working-tree checkpoint.**

---

## Task 7: Client — professor page (`#prof/<id>`)

**Files:**
- Modify: `web/app.js` — `PAGE_FOR_ROUTE += {prof:"explore"}`; dispatch `prof` in `renderExplore`; add `renderProfDetail`
- Test: `preview_eval`

**Interfaces:**
- Consumes: `_EX.profById`, `_EX.profs`, `exProfIndex`, `exSemLabel`, `exProfs`, `_EX.names`/`depts`.
- Produces: `renderProfDetail(id)` — professor header (name, depts) + newest-first class list, each row linking to `#code/<sbjt_cd>`, co-taught rows annotated. Router shows the `explore` page for `#prof/...`, nav on 강의탐색.

- [ ] **Step 1: Verify `#prof/x` isn't handled yet**

`preview_eval`:

```js
location.hash = "#prof/anything";
const active = [...document.querySelectorAll('.page')].find(p => p.classList.contains('active'))?.dataset.page;
location.hash = "#timetable";
JSON.stringify({ activePage: active, hasProfFn: typeof window.renderProfDetail });
```

Expected BEFORE: `{"activePage":"timetable","hasProfFn":"undefined"}` (unknown route falls back).

- [ ] **Step 2: Register the `prof` route**

In `web/app.js`, extend the route map (Spec 1's `const PAGE_FOR_ROUTE = { code: "explore" };`):

```js
const PAGE_FOR_ROUTE = { code: "explore", prof: "explore" };
```

In `renderExplore` (Spec 1), add the `prof` dispatch before the search fallback:

```js
async function renderExplore(route, param) {
  await ensureExploreData();
  if (route === "code" && param) return renderCodeDetail(param);
  if (route === "prof" && param) return renderProfDetail(param);
  return renderExploreSearch();
}
```

- [ ] **Step 3: Add `renderProfDetail`**

At the end of the Explore module, add:

```js
function renderProfDetail(profIdStr) {
  $("#exploreSearch")?.classList.add("hidden");
  const box = $("#exploreDetail"); if (!box) return;
  box.classList.remove("hidden");

  const pid = _EX.profById.get(profIdStr);
  const p = pid == null ? null : _EX.profs[pid];
  if (!p) {
    box.replaceChildren(el("div", { className: "ex-empty" },
      el("p", {}, "해당 교수를 찾을 수 없습니다."),
      el("a", { className: "ex-back", href: "#explore" }, "← 강의탐색으로")));
    return;
  }

  const depts = (p.depts || []).map((d) => _EX.depts[d]).filter(Boolean).join(", ") || "정보 없음";
  const refs = (exProfIndex().get(pid) || []).slice();   // {code, o}
  // newest-first: rows already ascending-termIdx within a code; sort the merged list by termIdx
  refs.sort((a, b) => a.o[0] - b.o[0]);

  const header = el("div", { className: "ex-detail-head" },
    el("a", { className: "ex-back", href: "#explore" }, "← 강의탐색"),
    el("h2", { className: "ex-title" }, p.name || "정보 없음"),
    el("div", { className: "ex-sub" }, `교수 · ${depts} · 강의 ${refs.length}개`));

  const list = el("div", { className: "ex-hist" });
  for (const { code, o } of refs) {
    const others = exProfs(o).filter((i) => i !== pid).map((i) => _EX.profs[i]?.name).filter(Boolean);
    const nameLink = el("a", { className: "ex-h-name", href: "#code/" + encodeURIComponent(code.c) },
      _EX.names[o[1]] || "(이름 없음)");
    nameLink.onclick = (e) => { e.preventDefault(); location.hash = "#code/" + encodeURIComponent(code.c); };
    const row = el("div", { className: "ex-hrow ex-hrow-prof" },
      el("span", { className: "ex-h-sem" }, exSemLabel(o[0])),
      nameLink,
      el("span", { className: "ex-h-code" }, code.c),
      el("span", { className: "ex-h-dept" }, _EX.depts[o[3]] || "정보 없음"));
    if (others.length) row.append(el("span", { className: "ex-h-co" }, "공동담당: " + others.join(", ")));
    list.append(row);
  }

  box.replaceChildren(header, list);
  window.scrollTo(0, 0);
}
```

- [ ] **Step 4: Verify the professor page renders newest-first with code links**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const idx = exProfIndex();
  // pick a prof with >=2 classes
  let pid = null; for (const [k, v] of idx) if (v.length >= 2) { pid = k; break; }
  const id = _EX.profs[pid].id;
  location.hash = "#prof/" + encodeURIComponent(id);
  await renderExplore("prof", id);
  const d = document.querySelector('#exploreDetail');
  const sems = [...d.querySelectorAll('.ex-hrow .ex-h-sem')].map(s => s.textContent);
  const rows = d.querySelectorAll('.ex-hrow-prof');
  const codeLinks = [...d.querySelectorAll('.ex-h-name')].map(a => a.getAttribute('href'));
  // newest-first: reconstruct termIdx order from the index and assert monotonic
  const order = idx.get(pid).map(r => r.o[0]).sort((a,b)=>a-b);
  const res = JSON.stringify({
    title: d.querySelector('.ex-title')?.textContent,
    rowCount: rows.length,
    classCount: idx.get(pid).length,
    allCodeLinks: codeLinks.every(h => h.startsWith('#code/')),
    newestFirst: order.every((v,i,a)=> i===0 || a[i-1] <= v)
  });
  location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `rowCount === classCount`, `allCodeLinks:true`, `newestFirst:true`, `title` = professor name. **Working-tree checkpoint.**

---

## Task 8: Client — co-professor annotation + `#prof` links on the code page

**Files:**
- Modify: `web/app.js` — make `exProfNames` (Spec 1's code-page prof cell) render `#prof/<id>` links + 공동담당 for co-taught rows
- Test: `preview_eval`

**Interfaces:**
- Consumes: `renderCodeDetail` (Spec 1) which currently calls `exProfNames(row)` returning plain text.
- Produces: the code-page professor cell renders one `#prof/<id>` link per professor; co-taught rows show all links.

- [ ] **Step 1: Verify the code page shows plain-text professors (no links)**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const c = _EX.codes.find(x => x.o.length >= 1);
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const link = document.querySelector('#exploreDetail .ex-h-prof a');
  location.hash = "#timetable";
  return JSON.stringify({ hasProfLink: !!link });
})();
```

Expected BEFORE: `{"hasProfLink":false}` (Spec 1 rendered plain text).

- [ ] **Step 2: Replace `exProfNames` with a link-rendering version**

Spec 1 defined `exProfNames(row)` returning a string. Replace it with a version that returns a DOM fragment of `#prof/<id>` links, and update the one call site in `renderCodeDetail`.

Replace `exProfNames`:

```js
// Returns a <span> of #prof/<id> links (one per professor); "정보 없음" when none.
function exProfLinks(row) {
  const wrap = el("span", { className: "ex-prof-links" });
  const ids = exProfs(row);
  const named = ids.map((i) => ({ i, p: _EX.profs[i] })).filter((x) => x.p && x.p.name);
  if (!named.length) { wrap.append(document.createTextNode("정보 없음")); return wrap; }
  named.forEach(({ p }, k) => {
    if (k) wrap.append(document.createTextNode(", "));
    const a = el("a", { className: "ex-plink", href: "#prof/" + encodeURIComponent(p.id) }, p.name);
    a.onclick = (e) => { e.preventDefault(); location.hash = "#prof/" + encodeURIComponent(p.id); };
    wrap.append(a);
  });
  return wrap;
}
```

In `renderCodeDetail` (Spec 1), the offering-row build used:

```js
      el("span", { className: "ex-h-prof" }, exProfNames(o)),
```

Replace it with:

```js
      el("span", { className: "ex-h-prof" }, exProfLinks(o)),
```

(Remove the now-unused `exProfNames` if nothing else references it.)

- [ ] **Step 3: Verify the code page professor cell links to `#prof/<id>`**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  // prefer a code whose newest offering has a named professor
  const c = _EX.codes.find(x => x.o.some(o => exProfs(o).some(i => _EX.profs[i]?.name)));
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const link = document.querySelector('#exploreDetail .ex-h-prof a.ex-plink');
  const href = link?.getAttribute('href');
  location.hash = "#timetable";
  return JSON.stringify({ hasProfLink: !!link, hrefOk: !!href && href.startsWith('#prof/') });
})();
```

Expected AFTER: `{"hasProfLink":true,"hrefOk":true}`. **Working-tree checkpoint.**

---

## Task 9: Dev backend — `POST /api/prof-merge` + `/api/prof-split`

**Files:**
- Modify: `scraper/server.py:204-214` (`do_POST` routing) + add handlers
- Test: `curl` against a locally-run `server.py`

**Interfaces:**
- Consumes: existing `_authed()` ([server.py:302](scraper/server.py:302)), `_json()` ([server.py:125](scraper/server.py:125)); body-read pattern from `_lookup` ([server.py:278](scraper/server.py:278)).
- Produces: two admin-gated POST endpoints that append a merge/split to `scraper/prof_identity.json` (creating it if absent) and return the updated file. They **only** write the working-tree JSON — never the DB.

- [ ] **Step 1: Verify the endpoints 404 before adding them**

Start the dev backend (`make dev` in a background shell). Then:

```bash
curl -s -X POST http://127.0.0.1:8000/api/prof-merge -H 'Content-Type: application/json' \
  -d '{"canonical":"a","members":["b"]}' -w '\n%{http_code}\n'
```

Expected BEFORE: `{"error": "not found"}` then `404`.

- [ ] **Step 2: Add the routing + handlers**

In `do_POST` ([server.py:208-213](scraper/server.py:208)), add two routes before the final `return self._json(... 404)`:

```python
        if u.path == "/api/prof-merge":
            return self._prof_identity_write("merges")
        if u.path == "/api/prof-split":
            return self._prof_identity_write("splits")
```

Add the handler alongside the others (e.g. after `_lookup`, [server.py:289](scraper/server.py:289)):

```python
    _PROF_IDENTITY_PATH = Path(__file__).with_name("prof_identity.json")

    def _prof_identity_write(self, bucket):
        """Append a merge (or split) rule to scraper/prof_identity.json. Admin-gated;
        writes only the curated working-tree JSON — never the DB. Returns the file."""
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            rule = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if not isinstance(rule, dict):
            return self._json({"error": "rule must be an object"}, 400)
        try:
            data = json.loads(self._PROF_IDENTITY_PATH.read_text(encoding="utf-8")) \
                if self._PROF_IDENTITY_PATH.exists() else {}
        except json.JSONDecodeError:
            data = {}
        data.setdefault("merges", []); data.setdefault("splits", [])
        data[bucket].append(rule)
        self._PROF_IDENTITY_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._json({"ok": True, "count": {k: len(data[k]) for k in ("merges", "splits")}})
```

(Ensure `from pathlib import Path` is imported at the top of `server.py`; add it if missing.)

- [ ] **Step 3: Verify a merge is appended**

Restart the dev backend (so the new code loads), then:

```bash
cd /home/toxiclemon/project/class-checker && cp scraper/prof_identity.json /tmp/pi_backup.json
curl -s -X POST http://127.0.0.1:8000/api/prof-merge -H 'Content-Type: application/json' \
  -d '{"canonical":"CANON","members":["M1"],"note":"curl test"}' -w '\n%{http_code}\n'
python3 -c "import json; d=json.load(open('scraper/prof_identity.json')); assert d['merges'] and d['merges'][-1]['canonical']=='CANON', d; print('OK appended')"
cp /tmp/pi_backup.json scraper/prof_identity.json   # restore empty seed
```

Expected: `{"ok": true, ...}` then `200`, then `OK appended`, and the seed is restored. **Working-tree checkpoint.**

---

## Task 10: Dev management panel (professor-identity review)

**Files:**
- Modify: `web/partials/dev.html` — add a review panel section
- Modify: `web/app.js` — build the same-name-across-departments groups + wire the merge control (guarded — nodes absent in production)
- Test: `preview_eval` against `index-dev.html`

**Interfaces:**
- Consumes: `_EX.profs` (identity objects), `exProfIndex` (term ranges/sample classes), `api("/api/prof-merge", …)` (Task 9), the `refreshDB` fetch pattern ([app.js:1494](web/app.js:1494)).
- Produces: a `#profReview` panel listing each name mapping to ≥2 identities, with per-identity dept + a merge button that POSTs a merge then reports success. Absent in production (guarded like `#refreshBtn`).

- [ ] **Step 1: Add the panel markup to `dev.html`**

In `web/partials/dev.html`, before the closing `</div></div>` of the `dev-page`, add:

```html
<section class="panel prof-review">
  <div class="admin-head">
    <h2>교수 동일인 확인 <span class="sub">Professor identity</span></h2>
    <button id="profReviewLoad">동명이인 그룹 불러오기 Load groups</button>
  </div>
  <p class="admin-note">같은 이름이 여러 학과에 존재하는 그룹입니다. 동일인이면 병합(merge)하세요.
     결정은 scraper/prof_identity.json에 저장되어 다음 export에 반영됩니다.</p>
  <div id="profReview" class="prof-review-body"></div>
</section>
```

- [ ] **Step 2: Wire the panel in `app.js`**

At the end of the Explore module, add the panel builder (guarded so it no-ops when the dev nodes are absent):

```js
async function loadProfReview() {
  const box = $("#profReview"); if (!box) return;
  await ensureExploreData();
  const byName = new Map();
  _EX.profs.forEach((p, i) => {
    if (!p.name) return;
    let a = byName.get(p.name); if (!a) byName.set(p.name, a = []);
    a.push(i);
  });
  const groups = [...byName.entries()].filter(([, ids]) => ids.length >= 2);
  box.replaceChildren(el("div", { className: "pr-count" }, `${groups.length}개 그룹`));
  for (const [name, ids] of groups.slice(0, 200)) {   // cap render
    const g = el("div", { className: "pr-group" }, el("div", { className: "pr-name" }, name));
    ids.forEach((i) => {
      const p = _EX.profs[i];
      const depts = (p.depts || []).map((d) => _EX.depts[d]).filter(Boolean).join(", ");
      const n = (exProfIndex().get(i) || []).length;
      g.append(el("div", { className: "pr-id" }, `${depts || "?"} · 강의 ${n}개 · ${p.id.slice(0, 8)}`));
    });
    const btn = el("button", { className: "pr-merge" }, "이 그룹 병합 Merge");
    btn.onclick = async () => {
      btn.disabled = true;
      const canonical = _EX.profs[ids[0]].id;
      const members = ids.slice(1).map((i) => _EX.profs[i].id);
      try {
        await api("/api/prof-merge", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ canonical, members, note: `${name} merged ${new Date().toISOString().slice(0,10)}` }) });
        btn.textContent = "저장됨 (다음 export 반영) Saved";
      } catch { btn.textContent = "실패 Failed"; btn.disabled = false; }
    };
    g.append(btn);
    box.append(g);
  }
}
```

Then wire the load button in `init()` alongside the other guarded dev wirings (near `#refreshBtn`, [app.js:2825](web/app.js:2825)):

```js
  const prBtn = $("#profReviewLoad");   // dev panel — absent in production
  if (prBtn) prBtn.addEventListener("click", loadProfReview);
```

- [ ] **Step 3: Verify the panel builds groups (dev shell)**

Point the preview at the dev shell for this check — `preview_eval` → `window.location.href = "index-dev.html"`, wait for load, then `preview_eval`:

```js
(async () => {
  if (!document.querySelector('#profReview')) return JSON.stringify({ devPanel: false });
  await loadProfReview();
  const groups = document.querySelectorAll('#profReview .pr-group');
  const hasMergeBtn = !!document.querySelector('#profReview .pr-merge');
  return JSON.stringify({ devPanel: true, groupCount: groups.length, hasMergeBtn });
})();
```

Expected AFTER: `{"devPanel":true,"groupCount":<>0>,"hasMergeBtn":true}`. Then restore: `preview_eval` → `window.location.href = "index.html"`. **Working-tree checkpoint.**

> Note: after a real merge via this panel, the maintainer runs `make export-json` to regenerate `explore-index.json` with the merge applied. The panel writes only `prof_identity.json`; it does not rebuild live.

---

## Task 11: Styles (professor tag + professor page + review panel)

**Files:**
- Modify: `web/styles.css` (append)
- Test: `preview_eval`

- [ ] **Step 1: Append styles**

At the end of `web/styles.css`, append:

```css
/* ---- Explore: professor results + page ---- */
.ex-tag { font-size: 10.5px; padding: 1px 6px; border-radius: 999px; border: 1px solid var(--cp-line);
  color: var(--cp-muted); align-self: center; }
.ex-tag-prof { color: var(--cp-accent); border-color: color-mix(in srgb, var(--cp-accent) 40%, transparent); }
.ex-row-prof { grid-template-areas: "name tag" "dept meta"; }
.ex-row-prof .ex-tag { grid-area: tag; justify-self: end; }
.ex-plink, .ex-h-name { color: var(--cp-accent); text-decoration: none; }
.ex-plink:hover, .ex-h-name:hover { text-decoration: underline; }
.ex-hrow-prof { grid-template-columns: 110px 1fr auto auto; }
.ex-h-code { color: var(--cp-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.ex-h-co { grid-column: 2 / -1; font-size: 11.5px; color: var(--cp-muted); }

/* ---- Dev: professor-identity review panel ---- */
.prof-review-body { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.pr-count { font-size: 12px; color: var(--cp-muted); }
.pr-group { border: 1px solid var(--cp-line); border-radius: 8px; padding: 8px 10px; }
.pr-name { font-weight: 700; color: var(--cp-ink); }
.pr-id { font-size: 12px; color: var(--cp-muted); font-variant-numeric: tabular-nums; }
.pr-merge { margin-top: 6px; font-size: 12px; }
```

- [ ] **Step 2: Verify the professor tag styles apply**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const p = _EX.profs.find(x => x.name && x.name.length >= 2);
  location.hash = "#explore"; $("#exploreQ").value = p.name; renderExploreResults();
  const tag = document.querySelector('.ex-tag-prof');
  const disp = tag ? getComputedStyle(tag).borderRadius : null;
  $("#exploreQ").value = ""; renderExploreResults(); location.hash = "#timetable";
  return JSON.stringify({ tagBorderRadius: disp });   // 999px pill
})();
```

Expected AFTER: a non-empty `tagBorderRadius` (e.g. `"999px"`). **Working-tree checkpoint.**

---

## Final verification (whole feature)

- [ ] **Investigation recorded:** the Findings block is filled; no synth code predates it.
- [ ] **Determinism:** two `make export-json` runs produce identical `strings.profs` and identical offering rows (position-2 profIds).
- [ ] **Merge:** adding a merge to `prof_identity.json` collapses two identities into one on the next export; both sets of offerings resolve to the canonical id.
- [ ] **Professor page:** `#prof/<known>` renders newest-first; class count matches the reverse index; every class links to `#code/...`; co-taught rows show 공동담당.
- [ ] **Search:** typing a professor name yields a 교수-tagged result routing to `#prof/<id>`, showing department(s).
- [ ] **Code page:** professor cell links to `#prof/<id>`.
- [ ] **Dev-only backend:** `/api/prof-merge` writes `prof_identity.json` under `make dev`; the static `index.html` build ships no backend (`/api` 404 under `SERVE_STATIC`).
- [ ] **JSON validity:** `python3 -m json.tool web/data/explore-index.json` and `python3 -m json.tool scraper/prof_identity.json` pass.

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (preview tab hidden — no screenshots).

## Handoff to Spec 3

Spec 3 adds `prev`/`next` code arrays and fills the reserved `#exCodeLinks` container on the code page. It reuses this spec's normalization (`_norm_name`) for its name-based inference grouping — sequence Spec 2's `_norm_name` first if both land together.
