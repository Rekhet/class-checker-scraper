# 졸업요건 cross-dept 전공선택 recognition rework + foldable list — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single coarse `external_recognition.depts` substring matcher with a 4-mechanism matcher (exact-code / code-prefix / college / legacy-dept), migrate econ·me·stat data to the correct mechanism, and add a foldable `<details>` field listing exactly which courses counted as 전공선택.

**Architecture:** All audit logic lives in `_gradAuditBlock` (a top-level global function in `web/app.js`). The matcher is rewritten in place; recognition rules become per-major JSON in `web/data/grad_req/*.json`. The foldable reuses the new `recogRows` array. CSS goes in `web/styles.css`.

**Tech Stack:** Vanilla browser JS (classic `<script>`, globals on `window`, `"use strict"` at file top but **no** IIFE wrapper — so `_gradAuditBlock`, `_loadCodeEquiv`, `el`, `_gradState` are all reachable by bare name in `preview_eval`). No bundler, no JS test runner, no HMR.

**Spec:** `docs/superpowers/specs/2026-06-25-grad-req-cross-dept-recognition-design.md`

---

## Conventions for this plan

- **No commits.** Standing directive: working-tree only. Every task ends with a **verification checkpoint**, not a git commit. Do not run `git add`/`git commit`.
- **No test runner exists.** "Tests" are (a) `python3 -m json.tool` for JSON validity and (b) `preview_eval` harnesses that call the real `_gradAuditBlock` with synthetic inputs and assert on the returned DOM node's text. This matches the codebase's existing manual-verification pattern (preview tab hidden per memory → use `preview_eval`, never screenshots/rAF).
- **Static app, no HMR.** After editing `web/app.js` or `web/styles.css` you MUST reload the preview (`preview_eval` → `location.reload()`) before the harness sees the new code. JSON data edits do **not** need a reload because the harnesses pass *synthetic* specs (they verify the matcher, not the on-disk JSON); JSON files are verified separately by `json.tool`.
- **Preview server:** start once before Task 1 (`preview_start`), reuse for all checks.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `web/app.js` | matcher rewrite (`isRecog`, `recogRows`/`recogCr`, `college` threading) + foldable `<details>` | 1, 2 |
| `web/styles.css` | foldable styling (`.grad-fold`, `.gfold-*`, `.gfr-*`) | 2 |
| `web/data/grad_req/econ_2026.json`, `econ_2023_2024.json` | econ → 48-course allowlist, drop `depts` | 3 |
| `web/data/grad_req/me_2026.json`, `me_2023_2024.json` | me → prefixes + colleges + 10 경영 entries, drop label `depts` | 4 |
| `web/data/grad_req/stat_2021_2022.json`, `stat_2023_2024.json`, `stat_2025.json`, `stat_2026.json` | stat → add `recog_max` (9 / 0) per track | 5 |
| (all of the above) | final regression + JSON validity + real-app smoke | 6 |

---

## Task 1: Matcher rewrite in `_gradAuditBlock`

Thread `college` into rows, reorder `hasCls` above the recognition setup, and replace `isRecog(dept)` with a 4-mechanism `isRecog(row)` that excludes own-major courses. Introduce `recogRows` (reused by Task 2's foldable).

**Files:**
- Modify: `web/app.js` (row map ~2388-2392; `isStat`/`isRecog`/`hasCls` block ~2415-2417; `recogCr` block ~2437-2438)

- [ ] **Step 1: Start the preview server** (once, reused by all later tasks)

Use `preview_start` on the `web/` app. Confirm it serves (a later `preview_eval` returning a value proves it).

- [ ] **Step 2: Thread `college` into each row**

In `web/app.js`, replace the row map:

```js
  const rows = taken.map((c) => {
    const m = cat.get(classKey(c)) || {};
    return { name: c.name, sbjt_cd: c.sbjt_cd, credits: Number(m.credits ?? c.credits ?? 0) || 0,
      cls: m.classification || c.classification || [], dept: m.department || c.dept || c.department || "" };
  });
```

with (adds the `college` field; `m` is the live-catalog record from `lookupLocal`, which carries `college`; manual rows fall back to `""` and never match college-mode):

```js
  const rows = taken.map((c) => {
    const m = cat.get(classKey(c)) || {};
    return { name: c.name, sbjt_cd: c.sbjt_cd, credits: Number(m.credits ?? c.credits ?? 0) || 0,
      cls: m.classification || c.classification || [], dept: m.department || c.dept || c.department || "",
      college: m.college || c.college || "" };
  });
```

- [ ] **Step 3: Replace the `isStat`/`isRecog`/`hasCls` block with the 4-mechanism matcher**

Replace this block (note: original order is isStat, isRecog, hasCls):

```js
  const isStat = (d) => (spec.major_required_match?.departments || []).some((x) => (d || "").includes(x));
  const isRecog = (d) => (spec.external_recognition?.depts || []).some((x) => (d || "").includes(x.replace(/부$/, "")));
  const hasCls = (r, t) => (r.cls || []).includes(t);
```

with (reorders `hasCls` up so `isRecog` can close over it; `canon` is already defined just above at the top of `_gradAuditBlock`):

```js
  const isStat = (d) => (spec.major_required_match?.departments || []).some((x) => (d || "").includes(x));
  const hasCls = (r, t) => (r.cls || []).includes(t);
  const er = spec.external_recognition || {};
  const recogCodes = new Set((er.courses || []).map((c) => canon(c.code)));
  const recogPrefix = er.code_prefixes || [];
  const recogColl = er.colleges || [];
  const recogDepts = er.depts || [];
  const isRecog = (r) => {
    if (isStat(r.dept)) return false;                                  // own major handled by isStat path (no double-count)
    const code = canon(r.sbjt_cd);
    if (recogCodes.has(code)) return true;                            // designated course — any classification
    if (recogPrefix.some((p) => code.startsWith(p))) return true;     // 공대공통 prefix — any classification
    const isMajorCourse = hasCls(r, "전선") || hasCls(r, "전필");
    if (recogColl.includes(r.college) && isMajorCourse) return true;  // college 전공 course
    if (recogDepts.some((x) => (r.dept || "").includes(x.replace(/부$/, ""))) && isMajorCourse) return true;
    return false;
  };
```

- [ ] **Step 4: Replace the `recogCr` block to build `recogRows` first**

Replace:

```js
  let recogCr = rows.filter((r) => isRecog(r.dept) && (hasCls(r, "전선") || hasCls(r, "전필"))).reduce((s, r) => s + r.credits, 0);
  if (track.recog_max != null) recogCr = Math.min(recogCr, track.recog_max);   // CSE caps 인정 (주전공 12 / 복수 6)
```

with (classification gating now lives *inside* `isRecog` for college/dept modes; `recogRows` is reused by the foldable in Task 2):

```js
  const recogRows = rows.filter((r) => isRecog(r));
  let recogCr = recogRows.reduce((s, r) => s + r.credits, 0);
  if (track.recog_max != null) recogCr = Math.min(recogCr, track.recog_max);   // recog_max cap (econ 12 / me 15 / stat 9; 부전공 0)
```

- [ ] **Step 5: Reload preview, then verify the matcher with a `preview_eval` harness**

First call `preview_eval`:

```js
location.reload(); "reloading"
```

Then in a **fresh** `preview_eval` call run the harness (tests: designated course recognized, 컴공 rejected, legacy-dept mode still works):

```js
const ce = await _loadCodeEquiv();
const mk = (n,c,cls,d,col,cr=3) => ({name:n, sbjt_cd:c, credits:cr, cls, dept:d, college:col});
const track = { name:'단일', general:false, major_min_credits:60, select_min:0, recog_max:null, required_credits:0 };
// Scenario A — econ-style course allowlist
const specA = { major_required_match:{departments:['경제학부']}, major_select_match:{departments:['경제학부']},
  external_recognition:{ courses:[{code:'251.205',name:'회계원리',dept:'경영학과'}] }, total_credits:130, suri:{seq:[],combined:null} };
const rowsA = [ mk('회계원리','251.205',['전선'],'경영학과','경영대학'),
  mk('자료구조','M1522.000300',['전선'],'컴퓨터공학부','공과대학') ];
const rA = _gradAuditBlock(specA, track, rowsA, [], {type:'single',year:'2026'}, 0, null, {codes:{},exceptions:{}}, ce);
const fA = rA.node.querySelector('.grad-fold');
// Scenario B — legacy depts mode (cse-style) still recognizes a major course from the listed dept
const specB = { major_required_match:{departments:['경제학부']}, major_select_match:{departments:['경제학부']},
  external_recognition:{ depts:['컴퓨터공학부'] }, total_credits:130, suri:{seq:[],combined:null} };
const rB = _gradAuditBlock(specB, track, [mk('자료구조','M1522.000300',['전선'],'컴퓨터공학부','공과대학')], [], {type:'single',year:'2026'}, 0, null, {codes:{},exceptions:{}}, ce);
const noteB = [...rB.node.querySelectorAll('.grad-note')].map(x=>x.textContent).find(t=>/반영/.test(t)) || '';
return JSON.stringify({
  A_foldRenders: !!fA,
  A_econRecognized: /회계원리/.test(fA?.textContent || ''),
  A_cseRejected: !/자료구조/.test(fA?.textContent || ''),
  B_deptsRecognized: /3학점 반영/.test(noteB)
});
```

Expected (exact): `{"A_foldRenders":true,"A_econRecognized":true,"A_cseRejected":true,"B_deptsRecognized":true}`

If `_gradAuditBlock is not defined` → the app is wrapped in an IIFE; fall back to `window._gradAuditBlock` / stop and report. If `A_cseRejected` is false → the designated-vs-dept short-circuit is wrong (re-check Step 3 order).

- [ ] **Step 6: Verification checkpoint (no commit)**

Confirm all four assertions are `true`. Do not commit. Proceed to Task 2.

---

## Task 2: Foldable 전공선택 field + CSS

Render a collapsed `<details>` after the 전공선택 bars listing own-major 전선 (`majorSelRows`) and cross-dept recognized courses (`recogRows`), each row showing its offering 학과.

**Files:**
- Modify: `web/app.js` (insert after the recog note ~2558, before `sections.push(major)`)
- Modify: `web/styles.css` (append foldable rules near the other `grad-*` rules)

- [ ] **Step 1: Insert the foldable block**

In `web/app.js`, replace:

```js
  if (recogCr) major.append(el("div", { className: "grad-note" }, `전공선택인정(수리·컴공 등): ${recogCr}학점 반영`));
  sections.push(major);
```

with:

```js
  if (recogCr) major.append(el("div", { className: "grad-note" }, `전공선택인정(수리·컴공 등): ${recogCr}학점 반영`));
  if (majorSelRows.length || recogRows.length) {
    const fold = el("details", { className: "grad-fold" });
    fold.append(el("summary", {}, `전공선택 인정 과목 ${majorSelRows.length + recogRows.length}개 · ${selectCredits}학점`));
    const courseLine = (r) => el("div", { className: "gfold-row" },
      el("span", { className: "gfr-name" }, r.name),
      el("span", { className: "gfr-code" }, r.sbjt_cd),
      el("span", { className: "gfr-cr" }, `${r.credits}학점`),
      el("span", { className: "gfr-dept" }, r.dept || "—"));
    if (majorSelRows.length) {
      fold.append(el("div", { className: "gfold-grp" }, "전공 (전선)"));
      majorSelRows.forEach((r) => fold.append(courseLine(r)));
    }
    if (recogRows.length) {
      const cap = track.recog_max != null ? ` (최대 ${track.recog_max}학점, 반영 ${recogCr})` : "";
      fold.append(el("div", { className: "gfold-grp" }, "타과 인정" + cap));
      recogRows.forEach((r) => fold.append(courseLine(r)));
    }
    major.append(fold);
  }
  sections.push(major);
```

- [ ] **Step 2: Append the foldable CSS**

Open `web/styles.css`, locate the existing `.grad-note` rule (the grad-audit styles cluster), and append this block immediately after it (or at end of file if simpler — it is self-contained). `--cp-ink` is the existing ink color variable used by the other grad rules:

```css
.grad-fold { margin-top: 6px; font-size: 12px; }
.grad-fold > summary { cursor: pointer; color: var(--cp-ink); opacity: .8; font-variant-numeric: tabular-nums; }
.grad-fold > summary:hover { opacity: 1; }
.gfold-grp { margin: 6px 0 2px; font-weight: 600; color: var(--cp-ink); opacity: .7; }
.gfold-row { display: grid; grid-template-columns: 1fr auto auto auto; gap: 8px; align-items: baseline;
  padding: 2px 0; color: var(--cp-ink); font-variant-numeric: tabular-nums; }
.gfold-row .gfr-code { opacity: .55; font-size: 11px; }
.gfold-row .gfr-cr { opacity: .7; }
.gfold-row .gfr-dept { opacity: .7; text-align: right; }
```

- [ ] **Step 3: Reload preview, then verify the foldable renders both groups**

`preview_eval`:

```js
location.reload(); "reloading"
```

Then in a fresh `preview_eval`:

```js
const ce = await _loadCodeEquiv();
const mk = (n,c,cls,d,col,cr=3) => ({name:n, sbjt_cd:c, credits:cr, cls, dept:d, college:col});
const track = { name:'단일', general:false, major_min_credits:60, select_min:0, recog_max:null, required_credits:0 };
const spec = { major_required_match:{departments:['경제학부']}, major_select_match:{departments:['경제학부']},
  external_recognition:{ courses:[{code:'251.205',name:'회계원리',dept:'경영학과'}] }, total_credits:130, suri:{seq:[],combined:null} };
const rows = [ mk('회계원리','251.205',['전선'],'경영학과','경영대학'),
  mk('화폐금융론','212.301',['전선'],'경제학부','사회과학대학') ];   // own-major 전선
const r = _gradAuditBlock(spec, track, rows, [], {type:'single',year:'2026'}, 0, null, {codes:{},exceptions:{}}, ce);
const summary = r.node.querySelector('details.grad-fold > summary')?.textContent || '';
const grps = [...r.node.querySelectorAll('.gfold-grp')].map(x => x.textContent);
const depts = [...r.node.querySelectorAll('.gfold-row .gfr-dept')].map(x => x.textContent);
return JSON.stringify({ summary, grps, depts });
```

Expected (exact): `{"summary":"전공선택 인정 과목 2개 · 6학점","grps":["전공 (전선)","타과 인정"],"depts":["경제학부","경영학과"]}`

(`gfr-dept` renders `r.dept`. Order is own-major 전선 first (화폐금융론 → `경제학부`), then 타과 인정 (회계원리 → `경영학과`). recog_max is null here so the 타과 group label has no cap suffix.)

- [ ] **Step 4: Verification checkpoint (no commit)**

Confirm summary count = 2, both group labels present, both 학과 shown. Do not commit. Proceed to Task 3.

---

## Task 3: econ data — 48-course allowlist, drop `depts`

Replace `external_recognition.depts` (11 whole departments → over-accepts) with the curated 48-course `courses` allowlist. Keep `approval_max_credits:12`. Tracks already carry `recog_max` 12/12/12/0 (unchanged).

**Files:**
- Modify: `web/data/grad_req/econ_2026.json` (lines 14-17)
- Modify: `web/data/grad_req/econ_2023_2024.json` (lines 17-32)

The replacement `external_recognition` value is **identical for both files** (only the matched old text differs). New value:

```json
  "external_recognition": {
    "courses": [
      {"code":"216A.210","name":"행정학서론","dept":"정치외교학부"},
      {"code":"216A.304","name":"한국정치론","dept":"정치외교학부"},
      {"code":"216B.302","name":"국제정치이론","dept":"정치외교학부"},
      {"code":"216B.223","name":"국제정치경제론","dept":"정치외교학부"},
      {"code":"208.205","name":"경제지리학","dept":"지리학과"},
      {"code":"208.401A","name":"법제지리학","dept":"지리학과"},
      {"code":"206.331","name":"문화와 경제","dept":"인류학과"},
      {"code":"209.232","name":"복지국가원론","dept":"사회복지학과"},
      {"code":"209.304","name":"사회복지정책","dept":"사회복지학과"},
      {"code":"211.227A","name":"커뮤니케이션,문명,사회변동","dept":"언론정보학과"},
      {"code":"251.101","name":"경영학원론","dept":"경영학과"},
      {"code":"251.209","name":"조직행위론","dept":"경영학과"},
      {"code":"251.205","name":"회계원리","dept":"경영학과"},
      {"code":"251.320","name":"생산관리","dept":"경영학과"},
      {"code":"251.301","name":"재무관리","dept":"경영학과"},
      {"code":"251.423","name":"노사관계론","dept":"경영학과"},
      {"code":"251.401","name":"회계감사","dept":"경영학과"},
      {"code":"273.201","name":"헌법1","dept":"법학부"},
      {"code":"273.202","name":"헌법2","dept":"법학부"},
      {"code":"273.101","name":"민법총칙","dept":"법학부"},
      {"code":"273.205","name":"상법총론","dept":"법학부"},
      {"code":"273.306","name":"행정법1","dept":"법학부"},
      {"code":"273.307","name":"행정법2","dept":"법학부"},
      {"code":"273.208","name":"국제법1","dept":"법학부"},
      {"code":"273.209","name":"국제법2","dept":"법학부"},
      {"code":"273.310","name":"노동법1","dept":"법학부"},
      {"code":"273.317","name":"노동법2","dept":"법학부"},
      {"code":"273.001","name":"행정학","dept":"법학부"},
      {"code":"273.420A","name":"법경제학","dept":"법학부"},
      {"code":"326.211","name":"확률의 개념 및 응용","dept":"통계학과"},
      {"code":"326.311","name":"수리통계1","dept":"통계학과"},
      {"code":"326.313","name":"회귀분석 및 실습","dept":"통계학과"},
      {"code":"326.415","name":"시계열분석 및 실습","dept":"통계학과"},
      {"code":"881.003","name":"미분방정식","dept":"수리과학부"},
      {"code":"881.007","name":"선형대수학","dept":"수리과학부"},
      {"code":"881.008","name":"해석개론","dept":"수리과학부"},
      {"code":"3341.201","name":"해석개론1","dept":"수리과학부"},
      {"code":"3341.202","name":"해석개론2","dept":"수리과학부"},
      {"code":"300.204","name":"미분방정식 및 연습","dept":"수리과학부"},
      {"code":"300.203A","name":"선형대수학1","dept":"수리과학부"},
      {"code":"881.301","name":"현대대수학1","dept":"수리과학부"},
      {"code":"881.302","name":"현대대수학2","dept":"수리과학부"},
      {"code":"881.401","name":"위상수학개론1","dept":"수리과학부"},
      {"code":"881.402","name":"위상수학개론2","dept":"수리과학부"},
      {"code":"881.425","name":"실변수함수론","dept":"수리과학부"},
      {"code":"881.320","name":"수치해석개론","dept":"수리과학부"},
      {"code":"406.401","name":"선형계획","dept":"산업공학과"},
      {"code":"M2908.000200","name":"시장과 윤리","dept":"철학과"}
    ],
    "approval_max_credits": 12
  },
```

- [ ] **Step 1: Edit `econ_2026.json`**

Replace exactly (lines 14-17):

```json
  "external_recognition": {
    "depts": ["정치외교학부", "지리학과", "경영학과", "인류학과", "사회복지학과", "언론정보학과", "법학", "수리과학부", "통계학과", "산업공학과", "철학과"],
    "approval_max_credits": 12
  },
```

with the new `external_recognition` value above.

- [ ] **Step 2: Edit `econ_2023_2024.json`**

Replace exactly (lines 17-32):

```json
  "external_recognition": {
    "depts": [
      "정치외교학부",
      "지리학과",
      "경영학과",
      "인류학과",
      "사회복지학과",
      "언론정보학과",
      "법학",
      "수리과학부",
      "통계학과",
      "산업공학과",
      "철학과"
    ],
    "approval_max_credits": 12
  },
```

with the same new `external_recognition` value above.

- [ ] **Step 3: Verify JSON validity**

Run:

```bash
python3 -m json.tool /home/toxiclemon/project/class-checker/web/data/grad_req/econ_2026.json > /dev/null && echo "econ_2026 VALID"
python3 -m json.tool /home/toxiclemon/project/class-checker/web/data/grad_req/econ_2023_2024.json > /dev/null && echo "econ_2023_2024 VALID"
```

Expected: `econ_2026 VALID` then `econ_2023_2024 VALID`. Any `JSONDecodeError` → fix the trailing comma / quote and re-run.

- [ ] **Step 4: Verify the count is 48 in both files**

Run:

```bash
python3 -c "import json; print('2026', len(json.load(open('/home/toxiclemon/project/class-checker/web/data/grad_req/econ_2026.json'))['external_recognition']['courses']))"
python3 -c "import json; d=json.load(open('/home/toxiclemon/project/class-checker/web/data/grad_req/econ_2023_2024.json')); print('2023_2024', len(d['external_recognition']['courses']), 'depts' in d['external_recognition'])"
```

Expected: `2026 48` then `2023_2024 48 False` (48 courses, `depts` key gone).

- [ ] **Step 5: Verification checkpoint (no commit)**

Both files valid, 48 courses, no `depts`. Do not commit. Proceed to Task 4.

---

## Task 4: me data — prefixes + colleges + 10 경영 entries, drop label `depts`

Replace the inert label-`depts` (4 college-label tokens that match 0 live departments → under-accepts) with `code_prefixes` + `colleges` + a 10-entry `courses` array (9 designated 경영 courses; `251.209` is an explicit old-code alias for the `M1338.004000` renumber). Keep `approval_max_credits:15`. Tracks keep `recog_max` 15/15/0/0 (unchanged).

**Files:**
- Modify: `web/data/grad_req/me_2026.json` (lines 14-17)
- Modify: `web/data/grad_req/me_2023_2024.json` (lines 17-25)

The replacement `external_recognition` value is **identical for both files**:

```json
  "external_recognition": {
    "code_prefixes": ["400.", "M2177."],
    "colleges": ["공과대학", "자연과학대학"],
    "courses": [
      {"code":"251.204A","name":"중급회계1","dept":"경영학과"},
      {"code":"251.205","name":"회계원리","dept":"경영학과"},
      {"code":"M1338.004000","name":"조직행동론","dept":"경영학과"},
      {"code":"251.209","name":"조직행위론","dept":"경영학과"},
      {"code":"M1338.004100","name":"조직이론","dept":"경영학과"},
      {"code":"251.301","name":"재무관리","dept":"경영학과"},
      {"code":"251.303","name":"인사관리","dept":"경영학과"},
      {"code":"251.321","name":"마케팅관리","dept":"경영학과"},
      {"code":"251.322","name":"국제경영","dept":"경영학과"},
      {"code":"251.332","name":"현대경영이론","dept":"경영학과"}
    ],
    "approval_max_credits": 15
  },
```

- [ ] **Step 1: Edit `me_2026.json`**

Replace exactly (lines 14-17):

```json
  "external_recognition": {
    "depts": ["공과대학 공통", "공과대학 전공", "자연과학대학 전공", "경영대학 지정과목"],
    "approval_max_credits": 15
  },
```

with the new `external_recognition` value above.

- [ ] **Step 2: Edit `me_2023_2024.json`**

Replace exactly (lines 17-25):

```json
  "external_recognition": {
    "depts": [
      "공과대학 공통",
      "공과대학 전공",
      "자연과학대학 전공",
      "경영대학 지정과목"
    ],
    "approval_max_credits": 15
  },
```

with the same new `external_recognition` value above.

- [ ] **Step 3: Verify JSON validity + shape**

Run:

```bash
python3 -m json.tool /home/toxiclemon/project/class-checker/web/data/grad_req/me_2026.json > /dev/null && echo "me_2026 VALID"
python3 -m json.tool /home/toxiclemon/project/class-checker/web/data/grad_req/me_2023_2024.json > /dev/null && echo "me_2023_2024 VALID"
python3 -c "import json; d=json.load(open('/home/toxiclemon/project/class-checker/web/data/grad_req/me_2026.json'))['external_recognition']; print(d['code_prefixes'], d['colleges'], len(d['courses']), 'depts' in d)"
```

Expected: `me_2026 VALID`, `me_2023_2024 VALID`, then `['400.', 'M2177.'] ['공과대학', '자연과학대학'] 10 False`.

- [ ] **Step 4: Reload preview, then verify the me matcher (prefix + college + cap + own-major exclusion)**

`preview_eval`:

```js
location.reload(); "reloading"
```

Then a fresh `preview_eval`:

```js
const ce = await _loadCodeEquiv();
const mk = (n,c,cls,d,col,cr=3) => ({name:n, sbjt_cd:c, credits:cr, cls, dept:d, college:col});
const track = { name:'주전공(단일전공)', general:false, major_min_credits:62, select_min:0, recog_max:15, required_credits:0 };
const spec = { major_required_match:{departments:['기계공학부']}, major_select_match:{departments:['기계공학부']},
  external_recognition:{ code_prefixes:['400.','M2177.'], colleges:['공과대학','자연과학대학'],
    courses:[{code:'251.205',name:'회계원리',dept:'경영학과'}] }, total_credits:130, suri:{seq:[],combined:null} };
const rows = [
  mk('공학수학','400.001',['전선'],'전기정보공학부','공과대학',6),     // prefix 400. → recog
  mk('일반물리학','3406.201',['전선'],'물리천문학부','자연과학대학',6),  // college 자연과학대학 + 전선 → recog
  mk('회계원리','251.205',['전선'],'경영학과','경영대학',6),          // designated course → recog
  mk('고체역학','M2794.001000',['전선'],'기계공학부','공과대학',3),    // OWN major → isStat → NOT recog
  mk('마케팅조사','251.999',['전선'],'경영학과','경영대학',3)          // non-listed 경영 → reject
];
const r = _gradAuditBlock(spec, track, rows, [], {type:'single',year:'2026'}, 0, null, {codes:{},exceptions:{}}, ce);
const txt = r.node.textContent;
const recogNote = [...r.node.querySelectorAll('.grad-note')].map(x=>x.textContent).find(t=>/반영/.test(t)) || '';
const grps = [...r.node.querySelectorAll('.gfold-grp')].map(x=>x.textContent);
return JSON.stringify({
  recogNote,                                   // capped at 15 (raw 18)
  ownInSelGroup: grps.some(g=>/전공 \(전선\)/.test(g)) && /고체역학/.test(txt),
  nonListedRejected: !/마케팅조사/.test(txt),
  capLabel: grps.find(g=>/타과 인정/.test(g)) || ''
});
```

Expected (exact): `{"recogNote":"전공선택인정(수리·컴공 등): 15학점 반영","ownInSelGroup":true,"nonListedRejected":true,"capLabel":"타과 인정 (최대 15학점, 반영 15)"}`

(Raw recog = 400.001(6) + 3406.201(6) + 251.205(6) = 18 → capped to 15. 고체역학 is own-major → counted via `majorSelRows`, not double-counted via `colleges`. 마케팅조사 251.999 matches nothing → rejected.)

- [ ] **Step 5: Verification checkpoint (no commit)**

All four assertions correct. Do not commit. Proceed to Task 5.

---

## Task 5: stat data — add `recog_max` per track

stat's `depts` recognition is correct, but the 9-credit cap was never enforced (tracks had no `recog_max`, so the matcher's `if (track.recog_max != null)` guard never fired → false pass). Add `recog_max:9` to 심화전공(단일전공)/주전공(다전공)/복수전공 and `recog_max:0` to 부전공. `external_recognition` (수리과학부·컴퓨터공학부) unchanged.

**Files:**
- Modify: `web/data/grad_req/stat_2021_2022.json` (tracks lines 30-35)
- Modify: `web/data/grad_req/stat_2025.json` (tracks lines 30-35)
- Modify: `web/data/grad_req/stat_2026.json` (tracks lines 34-39)
- Modify: `web/data/grad_req/stat_2023_2024.json` (tracks lines 84-117)

- [ ] **Step 1: Edit the three compact stat files** (`stat_2021_2022.json`, `stat_2025.json`, `stat_2026.json`)

These three files share **identical** track text. In each, replace:

```json
    { "key": "single", "name": "심화전공(단일전공)", "major_min_credits": 60, "general": true, "select_min": 5, "suri_sub": false },
    { "key": "multi", "name": "주전공(다전공)", "major_min_credits": 39, "general": true, "select_min": 5, "suri_sub": false },
    { "key": "double", "name": "복수전공", "major_min_credits": 39, "general": false, "select_min": 5, "suri_sub": false },
    { "key": "minor", "name": "부전공", "major_min_credits": 21, "general": false, "select_min": 0, "suri_sub": true }
```

with:

```json
    { "key": "single", "name": "심화전공(단일전공)", "major_min_credits": 60, "general": true, "select_min": 5, "recog_max": 9, "suri_sub": false },
    { "key": "multi", "name": "주전공(다전공)", "major_min_credits": 39, "general": true, "select_min": 5, "recog_max": 9, "suri_sub": false },
    { "key": "double", "name": "복수전공", "major_min_credits": 39, "general": false, "select_min": 5, "recog_max": 9, "suri_sub": false },
    { "key": "minor", "name": "부전공", "major_min_credits": 21, "general": false, "select_min": 0, "recog_max": 0, "suri_sub": true }
```

- [ ] **Step 2: Edit the pretty-printed `stat_2023_2024.json`**

Replace the whole tracks array (lines 84-117):

```json
  "tracks": [
    {
      "key": "single",
      "name": "심화전공(단일전공)",
      "major_min_credits": 60,
      "general": true,
      "select_min": 5,
      "suri_sub": false
    },
    {
      "key": "multi",
      "name": "주전공(다전공)",
      "major_min_credits": 39,
      "general": true,
      "select_min": 5,
      "suri_sub": false
    },
    {
      "key": "double",
      "name": "복수전공",
      "major_min_credits": 39,
      "general": false,
      "select_min": 5,
      "suri_sub": false
    },
    {
      "key": "minor",
      "name": "부전공",
      "major_min_credits": 21,
      "general": false,
      "select_min": 0,
      "suri_sub": true
    }
  ],
```

with:

```json
  "tracks": [
    {
      "key": "single",
      "name": "심화전공(단일전공)",
      "major_min_credits": 60,
      "general": true,
      "select_min": 5,
      "recog_max": 9,
      "suri_sub": false
    },
    {
      "key": "multi",
      "name": "주전공(다전공)",
      "major_min_credits": 39,
      "general": true,
      "select_min": 5,
      "recog_max": 9,
      "suri_sub": false
    },
    {
      "key": "double",
      "name": "복수전공",
      "major_min_credits": 39,
      "general": false,
      "select_min": 5,
      "recog_max": 9,
      "suri_sub": false
    },
    {
      "key": "minor",
      "name": "부전공",
      "major_min_credits": 21,
      "general": false,
      "select_min": 0,
      "recog_max": 0,
      "suri_sub": true
    }
  ],
```

- [ ] **Step 3: Verify JSON validity + recog_max values across all four files**

Run:

```bash
for f in stat_2021_2022 stat_2023_2024 stat_2025 stat_2026; do
  python3 -c "import json; d=json.load(open('/home/toxiclemon/project/class-checker/web/data/grad_req/$f.json')); print('$f', [t.get('recog_max') for t in d['tracks']])"
done
```

Expected (each line): `stat_2021_2022 [9, 9, 9, 0]`, `stat_2023_2024 [9, 9, 9, 0]`, `stat_2025 [9, 9, 9, 0]`, `stat_2026 [9, 9, 9, 0]`. (A `JSONDecodeError` means a syntax slip — fix and re-run.)

- [ ] **Step 4: Verify the stat cap binds (reload not needed — synthetic spec)**

In `preview_eval` (app.js already reloaded in Task 4; the matcher reads `track.recog_max` from the synthetic track here):

```js
const ce = await _loadCodeEquiv();
const mk = (n,c,cls,d,col,cr=3) => ({name:n, sbjt_cd:c, credits:cr, cls, dept:d, college:col});
const track = { name:'심화전공(단일전공)', general:false, major_min_credits:60, select_min:5, recog_max:9, required_credits:0 };
const spec = { major_required_match:{departments:['통계']}, major_select_match:{departments:['통계']},
  external_recognition:{ depts:['수리과학부','컴퓨터공학부'] }, total_credits:130, suri:{seq:[],combined:null} };
const rows = [ mk('해석개론','881.008',['전선'],'수리과학부','자연과학대학',6),
  mk('자료구조','M1522.000300',['전선'],'컴퓨터공학부','공과대학',6) ];   // raw 12 → cap 9
const r = _gradAuditBlock(spec, track, rows, [], {type:'single',year:'2026'}, 0, null, {codes:{},exceptions:{}}, ce);
const recogNote = [...r.node.querySelectorAll('.grad-note')].map(x=>x.textContent).find(t=>/반영/.test(t)) || '';
const capLabel = [...r.node.querySelectorAll('.gfold-grp')].map(x=>x.textContent).find(g=>/타과 인정/.test(g)) || '';
const foldRows = r.node.querySelectorAll('.grad-fold .gfold-row').length;
return JSON.stringify({ recogNote, capLabel, foldRows });
```

Expected (exact): `{"recogNote":"전공선택인정(수리·컴공 등): 9학점 반영","capLabel":"타과 인정 (최대 9학점, 반영 9)","foldRows":2}`

(Cap binds: raw 12 → 9. The foldable still lists **both** rows (`foldRows`:2) — the design intentionally shows all recognized courses while the summary/cap reflects the capped 9학점.)

- [ ] **Step 5: Verification checkpoint (no commit)**

All four files `[9,9,9,0]` and the cap binds at 9. Do not commit. Proceed to Task 6.

---

## Task 6: Final regression

Confirm every edited JSON parses, the real app audits without console errors, and cse (unchanged) still recognizes via legacy `depts`.

**Files:** none (verification only)

- [ ] **Step 1: Validate all eight edited JSON files at once**

```bash
for f in econ_2026 econ_2023_2024 me_2026 me_2023_2024 stat_2021_2022 stat_2023_2024 stat_2025 stat_2026; do
  python3 -m json.tool /home/toxiclemon/project/class-checker/web/data/grad_req/$f.json > /dev/null && echo "$f OK" || echo "$f FAIL"
done
```

Expected: eight `... OK` lines, zero `FAIL`.

- [ ] **Step 2: Confirm cse is untouched and still uses `depts`**

```bash
python3 -c "import json; d=json.load(open('/home/toxiclemon/project/class-checker/web/data/grad_req/cse_2026.json'))['external_recognition']; print('depts' in d, list(d.keys()))"
```

Expected: `True [...]` with `depts` present (cse intentionally unchanged — its tokens resolve 1:1 against live departments).

- [ ] **Step 3: Real-app smoke — reload, open 졸업요건, no console errors**

`preview_eval`:

```js
location.reload(); "reloading"
```

Then a fresh `preview_eval` (drives the real audit over whatever spec data is on disk; passing means `renderGrad` + the new matcher run end-to-end without throwing):

```js
await renderGrad();
const body = document.querySelector('#gradBody');
return JSON.stringify({
  gradBodyExists: !!body,
  rendered: (body?.textContent || '').length > 0,
  blocks: body ? body.querySelectorAll('.grad-block, .grad-note').length : 0
});
```

Expected: `gradBodyExists:true`, `rendered:true`, `blocks` ≥ 1. Then check `preview_console_logs` (or `preview_logs`) for errors — expect **no** uncaught exceptions from `_gradAuditBlock`/`renderGrad`.

- [ ] **Step 4: Final verification checkpoint (no commit)**

All JSON valid, cse intact, real audit renders cleanly. Implementation complete — leave the working tree uncommitted per the standing directive and report.

---

## Spec Coverage (self-review)

| Spec section | Task(s) |
|---|---|
| §4 unified `external_recognition` model | 3 (econ), 4 (me), 5 (stat) |
| §5.1 thread `college` into rows | 1 (Step 2) |
| §5.2 `isRecog(row)` 4-mechanism + own-major exclusion | 1 (Step 3) |
| §5.3 `recogRows` / `recogCr` | 1 (Step 4) |
| §6.1 econ 48-course allowlist, drop `depts` | 3 |
| §6.2 me prefixes + colleges + 10 경영 entries | 4 |
| §6.3 stat `recog_max` 9/0 | 5 |
| §6.4 cse unchanged | 6 (Step 2 confirms) |
| §7 foldable `<details>` + CSS | 2 |
| §9 edge cases (own-major overlap, cap binding, renumber alias) | verified in 4 (Step 4), 5 (Step 4); alias present in 4 data |
