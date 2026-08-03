# 졸업요건 cross-dept recognition — omission fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **NO git commits** — working-tree only (project is not a git repo).

**Goal:** Encode confirmed cross-department 전공선택 recognition rules for 5 majors that currently have no `external_recognition` block and therefore under-count 전선 credit.

**Architecture:** Extend the existing OR-matcher `isRecog` in `app.js` `_gradAuditBlock` with one new mechanism — `any_dept: true` (recognize any *other* department's 전선/전필, capped by `track.recog_max`). Then add a data-only `external_recognition` block + per-track `recog_max` to 12 spec JSON files. socwelf is college-scoped (fits existing `colleges` mechanism, no wildcard needed); geoedu2/psir/comm/socio use the new `any_dept` wildcard.

**Tech Stack:** Vanilla browser JS (no bundler/test runner), static JSON specs. Verify via `python3 -m json.tool` (JSON validity) + `preview_eval` harnesses calling the real `_gradAuditBlock` (no screenshots — preview tab hidden per project memory). Reload (`location.reload()`) after `app.js` edits before the harness sees new code; JSON-only edits need no reload (harnesses build synthetic specs).

---

## Background — what each rule is and where it came from

All rules are quoted from the project's own scrape notes (`web/data/grad_req/_scrape/soc.md`, `_scrape/edu.md`), which digest SNU dept pages/PDFs. Verified directly this session.

| major | spec files (catalog years) | rule (source) | own-dept token |
|---|---|---|---|
| 사회복지 socwelf | `socwelf_2026`, `socwelf_2024`, `socwelf_2023` | 사회과학대학 개설 모든 전공과목(전필/전선) **≤9학점**, 복수전공 사회대 내 타과 9 중복인정, S/U 제외 (soc.md L75) | 사회복지학과 |
| 지리교육 geoedu2 | `geoedu2_2026`, `geoedu2_2023_2024` | 타학과 전공 교과목 **≤9학점** 주전공 전공선택 인정 (edu.md L92) | 지리교육과 |
| 정치외교 psir | `psir_2026`, `psir_2023_2024` | 타과 전선 인정 정외 ≥39학점 시 **9** / <39 시 6 (soc.md L23) | 정치외교학부 |
| 언론정보 comm | `comm_2026`, `comm_2025`, `comm_2023_2024` | 타과 전선 ≤9(주/복수), 심화 시 **≤18**(정보문화 제외 타과 ≤12), 부전공 0 (soc.md L86) | 언론정보학과 |
| 사회학 socio | `socio_2026`, `socio_2023_2024` | 복수전공 과목연계 확인 시 **≤9학점** 중복인정 (soc.md L45) | 사회학과 |

**Modeling approximations (documented, bias toward NOT over-accepting):**
- S/U-grade exclusion (socwelf): the audit has no grade-type signal → unmodeled (upper bound).
- psir conditional 6-vs-9: modeled as flat 9 (upper bound; <39학점 students slightly over-credited).
- comm 심화 카브아웃 (정보문화 ≤12 within the 18): modeled as flat 18 cap (upper bound).
- socio is 복수전공-중복인정 semantics, not general recognition → encoded as 복수전공-only (caps 0 on all other tracks). Flagged for later review.

**Live-catalog facts (verified):** college strings are exactly `사회과학대학` and `사범대학`. `track.recog_max` is a per-track scalar field (currently absent on all 12 files); `_gradAuditBlock` caps via `if (track.recog_max != null) recogCr = Math.min(recogCr, track.recog_max)` (`app.js:2454`). `approval_max_credits` is documentation-only (never read by code) — set it to the headline cap for each major.

**Per-track `recog_max` to write** (track order is the spec's existing `tracks[]` order):

| major | track[0] | track[1] | track[2] 복수전공 | track[3] 부전공 | mechanism | approval_max_credits |
|---|---|---|---|---|---|---|
| socwelf | 심화 **9** | 주전공 **9** | **9** | **0** | `colleges:["사회과학대학"]` | 9 |
| geoedu2 | 주전공단일 **9** | 주전공병행 **9** | **0** | **0** | `any_dept:true` | 9 |
| psir | 심화 **9** | 주전공 **9** | **9** | **0** | `any_dept:true` | 9 |
| comm | 심화 **18** | 주전공 **9** | **9** | **0** | `any_dept:true` | 18 |
| socio | 심화 **0** | 주전공 **0** | **9** | **0** | `any_dept:true` | 9 |

(geoedu2 `tracks` are named 주전공(단일전공)/주전공(다전공 병행)/복수전공/부전공; the rule grants recognition to 주전공 only, so indices 0,1 = 9 and 2,3 = 0.)

---

## File Structure

- **Modify (code):** `web/app.js` — `_gradAuditBlock` `isRecog` (one new mechanism).
- **Modify (data):** 12 JSON specs listed above — add `external_recognition` block + per-track `recog_max`.
- No new files. No CSS change (foldable list already renders recogRows).

---

### Task 1: Add `any_dept` mechanism to `isRecog`

**Files:**
- Modify: `web/app.js:2418-2432` (the `isRecog` closure inside `_gradAuditBlock`)

**Context:** `isRecog(r)` already ORs four mechanisms (exact-code allowlist, code-prefix, college, legacy-dept). Add a fifth: when `external_recognition.any_dept === true`, recognize ANY non-own-department 전선/전필 course. `isStat(r.dept)` already excludes the own major (line 2424), so this never double-counts own courses; `track.recog_max` bounds the credit (line 2454). It is gated on `isMajorCourse` (전선/전필) so it never grabs 교양/일반선택.

- [ ] **Step 1: Add the `recogAnyDept` flag**

In `app.js`, immediately after line 2422 (`  const recogDepts = er.depts || [];`), add:

```js
  const recogAnyDept = er.any_dept === true;                          // 타과 전선/전필 전부 인정(상한은 track.recog_max)
```

- [ ] **Step 2: Add the wildcard branch inside `isRecog`**

In the same closure, immediately after line 2428 (`    const isMajorCourse = hasCls(r, "전선") || hasCls(r, "전필");`), add:

```js
    if (recogAnyDept && isMajorCourse) return true;                   // any other dept's 전공 course
```

The closure now reads (for reference):

```js
  const isRecog = (r) => {
    if (isStat(r.dept)) return false;
    const code = canon(r.sbjt_cd);
    if (recogCodes.has(code)) return true;
    if (recogPrefix.some((p) => code.startsWith(p))) return true;
    const isMajorCourse = hasCls(r, "전선") || hasCls(r, "전필");
    if (recogAnyDept && isMajorCourse) return true;
    if (recogColl.includes(r.college) && isMajorCourse) return true;
    if (recogDepts.some((x) => (r.dept || "").includes(x.replace(/부$/, ""))) && isMajorCourse) return true;
    return false;
  };
```

- [ ] **Step 3: Reload so the harness sees new code**

`preview_eval`: `window.location.reload()` (then wait for app globals via the project's flush/ready pattern — do NOT await rAF/screenshots).

- [ ] **Step 4: Verify wildcard recognizes any other dept, excludes own + non-major, respects cap**

`preview_eval` harness (assert on the returned node's `textContent`, which contains `전공선택인정: N학점 반영`):

```js
const spec = { major_required_match: { departments: ["지리교육과"] },
  external_recognition: { any_dept: true, approval_max_credits: 9 },
  tracks: [] };
const track = { recog_max: 9 };
const rows = [
  { name: "지리교육론", sbjt_cd: "X.own",  credits: 3, cls: ["전필"], dept: "지리교육과",   college: "사범대학" },     // own → NOT recog
  { name: "타과전선A",  sbjt_cd: "208.205", credits: 3, cls: ["전선"], dept: "지리학과",     college: "사회과학대학" }, // other dept → recog
  { name: "타과전필B",  sbjt_cd: "400.001", credits: 3, cls: ["전필"], dept: "기계공학부",   college: "공과대학" },     // other dept → recog (any college)
  { name: "교양C",      sbjt_cd: "999.000", credits: 3, cls: ["교양"], dept: "철학과",       college: "인문대학" },     // not 전선/전필 → NOT recog
  { name: "타과전선D",  sbjt_cd: "211.227", credits: 3, cls: ["전선"], dept: "언론정보학과", college: "사회과학대학" }, // 4th major course (would be 12) → capped at 9
];
const node = _gradAuditBlock(spec, track, rows, [], {}, 0, {}, {}, {}).node;
const txt = node.textContent;
return { ok: txt.includes("전공선택인정: 9학점"), capWorked: txt.includes("전공선택인정: 9학점"), txt };
```

Expected: `ok: true` — three 전선/전필 타과 courses = 9학점 raw, 4th would push to 12 but `recog_max:9` caps it; own course and 교양 excluded.

- [ ] **Step 5: Verify `any_dept` absent ⇒ no behavior change (regression guard for existing specs)**

`preview_eval`:

```js
const spec = { major_required_match: { departments: ["통계학과"] },
  external_recognition: { depts: ["수리과학부", "컴퓨터공학부"] },   // legacy mechanism, no any_dept
  tracks: [] };
const rows = [
  { name: "타과전선", sbjt_cd: "100.1", credits: 3, cls: ["전선"], dept: "심리학과", college: "사회과학대학" }, // not in depts → NOT recog
  { name: "수리전선", sbjt_cd: "881.1", credits: 3, cls: ["전선"], dept: "수리과학부", college: "자연과학대학" }, // in depts → recog
];
const node = _gradAuditBlock(spec, { recog_max: 9 }, rows, [], {}, 0, {}, {}, {}).node;
return { ok: node.textContent.includes("전공선택인정: 3학점"), txt: node.textContent };
```

Expected: `ok: true` — only the `수리과학부` course (3학점) recognized; the unrelated 심리학과 course is NOT (any_dept is off). Confirms the new flag is inert when absent.

---

### Task 2: Encode `external_recognition` + `recog_max` in 12 spec files

**Files (modify):**
- `web/data/grad_req/socwelf_2026.json`, `socwelf_2024.json`, `socwelf_2023.json`
- `web/data/grad_req/geoedu2_2026.json`, `geoedu2_2023_2024.json`
- `web/data/grad_req/psir_2026.json`, `psir_2023_2024.json`
- `web/data/grad_req/comm_2026.json`, `comm_2025.json`, `comm_2023_2024.json`
- `web/data/grad_req/socio_2026.json`, `socio_2023_2024.json`

**Context:** Each file is a pretty-printed JSON object with a top-level `tracks` array (4 entries, order verified identical within each major) and other top-level keys (`major_required_match`, etc.). Add one top-level `external_recognition` object, and add a `recog_max` field to each of the 4 track objects per the table below. Do NOT reorder or remove existing keys. Use `python3 -m json.tool` after each file to confirm validity.

**`external_recognition` block to add (top-level) per major:**

```jsonc
// socwelf_2026, socwelf_2024, socwelf_2023
"external_recognition": { "colleges": ["사회과학대학"], "approval_max_credits": 9 }

// geoedu2_2026, geoedu2_2023_2024
"external_recognition": { "any_dept": true, "approval_max_credits": 9 }

// psir_2026, psir_2023_2024
"external_recognition": { "any_dept": true, "approval_max_credits": 9 }

// comm_2026, comm_2025, comm_2023_2024
"external_recognition": { "any_dept": true, "approval_max_credits": 18 }

// socio_2026, socio_2023_2024
"external_recognition": { "any_dept": true, "approval_max_credits": 9 }
```

**`recog_max` per track** (add `"recog_max": N` into each track object; track index = existing order):

| major | track[0] | track[1] | track[2] | track[3] |
|---|---|---|---|---|
| socwelf (심화/주전공/복수/부전공) | 9 | 9 | 9 | 0 |
| geoedu2 (주전공단일/주전공병행/복수/부전공) | 9 | 9 | 0 | 0 |
| psir (심화/주전공/복수/부전공) | 9 | 9 | 9 | 0 |
| comm (심화/주전공/복수/부전공) | 18 | 9 | 9 | 0 |
| socio (심화/주전공/복수/부전공) | 0 | 0 | 9 | 0 |

- [ ] **Step 1: Edit all 12 files** — add the `external_recognition` block + the 4 `recog_max` values per the tables. Match each file's existing indentation.

- [ ] **Step 2: Validate JSON**

Run: `cd web/data/grad_req && for f in socwelf_2026 socwelf_2024 socwelf_2023 geoedu2_2026 geoedu2_2023_2024 psir_2026 psir_2023_2024 comm_2026 comm_2025 comm_2023_2024 socio_2026 socio_2023_2024; do python3 -m json.tool "$f.json" >/dev/null && echo "$f OK" || echo "$f FAIL"; done`
Expected: 12 × `OK`.

- [ ] **Step 3: Confirm fields landed correctly**

Run: `cd web/data/grad_req && for f in socwelf_2026 geoedu2_2026 psir_2026 comm_2026 socio_2026; do python3 -c "import json;d=json.load(open('$f.json'));print('$f',d['external_recognition'],[t.get('recog_max') for t in d['tracks']])"; done`
Expected:
```
socwelf_2026 {'colleges': ['사회과학대학'], 'approval_max_credits': 9} [9, 9, 9, 0]
geoedu2_2026 {'any_dept': True, 'approval_max_credits': 9} [9, 9, 0, 0]
psir_2026 {'any_dept': True, 'approval_max_credits': 9} [9, 9, 9, 0]
comm_2026 {'any_dept': True, 'approval_max_credits': 18} [18, 9, 9, 0]
socio_2026 {'any_dept': True, 'approval_max_credits': 9} [0, 0, 9, 0]
```
(Repeat-spot-check the older-year files match their 2026 sibling.)

- [ ] **Step 4: End-to-end load + audit via real on-disk specs**

After Task 1's reload, `preview_eval` harness that loads a real spec through the app's own loader and audits a synthetic transcript. Use `_loadGradSpec` (async) for one socwelf year and one any_dept year (e.g. geoedu2), then call `renderGrad`-path or `_gradAuditBlock` directly:

```js
const codeEquiv = await _loadCodeEquiv();
const spec = await _loadGradSpec("socwelf_2026");          // real file
const track = spec.tracks[1];                               // 주전공(다전공), recog_max 9
const rows = [
  { name: "사복전필", sbjt_cd: "209.219", credits: 3, cls: ["전필"], dept: "사회복지학과", college: "사회과학대학" }, // own
  { name: "사회대전선", sbjt_cd: "211.227", credits: 3, cls: ["전선"], dept: "언론정보학과", college: "사회과학대학" }, // recog (사회대)
  { name: "공대전선",  sbjt_cd: "400.001", credits: 3, cls: ["전선"], dept: "기계공학부",   college: "공과대학" },     // NOT recog (socwelf college-scoped)
];
const node = _gradAuditBlock(spec, track, rows, track.required || [], {}, 0, {}, {}, codeEquiv).node;
return { socwelf: node.textContent.includes("전공선택인정: 3학점"), txt: node.textContent.slice(0, 400) };
```

Expected: `socwelf: true` — only the 사회대 other-dept course recognized (3학점); the 공대 course excluded (proves college-scoping, not wildcard). For geoedu2, swap to a real geoedu2 spec + any_dept and assert the 공대 course IS recognized.

- [ ] **Step 5: No console errors**

`preview_eval`: drive `renderGrad()` once (or open 졸업요건) and confirm zero thrown errors / clean console for these majors.

---

## Verification (whole change)

- All 12 JSON valid (`json.tool`).
- Task 1 harness: wildcard recognizes any other-dept 전선/전필, excludes own + 교양, caps at `recog_max`.
- Regression harness: `any_dept` absent ⇒ existing econ/me/stat/cse behavior unchanged.
- socwelf college-scoped (공대 course excluded); geoedu2/psir/comm/socio wildcard (공대 course included).
- Foldable 전공선택 list shows the recognized 타과 rows with their 학과 (already-built UI; no change needed).
- Reload-persistence: edits are static JSON / app.js — survive reload.

## Out of scope (YAGNI)

- **psych** — rule is a designated *course list* held in a PDF (`타과인정 2023ver`) not present in the scrape notes; cannot encode without the list. Parked.
- **ace** — 학부내 ≤9 + 타과 ≤9 *전공주임 승인* (approval-gated, dual-cap, 첨단융합 special structure). Parked.
- **bioedu2** — "서울대+협정대 개설 과목 학과장 사전승인" (unbounded + discretionary). Parked.
- **math / cse** — need authoritative source confirmation (Agent B moderate confidence). Parked.
- Modeling S/U exclusion, psir 6-vs-9 conditional, comm 정보문화 ≤12 sub-carve — all upper-bound approximations, documented above.
- Making the audit read `approval_max_credits` (stays documentation-only).
