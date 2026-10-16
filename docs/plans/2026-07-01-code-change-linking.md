# Code-Change Linking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a course's `sbjt_cd` changes over time (same course, new code), link the two per-code pages so a reader can jump 이전 과목코드 ↔ 이후 과목코드. Links are **manufactured** (there is no source pointer): automatic **inference** (shared normalized name + department + adjacent term ranges) plus a hand-curated **override map** (`scraper/code_links.json`), rendered into the reserved `#exCodeLinks` slot on the code page. Inferred links are labeled **추정**; curated links are authoritative.

**Architecture:** A post-pass at the end of Spec 1's `_export_explore` (in `scraper/export_json.py`) — run **after** all codes + offerings are grouped, because it needs every code's name set and term range — infers predecessor→successor candidates, merges them with the curated `code_links.json` (`links` add confirmed pairs, `suppress` removes false ones), and writes `prev`/`next` arrays onto each `codes[]` object. The client (in `web/app.js`) reads those arrays (already present on the decoded code objects — no decode change) and fills Spec 1's reserved `#exCodeLinks` container in `renderCodeDetail`, badging `low`-confidence links 추정. An **optional dev-only** review panel (`partials/dev.html` → `server.py` `POST /api/code-link`/`/api/code-suppress`) lets a maintainer confirm/reject inferred `low` links into `code_links.json`.

**Tech Stack:** Python 3 stdlib (`re`, `json`, `pathlib`) for the build post-pass; vanilla browser JS for the client; `scraper/server.py` stdlib HTTP server for the optional dev-only admin endpoints. Depends on **Spec 1** (`_export_explore`, `code_objs` shape `{c, names, o}`, `_EX`/`byCode` client module, `renderCodeDetail` with its reserved `#exCodeLinks` slot) and **Spec 2** (`_norm_name` normalization helper — reused, not duplicated).

## Global Constraints

- **No git commits.** This project is not a git repo; all changes stay in the working tree. Every task ends at a passing `preview_eval` and/or `python3 -m json.tool` check — a **working-tree checkpoint**, never a commit.
- **Verification is `preview_eval` + `python3 -m json.tool` only.** Preview tab is hidden: verify by calling functions and inspecting returned state/DOM strings — never screenshots or awaited rAF.
- **CAVEMAN prose, normal code.** Chat prose terse; all code/JSON/file content normal.
- **CARDINAL-RULE — links are additive and display-only.** The post-pass only **adds** `prev`/`next` arrays; it **never** hides, merges, moves, or blocks an offering or a code page. A wrong link mildly misleads (mitigated by the 추정 label + curated `suppress`); a missing link just means no cross-reference. Both are strictly safer than dropping data.
- **Identity is the CODE, never the name.** These links are a non-authoritative cross-reference layer over Spec 1's per-code pages. Inference stays **conservative**: name+dept alone (no corroborating credits/adjacency) **never** exceeds `low`, and every inferred link is labeled 추정. Only the curated map produces unlabeled (`confirmed`) links.
- **Reuse Spec 2's `_norm_name`.** One shared normalization helper; do not fork a second one (avoids drift — Spec 3 sequences after Spec 2 for exactly this reason). Spec 2 already defines `_norm_name` at module level in `export_json.py`.
- **Data model is additive.** `explore-index.json` stays `version: 1`; `prev`/`next` are new optional keys on `codes[]` objects (omitted when empty). Spec 1's client tolerates their absence; this plan's client tolerates them being missing (`c.prev || []`).

---

## Conventions for this plan (read first)

**No test runner exists.** "Tests" are (a) `python3` snippets that import build helpers or assert over the built JSON, (b) `preview_eval` snippets against the live page, (c) for the optional dev backend, `curl` against a locally-run `server.py`.

**Dev server for the app:** reuse Spec 1's `preview_start` (serves `web/index.html`). Reload with `preview_eval` → `window.location.reload()` after `app.js`/partial/css edits.

**Rebuild after any build change:** `make export-json` (runs `.venv/bin/python scraper/export_json.py`), then `python3 -m json.tool web/data/explore-index.json > /dev/null`.

**Data-independent UI tests.** Real data may or may not contain a renumbering that inference catches. So UI tests for the link renderer build a **synthetic code object** (pointing at a real existing target code so name lookup resolves) rather than relying on a real inferred link. A separate soft check counts/print real inferred links.

**Working-tree checkpoints, NOT commits.** No task runs `git commit`.

**Term index orientation (critical for direction).** `terms[]` is newest-first, so `termIdx 0` = newest term; a **larger** `termIdx` = **older**. For a code, `newest = min(termIdx over its offerings)` and `oldest = max(termIdx)`. The **older** code (larger termIdx range) is `prev`; the **newer** code is `next`. Spec 1 already sorts each code's `o` ascending termIdx, so `o[0]` is that code's newest offering.

**Dev backend (optional Tasks 5-6 only):** run `make dev` (starts `scraper/server.py` with `WEB_INDEX=index-dev.html`) in a background shell; binds `127.0.0.1:8000`. `ADMIN_TOKEN` unset locally ⇒ `_authed()` permissive.

---

## File Structure

- **Modify** `scraper/export_json.py` — add module-level `_infer_code_links`, `_load_code_links`, `_apply_code_links`, and small span helpers; call `_apply_code_links` as a post-pass at the end of `_export_explore` (after `code_objs` is built, before the `explore = {…}` dict). (Responsibility: manufacture + merge `prev`/`next`.)
- **Create** `scraper/code_links.json` — curated `{"links":[],"suppress":[]}` (seed empty).
- **Modify** `web/app.js` — add `renderCodeLinks` + `exCodeLinkAnchor` in the Explore module; one-line change in `renderCodeDetail` to populate the reserved `#exCodeLinks` slot. (Optional Tasks 5-6: dev panel wiring.)
- **Modify** `web/styles.css` — code-link group + 추정 badge styling.
- **(Optional/v2) Modify** `scraper/server.py` — `POST /api/code-link` + `/api/code-suppress` writing `code_links.json`.
- **(Optional/v2) Modify** `web/partials/dev.html` + `web/app.js` — inferred-`low` review panel.

The Makefile's `export-json` target runs `scraper/export_json.py` unchanged — **no Makefile edit needed.**

---

## Task 1: Build the inference + merge post-pass (writes `prev`/`next`)

**Files:**
- Modify: `scraper/export_json.py` — add helpers immediately before `def _export_explore(`; call `_apply_code_links` inside `_export_explore` before the `explore = {…}` assignment.
- Test: `python3` unit snippet on `_infer_code_links` (synthetic fixture) + global-invariant check over the rebuilt `web/data/explore-index.json`.

**Interfaces:**
- Consumes: Spec 1's `code_objs` (list of `{"c": str, "names": [nameId…], "o": [[termIdx,nameId,profId,deptId,credits,ltNo]…]}`, `o` ascending-termIdx); `keys_in_order(name_intern)` (the names string table, built inside `_export_explore`); Spec 2's module-level `_norm_name(s)`.
- Produces: `_infer_code_links(code_objs, names_tbl) -> list[(prev_c, next_c, conf)]` (conf ∈ `high|low`); `_load_code_links(path) -> {"links":[…],"suppress":[…]}`; `_apply_code_links(code_objs, names_tbl, path)` mutates each code obj in place, adding `prev`/`next` arrays of `{"c":str,"conf":str}` (conf ∈ `confirmed|high|low`), omitting empties.

- [ ] **Step 1: Write the failing check (helper absent)**

Run (before the edit):

```bash
cd /home/toxiclemon/project/class-checker && python3 -c "import sys; sys.path.insert(0,'scraper'); import export_json; print('has _infer_code_links:', hasattr(export_json, '_infer_code_links'))"
```

Expected BEFORE: `has _infer_code_links: False`.

- [ ] **Step 2: Add the code-link helpers**

In `scraper/export_json.py`, immediately **before** `def _export_explore(` (Spec 1), add:

```python
# ---- Code-change linking (Spec 3) --------------------------------------------
# Manufacture predecessor<->successor links between sbjt_cd codes. There is NO
# source pointer for a renumbering, so links are inferred (shared normalized name +
# department + adjacent term ranges) and/or curated (scraper/code_links.json).
# Links are additive + display-only: this NEVER hides/merges/moves an offering.
# Reuses Spec 2's module-level _norm_name (do not duplicate it).

CODE_LINK_ADJ_WINDOW = 4    # max termIdx gap between prev's newest and next's oldest
CODE_LINK_OVERLAP_MAX = 1   # allow <=this many shared terms (a transition term); more => concurrent, not a rename


def _code_span(code_obj):
    """(newest_termIdx, oldest_termIdx) for a code. termIdx 0 = newest, so
    newest = min, oldest = max."""
    tis = [o[0] for o in code_obj["o"]]
    return min(tis), max(tis)


def _infer_code_links(code_objs, names_tbl):
    """Infer prev->next candidates from name+dept adjacency. Returns
    [(prev_c, next_c, conf)] with conf in {'high','low'}. Conservative:
    name+dept alone never exceeds 'low' (different courses share names)."""
    groups: dict[tuple, list] = {}
    for c in code_objs:
        cur_name = names_tbl[c["names"][0]] if c["names"] else ""
        norm = _norm_name(cur_name)
        if not norm:
            continue
        primary_dept = c["o"][0][3]                 # newest offering's deptId (home dept)
        groups.setdefault((norm, primary_dept), []).append(c)

    out = []
    for (_norm_key, _dept_id), members in groups.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                a_new, _a_old = _code_span(a)
                b_new, _b_old = _code_span(b)
                if a_new == b_new:
                    continue                        # both still current => not a succession
                prev, nxt = (a, b) if a_new > b_new else (b, a)   # larger newest-termIdx = older = prev
                p_new, _p_old = _code_span(prev)
                _n_new, n_old = _code_span(nxt)
                gap = p_new - n_old                 # +1 == consecutive dataset terms; <=0 == overlap
                if gap > CODE_LINK_ADJ_WINDOW:
                    continue                        # too far apart => likely unrelated name reuse
                overlap = len({o[0] for o in prev["o"]} & {o[0] for o in nxt["o"]})
                if overlap > CODE_LINK_OVERLAP_MAX:
                    continue                        # heavily concurrent => distinct courses
                credits_match = prev["o"][0][4] == nxt["o"][0][4]
                conf = "high" if (credits_match and gap == 1 and overlap == 0) else "low"
                out.append((prev["c"], nxt["c"], conf))
    return out


def _load_code_links(path: Path) -> dict:
    """Curated override map. Missing/empty file -> no-op rules."""
    if not path.exists():
        return {"links": [], "suppress": []}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return {"links": data.get("links", []), "suppress": data.get("suppress", [])}


def _apply_code_links(code_objs, names_tbl, path: Path) -> None:
    """Merge inferred candidates with the curated map and write prev/next arrays
    onto each code object in place: inferred - suppressed + confirmed. Empty
    arrays are omitted. Deterministic (stable input order + sorted output)."""
    curated = _load_code_links(path)
    suppressed = set()
    for s in curated["suppress"]:
        a, b = s.get("a"), s.get("b")
        if a and b:
            suppressed.add(frozenset((a, b)))

    pairs: dict[tuple, str] = {}                    # (prev_c, next_c) -> conf
    for prev_c, next_c, conf in _infer_code_links(code_objs, names_tbl):
        if frozenset((prev_c, next_c)) in suppressed:
            continue
        key = (prev_c, next_c)
        if key not in pairs or (pairs[key] == "low" and conf == "high"):
            pairs[key] = conf
    for l in curated["links"]:                      # confirmed pairs override/add (never 추정)
        prev_c, next_c = l.get("prev"), l.get("next")
        if prev_c and next_c:
            pairs[(prev_c, next_c)] = "confirmed"

    by_code = {c["c"]: c for c in code_objs}
    for c in code_objs:
        c["prev"] = []
        c["next"] = []
    for (prev_c, next_c), conf in pairs.items():
        if prev_c not in by_code or next_c not in by_code:
            continue                                # only link codes that exist
        by_code[next_c]["prev"].append({"c": prev_c, "conf": conf})   # older code shown on newer's page
        by_code[prev_c]["next"].append({"c": next_c, "conf": conf})   # newer code shown on older's page
    for c in code_objs:
        c["prev"].sort(key=lambda x: x["c"])
        c["next"].sort(key=lambda x: x["c"])
        if not c["prev"]:
            del c["prev"]
        if not c["next"]:
            del c["next"]
```

> **`_norm_name` dependency:** Spec 2 defines `_norm_name` at module level in `export_json.py`. Execution order is 1→2→3, so it exists. If (unusually) running this plan without Spec 2, add `def _norm_name(s): return re.sub(r"\s+", " ", (s or "").strip())` once — but **do not duplicate** it if Spec 2 already added it. (`re` is imported top-level by Spec 2's Task 2.)

- [ ] **Step 3: Wire the post-pass into `_export_explore`**

In `_export_explore`, Spec 1 builds `code_objs` and then assembles the `explore` dict:

```python
        code_objs.append({"c": cd, "names": ordered_names, "o": offs})

    explore = {
        "version": 1,
```

Between the end of the `code_objs` loop and the `explore = {` line, insert:

```python
    _apply_code_links(code_objs, keys_in_order(name_intern), Path("scraper/code_links.json"))
```

(`keys_in_order` and `name_intern` are already in scope inside `_export_explore`. This is version-agnostic — it does not touch the `strings`/`profs` emission, so it composes with Spec 2's professor rewrite unchanged. Data stays `version: 1`; `prev`/`next` are additive.)

- [ ] **Step 4: Unit-test `_infer_code_links` on a synthetic fixture**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import sys; sys.path.insert(0, "scraper")
from export_json import _infer_code_links

names = ["미적분학"]                 # nameId 0
# termIdx 0 = newest. OLD code spans termIdx 3..4 (older); NEW code spans 0..2 (newer).
OLD = {"c": "OLD.001", "names": [0], "o": [[3,0,0,0,3,"001"], [4,0,0,0,3,"001"]]}
NEW = {"c": "NEW.001", "names": [0], "o": [[0,0,0,0,3,"001"], [1,0,0,0,3,"001"], [2,0,0,0,3,"001"]]}
r = _infer_code_links([OLD, NEW], names)
assert r == [("OLD.001", "NEW.001", "high")], r     # direction: older=prev; clean adjacency+credits => high

# Overlap => low (transition term shared at termIdx 2)
OLD2 = {"c": "A", "names": [0], "o": [[2,0,0,0,3,"001"], [3,0,0,0,3,"001"]]}
NEW2 = {"c": "B", "names": [0], "o": [[0,0,0,0,3,"001"], [1,0,0,0,3,"001"], [2,0,0,0,3,"001"]]}
r2 = _infer_code_links([OLD2, NEW2], names)
assert r2 == [("A", "B", "low")], r2                # gap == 0 (overlap) => low

# Far-apart name reuse => excluded (gap 6 > window 4)
OLD3 = {"c": "C", "names": [0], "o": [[10,0,0,0,3,"001"]]}
NEW3 = {"c": "D", "names": [0], "o": [[0,0,0,0,3,"001"]]}
assert _infer_code_links([OLD3, NEW3], names) == [], "far reuse should be excluded"

# Credit mismatch => downgraded to low even with clean adjacency
OLD4 = {"c": "E", "names": [0], "o": [[3,0,0,0,3,"001"]]}
NEW4 = {"c": "F", "names": [0], "o": [[2,0,0,0,4,"001"]]}   # 4 credits vs 3
assert _infer_code_links([OLD4, NEW4], names) == [("E", "F", "low")], "credit mismatch => low"

# Same dept required: different deptId => no link
OLD5 = {"c": "G", "names": [0], "o": [[3,0,0,0,3,"001"]]}
NEW5 = {"c": "H", "names": [0], "o": [[2,0,0,1,3,"001"]]}   # deptId 1 vs 0
assert _infer_code_links([OLD5, NEW5], names) == [], "cross-dept must not infer"
print("OK _infer_code_links")
PY
```

Expected: `OK _infer_code_links`.

- [ ] **Step 5: Rebuild + validate JSON**

Run:

```bash
cd /home/toxiclemon/project/class-checker && make export-json && python3 -m json.tool web/data/explore-index.json > /dev/null && echo "JSON OK"
```

Expected: the build prints its per-term + explore lines; `JSON OK`.

- [ ] **Step 6: Global-invariant check over the built JSON**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import json
ex = json.load(open("web/data/explore-index.json", encoding="utf-8"))
by = {c["c"]: c for c in ex["codes"]}
CONF = {"confirmed", "high", "low"}
n_prev = n_next = 0
for c in ex["codes"]:
    for e in c.get("prev", []):
        n_prev += 1
        assert set(e) == {"c", "conf"} and e["conf"] in CONF, e
        assert e["c"] in by, f"prev target {e['c']} missing"
        # symmetry: if X.prev contains P, then P.next must contain X with same conf
        assert any(x["c"] == c["c"] and x["conf"] == e["conf"] for x in by[e["c"]].get("next", [])), \
            f"asymmetric prev {c['c']}<-{e['c']}"
    for e in c.get("next", []):
        n_next += 1
        assert set(e) == {"c", "conf"} and e["conf"] in CONF, e
        assert e["c"] in by, f"next target {e['c']} missing"
        assert any(x["c"] == c["c"] and x["conf"] == e["conf"] for x in by[e["c"]].get("prev", [])), \
            f"asymmetric next {c['c']}->{e['c']}"
assert n_prev == n_next, (n_prev, n_next)   # every next has a mirror prev
print(f"OK links: {n_next} directed pairs, symmetric, all targets exist, conf in {sorted(CONF)}")
PY
```

Expected: `OK links: <N> directed pairs, symmetric, all targets exist, conf in ['confirmed', 'high', 'low']`. `N` may be small or 0 (inference is deliberately conservative); the invariants must hold regardless. **Working-tree checkpoint.**

---

## Task 2: Seed `scraper/code_links.json` + suppress/confirm tests

**Files:**
- Create: `scraper/code_links.json`
- Test: `python3` snippet exercising `_apply_code_links` (suppress removes an inferred pair; a `links` entry adds a `confirmed` pair inference missed).

**Interfaces:**
- Consumes: `_apply_code_links` / `_load_code_links` / `_infer_code_links` (Task 1).
- Produces: the curated file the export reads every build and the optional dev endpoints (Task 5) append to.

- [ ] **Step 1: Create the seed file**

Create `scraper/code_links.json`:

```json
{
  "links": [],
  "suppress": []
}
```

- [ ] **Step 2: Validate the seed is JSON**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 -m json.tool scraper/code_links.json > /dev/null && echo "seed OK"
```

Expected: `seed OK`.

- [ ] **Step 3: Test suppress + confirmed override (synthetic, no rebuild)**

Run:

```bash
cd /home/toxiclemon/project/class-checker && python3 - <<'PY'
import json, tempfile, os, sys
sys.path.insert(0, "scraper")
from pathlib import Path
from export_json import _apply_code_links

names = ["미적분학"]
def fresh():
    OLD = {"c": "OLD.001", "names": [0], "o": [[3,0,0,0,3,"001"], [4,0,0,0,3,"001"]]}
    NEW = {"c": "NEW.001", "names": [0], "o": [[0,0,0,0,3,"001"], [1,0,0,0,3,"001"], [2,0,0,0,3,"001"]]}
    UNREL = {"c": "X.900", "names": [0], "o": [[0,0,0,0,3,"900"]]}   # curated target for a confirmed link
    return [OLD, NEW, UNREL]

def write_curated(obj):
    p = Path(tempfile.mktemp(suffix=".json"))
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p

# Baseline: inference alone links OLD->NEW as high.
objs = fresh()
_apply_code_links(objs, names, Path("/nonexistent_code_links.json"))
by = {c["c"]: c for c in objs}
assert by["NEW.001"].get("prev") == [{"c": "OLD.001", "conf": "high"}], by["NEW.001"].get("prev")

# Suppress OLD.001<->NEW.001 => the inferred link is gone.
objs = fresh()
p = write_curated({"links": [], "suppress": [{"a": "OLD.001", "b": "NEW.001", "note": "test"}]})
_apply_code_links(objs, names, p); os.unlink(p)
by = {c["c"]: c for c in objs}
assert "prev" not in by["NEW.001"] and "next" not in by["OLD.001"], "suppress should remove the pair"

# Confirmed link inference did NOT find (NEW.001 -> X.900) => present as confirmed.
objs = fresh()
p = write_curated({"links": [{"prev": "NEW.001", "next": "X.900", "note": "reform"}], "suppress": []})
_apply_code_links(objs, names, p); os.unlink(p)
by = {c["c"]: c for c in objs}
assert {"c": "NEW.001", "conf": "confirmed"} in by["X.900"].get("prev", []), by["X.900"].get("prev")
assert {"c": "X.900", "conf": "confirmed"} in by["NEW.001"].get("next", []), by["NEW.001"].get("next")
print("OK suppress + confirmed override")
PY
```

Expected: `OK suppress + confirmed override`.

- [ ] **Step 4: Rebuild clean with the empty seed present**

Run:

```bash
cd /home/toxiclemon/project/class-checker && make export-json > /dev/null && python3 -m json.tool web/data/explore-index.json > /dev/null && echo "rebuilt OK"
```

Expected: `rebuilt OK` (the empty seed is a no-op; inference-only output unchanged). **Working-tree checkpoint** (seed file is the empty `{"links":[],"suppress":[]}`).

---

## Task 3: Client — render `prev`/`next` into the reserved `#exCodeLinks` slot

**Files:**
- Modify: `web/app.js` — add `renderCodeLinks` + `exCodeLinkAnchor` in the Explore module; one-line change in `renderCodeDetail` (Spec 1) to populate the reserved slot.
- Test: `preview_eval`

**Interfaces:**
- Consumes: `_EX.byCode` (Map code→codeObj), `_EX.names`; each code obj's optional `c.prev`/`c.next` arrays of `{c, conf}` (from Task 1); Spec 1's `renderCodeDetail` which already creates `const links = el("div", { id: "exCodeLinks", className: "ex-code-links" });`.
- Produces: `renderCodeLinks(container, codeObj)` fills the container with 이전/이후 groups (or leaves it empty → Spec 1's `.ex-code-links:empty{display:none}` hides it); `exCodeLinkAnchor(entry)` renders one `#code/<c>` link with the target's current name, badging 추정 when `conf === "low"`.

- [ ] **Step 1: Verify the slot renders empty (Spec 1 baseline)**

Start/reuse the dev server. `preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const c = _EX.codes.find(x => x.o.length >= 1);
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const slot = document.querySelector('#exCodeLinks');
  const out = JSON.stringify({ hasSlot: !!slot, childCount: slot?.childElementCount ?? -1, hasFn: typeof window.renderCodeLinks });
  location.hash = "#timetable";
  return out;
})();
```

Expected BEFORE: `{"hasSlot":true,"childCount":0,"hasFn":"undefined"}` — the reserved slot exists but is empty and the renderer is absent.

- [ ] **Step 2: Add `renderCodeLinks` + `exCodeLinkAnchor`**

In `web/app.js`, at the end of the Explore module (after `renderCodeDetail`/`exProfLinks`, before the `addEventListener("pagehide"...` line), append:

```js
// ---- 과목코드 변경 링크 (Spec 3): fill the reserved #exCodeLinks slot ----
// Renders 이전/이후 과목코드 as links to #code/<c>. conf 'confirmed'/'high' -> plain
// link; 'low' -> link + 추정 badge (inferred, may be wrong). Display-only.
function exCodeLinkAnchor(entry) {
  const target = _EX.byCode.get(entry.c);
  const nm = target ? (_EX.names[target.names[0]] || "(이름 없음)") : "(알 수 없음)";
  const a = el("a", { className: "ex-clink", href: "#code/" + encodeURIComponent(entry.c) },
    el("span", { className: "ex-clink-name" }, nm),
    el("span", { className: "ex-clink-code" }, entry.c));
  if (entry.conf === "low") a.append(el("span", { className: "ex-badge-guess" }, "추정"));
  a.onclick = (e) => { e.preventDefault(); location.hash = "#code/" + encodeURIComponent(entry.c); };
  return a;
}

function renderCodeLinks(container, codeObj) {
  container.replaceChildren();
  const prev = codeObj.prev || [];
  const next = codeObj.next || [];
  if (!prev.length && !next.length) return;       // stays empty -> hidden by .ex-code-links:empty
  if (prev.length) {
    container.append(el("div", { className: "ex-clink-group" },
      el("span", { className: "ex-clink-label" }, "이전 과목코드"),
      ...prev.map(exCodeLinkAnchor)));
  }
  if (next.length) {
    container.append(el("div", { className: "ex-clink-group" },
      el("span", { className: "ex-clink-label" }, "이후 과목코드"),
      ...next.map(exCodeLinkAnchor)));
  }
}
```

- [ ] **Step 3: Populate the slot from `renderCodeDetail`**

In `renderCodeDetail` (Spec 1), find the reserved-slot line:

```js
  // reserved slot for Spec 3 prev/next code links — rendered empty in Core.
  const links = el("div", { id: "exCodeLinks", className: "ex-code-links" });
```

Replace it with:

```js
  // prev/next code links (Spec 3) filled into the reserved slot; empty -> hidden by CSS.
  const links = el("div", { id: "exCodeLinks", className: "ex-code-links" });
  renderCodeLinks(links, c);
```

(`c` is the code object already fetched from `_EX.byCode` earlier in `renderCodeDetail`. The `box.replaceChildren(header, rename, links, table);` line is unchanged — `links` now carries content when the code has neighbors.)

- [ ] **Step 4: Verify the renderer (synthetic, data-independent)**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const realTarget = _EX.codes[0].c;              // a real code so name lookup resolves
  const fake = { c: "SYNTH.TEST", names: _EX.codes[0].names,
    prev: [{ c: realTarget, conf: "confirmed" }],
    next: [{ c: realTarget, conf: "low" }] };
  const box = document.createElement("div");
  renderCodeLinks(box, fake);
  const groups = box.querySelectorAll('.ex-clink-group');
  const labels = [...box.querySelectorAll('.ex-clink-label')].map(s => s.textContent);
  const badges = box.querySelectorAll('.ex-badge-guess');
  const hrefs = [...box.querySelectorAll('a.ex-clink')].map(a => a.getAttribute('href'));
  return JSON.stringify({
    groupCount: groups.length,                    // 2 (이전 + 이후)
    labels,                                        // ["이전 과목코드","이후 과목코드"]
    badgeCount: badges.length,                     // 1 (only the low/next one)
    allCodeHrefs: hrefs.every(h => h.startsWith('#code/')),
    // empty case hides
    emptyStaysEmpty: (() => { const e = document.createElement("div"); renderCodeLinks(e, { c: "z" }); return e.childElementCount === 0; })()
  });
})();
```

Expected AFTER: `{"groupCount":2,"labels":["이전 과목코드","이후 과목코드"],"badgeCount":1,"allCodeHrefs":true,"emptyStaysEmpty":true}` — confirmed link has no badge, low link has 추정, empty stays empty.

- [ ] **Step 5: Soft check against real inferred links (if any)**

`preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const withLinks = _EX.codes.filter(c => (c.prev && c.prev.length) || (c.next && c.next.length));
  let sample = null;
  if (withLinks.length) {
    const c = withLinks[0];
    location.hash = "#code/" + encodeURIComponent(c.c);
    await renderExplore("code", c.c);
    const slot = document.querySelector('#exCodeLinks');
    sample = { code: c.c, renderedChildren: slot?.childElementCount ?? -1, anchorCount: slot?.querySelectorAll('a.ex-clink').length ?? 0 };
    location.hash = "#timetable";
  }
  return JSON.stringify({ realCodesWithLinks: withLinks.length, sample });
})();
```

Expected AFTER: `realCodesWithLinks` ≥ 0. If > 0, `sample.renderedChildren` > 0 and `sample.anchorCount` > 0 (the reserved slot fills for a real linked code). If 0, inference produced no links on this dataset — still valid; Step 4 already proved the renderer. **Working-tree checkpoint.**

---

## Task 4: Styles (code-link groups + 추정 badge)

**Files:**
- Modify: `web/styles.css` (append at end)
- Test: `preview_eval`

**Interfaces:**
- Consumes: existing CSS variables (`--cp-line`, `--cp-accent`, `--cp-ink`, `--cp-muted`). Spec 1 already ships `.ex-code-links:empty { display: none; }`.
- Produces: layout for `.ex-clink-group`, `.ex-clink-label`, `.ex-clink`, `.ex-clink-name`, `.ex-clink-code`, `.ex-badge-guess`.

- [ ] **Step 1: Append code-link styles**

At the end of `web/styles.css`, append:

```css
/* ==================== 강의탐색: 과목코드 변경 링크 (Spec 3) ==================== */
.ex-code-links { display: flex; flex-direction: column; gap: 6px; margin: 10px 0 4px; }
.ex-clink-group { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.ex-clink-label { font-size: 12px; color: var(--cp-muted); min-width: 84px; }
.ex-clink { display: inline-flex; align-items: baseline; gap: 6px; padding: 3px 9px;
  border: 1px solid var(--cp-line); border-radius: 999px; text-decoration: none; color: var(--cp-ink); }
.ex-clink:hover { border-color: var(--cp-accent);
  background: color-mix(in srgb, var(--cp-accent) 6%, transparent); }
.ex-clink-name { font-weight: 600; font-size: 13px; }
.ex-clink-code { font-size: 11.5px; color: var(--cp-muted); font-variant-numeric: tabular-nums; }
.ex-badge-guess { font-size: 10px; padding: 0 5px; border-radius: 999px; color: var(--cp-muted);
  border: 1px solid var(--cp-line); }
```

(`.ex-code-links:empty { display: none; }` is already in Spec 1's Task 7 — not repeated here.)

- [ ] **Step 2: Verify styles apply (synthetic render into the live DOM)**

`preview_eval` → `window.location.reload()`, then after reload `preview_eval`:

```js
(async () => {
  await ensureExploreData();
  const c = _EX.codes.find(x => x.o.length >= 1);
  location.hash = "#code/" + encodeURIComponent(c.c);
  await renderExplore("code", c.c);
  const slot = document.querySelector('#exCodeLinks');           // real slot, in the document
  const realTarget = _EX.codes[0].c;
  renderCodeLinks(slot, { c: c.c, names: c.names, next: [{ c: realTarget, conf: "low" }] });
  const group = slot.querySelector('.ex-clink-group');
  const clink = slot.querySelector('.ex-clink');
  const badge = slot.querySelector('.ex-badge-guess');
  const res = JSON.stringify({
    slotDisplay: getComputedStyle(slot).display,                  // flex (not none — has children)
    groupDisplay: group ? getComputedStyle(group).display : null, // flex
    clinkRadius: clink ? getComputedStyle(clink).borderRadius : null, // 999px pill
    badgePresent: !!badge
  });
  location.hash = "#timetable";
  return res;
})();
```

Expected AFTER: `slotDisplay:"flex"`, `groupDisplay:"flex"`, `clinkRadius` a large px (pill), `badgePresent:true`. **Working-tree checkpoint.**

---

## Task 5 (OPTIONAL / v2): Dev backend — `POST /api/code-link` + `/api/code-suppress`

> **Optional.** Mechanisms 1+2 (inference + hand-edited `code_links.json`) fully deliver the feature. This task only adds a convenience write path for the review panel (Task 6). Skip both if a maintainer will hand-edit `code_links.json`.

**Files:**
- Modify: `scraper/server.py` — `do_POST` routing + a `_code_link_write` handler.
- Test: `curl` against a locally-run `server.py`.

**Interfaces:**
- Consumes: existing `_authed()` ([server.py:302](scraper/server.py:302)), `_json()` ([server.py:125](scraper/server.py:125)); the body-read pattern from `_lookup`. Mirrors Spec 2's `_prof_identity_write` exactly (same shape, different file/buckets).
- Produces: two admin-gated POST endpoints appending to `scraper/code_links.json` (`links` / `suppress`), creating it if absent; returns updated counts. Writes only the working-tree JSON — never the DB.

- [ ] **Step 1: Verify the endpoints 404 before adding them**

Start the dev backend (`make dev` in a background shell). Then:

```bash
curl -s -X POST http://127.0.0.1:8000/api/code-link -H 'Content-Type: application/json' \
  -d '{"prev":"A","next":"B"}' -w '\n%{http_code}\n'
```

Expected BEFORE: `{"error": "not found"}` then `404`.

- [ ] **Step 2: Add the routing + handler**

In `do_POST`, before the final 404 return, add:

```python
        if u.path == "/api/code-link":
            return self._code_link_write("links")
        if u.path == "/api/code-suppress":
            return self._code_link_write("suppress")
```

Add the handler alongside the others (e.g. after `_prof_identity_write` from Spec 2, or after `_lookup`):

```python
    _CODE_LINKS_PATH = Path(__file__).with_name("code_links.json")

    def _code_link_write(self, bucket):
        """Append a link/suppress rule to scraper/code_links.json. Admin-gated;
        writes only the curated working-tree JSON — never the DB. Returns counts."""
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
            data = json.loads(self._CODE_LINKS_PATH.read_text(encoding="utf-8")) \
                if self._CODE_LINKS_PATH.exists() else {}
        except json.JSONDecodeError:
            data = {}
        data.setdefault("links", []); data.setdefault("suppress", [])
        data[bucket].append(rule)
        self._CODE_LINKS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._json({"ok": True, "count": {k: len(data[k]) for k in ("links", "suppress")}})
```

(`from pathlib import Path` is already imported by Spec 2's Task 9; if running this task without Spec 2, add it at the top of `server.py`.)

- [ ] **Step 3: Verify a link is appended, then restore the seed**

Restart the dev backend (so the new code loads), then:

```bash
cd /home/toxiclemon/project/class-checker && cp scraper/code_links.json /tmp/cl_backup.json
curl -s -X POST http://127.0.0.1:8000/api/code-link -H 'Content-Type: application/json' \
  -d '{"prev":"P.001","next":"N.001","note":"curl test"}' -w '\n%{http_code}\n'
curl -s -X POST http://127.0.0.1:8000/api/code-suppress -H 'Content-Type: application/json' \
  -d '{"a":"X.001","b":"Y.001","note":"curl test"}' -w '\n%{http_code}\n'
python3 -c "import json; d=json.load(open('scraper/code_links.json')); assert d['links'][-1]['prev']=='P.001' and d['suppress'][-1]['a']=='X.001', d; print('OK appended')"
cp /tmp/cl_backup.json scraper/code_links.json   # restore empty seed
```

Expected: two `{"ok": true, ...}` + `200` responses, then `OK appended`, and the seed is restored. **Working-tree checkpoint.**

---

## Task 6 (OPTIONAL / v2): Dev management panel (inferred-`low` review)

> **Optional.** Depends on Task 5. Lets a maintainer confirm/reject inferred `low` links from the browser; each decision POSTs to Task 5's endpoints, writing `code_links.json` for the next export.

**Files:**
- Modify: `web/partials/dev.html` — add a review panel section.
- Modify: `web/app.js` — build the inferred-`low` list from `_EX` + wire the load button (guarded — nodes absent in production).
- Modify: `web/styles.css` — panel styles.
- Test: `preview_eval` against `index-dev.html`.

**Interfaces:**
- Consumes: `_EX.codes` (each code's `next` array carries `low` links), `_EX.byCode`, `_EX.names`, `api("/api/code-link" | "/api/code-suppress", …)` (Task 5), the `refreshDB` fetch pattern ([app.js:1494](web/app.js:1494)).
- Produces: a `#codeLinkReview` panel listing each inferred `low` pair once (iterating `next` on the older code avoids the symmetric double-count) with Confirm/Reject buttons. Absent in production (guarded like `#refreshBtn`).

- [ ] **Step 1: Add the panel markup to `dev.html`**

In `web/partials/dev.html`, before the closing `</div></div>` of the `dev-page`, add:

```html
<section class="panel code-link-review">
  <div class="admin-head">
    <h2>과목코드 변경 확인 <span class="sub">Code-change links</span></h2>
    <button id="codeLinkReviewLoad">추정 링크 불러오기 Load inferred</button>
  </div>
  <p class="admin-note">추론된 과목코드 연결(추정)입니다. 동일 과목의 코드 변경이면 확정,
     아니면 거부하세요. 결정은 scraper/code_links.json에 저장되어 다음 export에 반영됩니다.</p>
  <div id="codeLinkReview" class="code-link-body"></div>
</section>
```

- [ ] **Step 2: Wire the panel builder in `app.js`**

At the end of the Explore module, add (guarded so it no-ops when the dev node is absent):

```js
async function loadCodeLinkReview() {
  const box = $("#codeLinkReview"); if (!box) return;
  await ensureExploreData();
  const lows = [];
  for (const c of _EX.codes) {
    for (const e of (c.next || [])) if (e.conf === "low") lows.push({ prev: c.c, next: e.c });
  }
  box.replaceChildren(el("div", { className: "cl-count" }, `${lows.length}개 추정 링크`));
  for (const { prev, next } of lows.slice(0, 200)) {
    const pc = _EX.byCode.get(prev), nc = _EX.byCode.get(next);
    const pn = pc ? (_EX.names[pc.names[0]] || "?") : "?";
    const nn = nc ? (_EX.names[nc.names[0]] || "?") : "?";
    const g = el("div", { className: "cl-group" },
      el("div", { className: "cl-pair" }, `${pn} (${prev}) → ${nn} (${next})`));
    const ok = el("button", { className: "cl-confirm" }, "확정 Confirm");
    const no = el("button", { className: "cl-reject" }, "거부 Reject");
    ok.onclick = async () => {
      ok.disabled = no.disabled = true;
      try {
        await api("/api/code-link", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prev, next, note: `confirmed ${new Date().toISOString().slice(0, 10)}` }) });
        ok.textContent = "저장됨 Saved";
      } catch { ok.textContent = "실패 Failed"; ok.disabled = no.disabled = false; }
    };
    no.onclick = async () => {
      ok.disabled = no.disabled = true;
      try {
        await api("/api/code-suppress", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ a: prev, b: next, note: `rejected ${new Date().toISOString().slice(0, 10)}` }) });
        no.textContent = "저장됨 Saved";
      } catch { no.textContent = "실패 Failed"; ok.disabled = no.disabled = false; }
    };
    g.append(ok, no);
    box.append(g);
  }
}
```

Then wire the load button in `init()` alongside the other guarded dev wirings (near `#refreshBtn`, [app.js:2825](web/app.js:2825)):

```js
  const clBtn = $("#codeLinkReviewLoad");   // dev panel — absent in production
  if (clBtn) clBtn.addEventListener("click", loadCodeLinkReview);
```

- [ ] **Step 3: Append panel styles**

At the end of `web/styles.css`, append:

```css
/* ---- Dev: code-change link review panel ---- */
.code-link-body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.cl-count { font-size: 12px; color: var(--cp-muted); }
.cl-group { border: 1px solid var(--cp-line); border-radius: 8px; padding: 8px 10px;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cl-pair { flex: 1; font-size: 13px; color: var(--cp-ink); }
.cl-confirm, .cl-reject { font-size: 12px; }
```

- [ ] **Step 4: Verify the panel builds (dev shell)**

Point the preview at the dev shell — `preview_eval` → `window.location.href = "index-dev.html"`, wait for load, then `preview_eval`:

```js
(async () => {
  if (!document.querySelector('#codeLinkReview')) return JSON.stringify({ devPanel: false });
  await loadCodeLinkReview();
  const box = document.querySelector('#codeLinkReview');
  return JSON.stringify({
    devPanel: true,
    hasCount: !!box.querySelector('.cl-count'),
    groupCount: box.querySelectorAll('.cl-group').length,        // == number of inferred low links (may be 0)
    hasButtons: !!box.querySelector('.cl-confirm') || box.querySelectorAll('.cl-group').length === 0
  });
})();
```

Expected AFTER: `{"devPanel":true,"hasCount":true,"groupCount":<N>=0>,"hasButtons":true}` — the panel builds its count line; groups appear when inferred `low` links exist. Then restore: `preview_eval` → `window.location.href = "index.html"`. **Working-tree checkpoint.**

> After confirming/rejecting via this panel, the maintainer runs `make export-json` to regenerate `explore-index.json` with the curated decisions applied. The panel writes only `code_links.json`; it does not rebuild live.

---

## Final verification (whole feature)

- [ ] **Build:** `_infer_code_links` fixture test passes (direction, conf tiers, exclusions); the built `explore-index.json` passes the global-invariant check (symmetry, targets exist, `conf ∈ {confirmed,high,low}`).
- [ ] **Curated merge:** a `suppress` pair removes an inferred link; a `links` pair inference missed appears as `confirmed`.
- [ ] **UI slot:** the code page fills `#exCodeLinks` with 이전/이후 groups when neighbors exist; `low` links show a 추정 badge, `confirmed`/`high` do not; a code with no neighbors renders nothing (slot hidden).
- [ ] **Navigation:** clicking a link routes to the neighbor's `#code/<c>`, which itself shows its own prev/next (chain-walkable).
- [ ] **JSON validity:** `python3 -m json.tool web/data/explore-index.json` and `python3 -m json.tool scraper/code_links.json` pass; every `prev`/`next` target `c` exists in `codes`.
- [ ] **Safety:** links are additive only — no offering or code page is hidden/merged/blocked by this feature.
- [ ] **(If Tasks 5-6 done)** `/api/code-link` + `/api/code-suppress` write `code_links.json` under `make dev`; the dev panel lists inferred `low` links; static `index.html` ships no backend (`/api` 404 under `SERVE_STATIC`).

Verification per standing directive: `preview_eval` + `python3 -m json.tool` (preview tab hidden — no screenshots).

## Notes / open questions carried from the spec

- **Adjacency window** (`CODE_LINK_ADJ_WINDOW = 4`) and **overlap tolerance** (`CODE_LINK_OVERLAP_MAX = 1`) start strict. Widen only if real renumberings are being missed (measure before loosening).
- **Normalization aggressiveness:** exact normalized-name match only (via Spec 2's `_norm_name` — trim + collapse whitespace). Do not strip level/roman-numeral suffixes unless false negatives prove it's needed.
- **Cross-department moves** are intentionally **not** inferred (grouping requires same home dept) — that is what curated `links` are for.
- **Primary-dept grouping** uses each code's newest-offering dept; a code whose newest offering is a cross-listed dept may be mis-grouped → missed inference → curated fix. Documented limitation, low harm.
