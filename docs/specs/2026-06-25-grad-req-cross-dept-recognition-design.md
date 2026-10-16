# 졸업요건 cross-dept 전공선택 recognition rework + foldable list — design

Date: 2026-06-25
Area: `web/` (static SNU Class Checker app — `app.js` `_gradAuditBlock`, `web/data/grad_req/*.json`, `styles.css`)

## 1. Goal

Two coupled changes to the graduation-requirements (졸업요건) audit:

1. **Foldable 전공선택 list.** Under each major block's 전공선택 bars, a collapsed
   field that lists exactly which courses were counted as 전공선택 — own-major
   electives (전선) and externally-recognized courses (타과 인정) — each with its
   offering 학과 visible, so a mis-recognized course is obvious at a glance.
2. **Fix cross-dept recognition over/under-acceptance.** Today every major's
   "courses from other units count toward my 전공선택" rule is forced onto a single
   `external_recognition.depts` substring match. That mechanism only fits rules
   shaped as "all courses from department X." It fails for majors whose real rule is
   a curated **course list** (경제학부 — over-accepts) or **code-prefix / college
   membership** (기계공학부 — the college-label tokens match nothing, so the rule is
   inert and under-accepts). Replace the single mechanism with a small set of
   purpose-fit fields and migrate each major to the correct one.

## 2. Current state (what exists today)

- `_gradAuditBlock` (`app.js:2410`) classifies each taken course row
  `{name, sbjt_cd, credits, cls, dept}` and tallies 전공 credit.
- Own-major split:
  - `isStat = (d) => (spec.major_required_match?.departments||[]).some(x => (d||"").includes(x))`
    (`app.js:2415`) — "is this course from my own major's department(s)."
  - `majorSelRows = rows.filter(r => isStat(r.dept) && hasCls(r,"전선"))` (`app.js:2435`).
  - `majorReqCr` = own-major 전필 credit (`app.js:2436`).
- Cross-dept recognition:
  - `isRecog = (d) => (spec.external_recognition?.depts||[]).some(x => (d||"").includes(x.replace(/부$/,"")))`
    (`app.js:2416`) — strips a trailing "부" and substring-matches the course's dept.
  - `recogCr = rows.filter(r => isRecog(r.dept) && (hasCls(r,"전선")||hasCls(r,"전필"))).reduce(...)`
    then `if (track.recog_max != null) recogCr = Math.min(recogCr, track.recog_max)`
    (`app.js:2437-2438`).
  - `selectCredits = majorSelRows credits + recogCr` (`app.js:2497`); 전공선택 bars at
    `app.js:2556-2558`.
- `approval_max_credits` is **never read** in code — documentation only. The real cap
  is `track.recog_max`.

**Audit findings (from review agents, 2026-06-25):**

| major | files | today | shape of real rule | effect |
|---|---|---|---|---|
| 경제학부 | `econ_2026`, `econ_2023_2024` | 11 whole depts, max 12 | curated **48-course** list (PDF: "코드+명 완전일치") | false **pass** — over-accepts any course from those depts |
| 통계학과 | `stat_2021_2022/2023_2024/2025/2026` | `수리과학부·컴퓨터공학부`, `recog_max:null` | whole-dept (correct) **but** 9-credit cap | false **pass** — cap never binds (recog_max null) |
| 기계공학부 | `me_2026`, `me_2023_2024` | 4 college-label tokens, `recog_max` 15/15/0/0 | `400.*`/`M2177.*` prefixes + 공대/자연대 college 전공 + 경영대 designated list, max 15 | false **fail** — tokens match 0 live depts → inert rule, recogCr always 0 |
| 컴퓨터공학부 | `cse_2026`, `cse_2023_2024` | `전기/정보공학/수리과학/통계/인공지능`, max 12 | whole-dept (correct) | none today (tokens resolve 1:1) |

## 3. Decisions

1. **Unified, purpose-fit `external_recognition` model** (§4) replacing the single
   `depts` mechanism. Each major sets only the field(s) its real rule needs; the
   matcher ORs whatever is present.
2. **Match designated courses by canonical code, not by Korean name.** The
   institutions' human-facing rule is "code+name exact match"; in code we match
   `canon(sbjt_cd)` (codes are unique and renumber-stable via `code_equiv.json`).
   Enforcing byte-exact Korean names would cause false fails on spacing/normalization
   drift. Names are stored for display only.
3. **Recognition excludes own-major courses** (`!isStat(dept)`). Required because
   college-membership (기계) would otherwise re-count 기계's own 공대 courses that the
   `isStat` path already counts.
4. **Fix all four findings in one change** (econ, me, stat) + leave cse untouched
   (no current defect; changing its tokens risks regression).
5. **Authoritative-list-only for designated courses.** Where a source PDF has a
   discrepancy (me page-1 footnote vs page-6 table), use the authoritative/newer table
   and document the discrepancy. Bias toward *not* over-accepting.
6. **No code change for caps.** Keep relying on `track.recog_max` (already read).
   `approval_max_credits` stays documentation-only. stat is fixed purely by setting
   `recog_max`.

## 4. Data model: `external_recognition`

All fields optional; the matcher ORs whatever is present on a spec.

```
external_recognition: {
  courses:       [ {code, name, dept} ],   // exact canon(code) allowlist
  code_prefixes: [ "400.", "M2177." ],     // canon(code) startsWith
  colleges:      [ "공과대학", "자연과학대학" ], // course.college ∈ list
  depts:         [ "수리과학부", "컴퓨터공학부" ], // legacy dept substring
  approval_max_credits: 12                  // documentation only (cap = track.recog_max)
}
```

Per-major assignment after migration:

| major | fields set |
|---|---|
| econ | `courses` (48), `approval_max_credits:12` — **`depts` removed** |
| me | `code_prefixes:["400.","M2177."]`, `colleges:["공과대학","자연과학대학"]`, `courses` (9 경영), `approval_max_credits:15` — **college-label `depts` removed** |
| stat | `depts` unchanged; **add `recog_max:9`** to tracks (§6.3) |
| cse | unchanged |

## 5. Matcher rewrite (`_gradAuditBlock`)

### 5.1 Thread `college` into rows

In the `rows = taken.map(...)` block (`app.js:2388-2392`), add `college`:

```js
return { name: c.name, sbjt_cd: c.sbjt_cd,
  credits: Number(m.credits ?? c.credits ?? 0) || 0,
  cls: m.classification || c.classification || [],
  dept: m.department || c.dept || c.department || "",
  college: m.college || c.college || "" };
```

(`m` is the live-catalog record from `lookupLocal`, which carries `college`; manual
rows simply have `""` and never match college-mode.)

### 5.2 `isRecog(row)` — replaces `app.js:2416`

```js
const er = spec.external_recognition || {};
const recogCodes  = new Set((er.courses || []).map((c) => canon(c.code)));
const recogPrefix = er.code_prefixes || [];
const recogColl   = er.colleges || [];
const recogDepts  = er.depts || [];
const isRecog = (r) => {
  if (isStat(r.dept)) return false;                       // own major handled by isStat path
  const code = canon(r.sbjt_cd);
  if (recogCodes.has(code)) return true;                  // designated course (any classification)
  if (recogPrefix.some((p) => code.startsWith(p))) return true; // 공대공통 prefix (any classification)
  const isMajorCourse = hasCls(r, "전선") || hasCls(r, "전필");
  if (recogColl.includes(r.college) && isMajorCourse) return true;   // college 전공 course
  if (recogDepts.some((x) => (r.dept || "").includes(x.replace(/부$/, ""))) && isMajorCourse) return true;
  return false;
};
```

Classification rule by mechanism: **courses** and **code_prefixes** count regardless
of classification (they name exact courses / an exact code family). **colleges** and
**depts** require 전선/전필 (recognize a *major* course from that unit).

### 5.3 `recogRows` / `recogCr` — replaces `app.js:2437-2438`

```js
const recogRows = rows.filter((r) => isRecog(r));
let recogCr = recogRows.reduce((s, r) => s + r.credits, 0);
if (track.recog_max != null) recogCr = Math.min(recogCr, track.recog_max);
```

`recogRows` is reused by the foldable field (§7). Downstream `majorCr`, `selectCredits`
(`app.js:2440,2497`) are unchanged in formula.

## 6. Per-file data changes

### 6.1 econ — `courses` (48), drop `depts`

`econ_2026.json` and `econ_2023_2024.json` (identical): set
`external_recognition.courses` to the 48 designated courses below and remove `depts`.
Keep `approval_max_credits:12`. Source: `경제학부 졸업이수 규정_20260301.pdf` §3
"전공선택 인정 교과목."

- 정치외교학부: `216A.210` 행정학서론, `216A.304` 한국정치론, `216B.302` 국제정치이론, `216B.223` 국제정치경제론
- 지리학과: `208.205` 경제지리학, `208.401A` 법제지리학
- 인류학과: `206.331` 문화와 경제
- 사회복지학과: `209.232` 복지국가원론, `209.304` 사회복지정책
- 언론정보학과: `211.227A` 커뮤니케이션,문명,사회변동
- 경영학과: `251.101` 경영학원론, `251.209` 조직행위론, `251.205` 회계원리, `251.320` 생산관리, `251.301` 재무관리, `251.423` 노사관계론, `251.401` 회계감사
- 법학부: `273.201` 헌법1, `273.202` 헌법2, `273.101` 민법총칙, `273.205` 상법총론, `273.306` 행정법1, `273.307` 행정법2, `273.208` 국제법1, `273.209` 국제법2, `273.310` 노동법1, `273.317` 노동법2, `273.001` 행정학, `273.420A` 법경제학
- 통계학과: `326.211` 확률의 개념 및 응용, `326.311` 수리통계1, `326.313` 회귀분석 및 실습, `326.415` 시계열분석 및 실습
- 수리과학부: `881.003` 미분방정식, `881.007` 선형대수학, `881.008` 해석개론, `3341.201` 해석개론1, `3341.202` 해석개론2, `300.204` 미분방정식 및 연습, `300.203A` 선형대수학1, `881.301` 현대대수학1, `881.302` 현대대수학2, `881.401` 위상수학개론1, `881.402` 위상수학개론2, `881.425` 실변수함수론, `881.320` 수치해석개론
- 산업공학과: `406.401` 선형계획
- 철학과: `M2908.000200` 시장과 윤리

Each entry stored as `{code, name, dept}` (dept = the offering dept label above).

### 6.2 me — `code_prefixes` + `colleges` + `courses` (9 경영), drop label `depts`

`me_2026.json` and `me_2023_2024.json` (identical): set

```
external_recognition: {
  code_prefixes: ["400.", "M2177."],
  colleges: ["공과대학", "자연과학대학"],
  courses: [ 9 경영대 designated courses (10 entries — 251.209 is a renumber alias), below ],
  approval_max_credits: 15
}
```

remove the old `depts` (공과대학 공통 / … labels). Source:
`SNU_ME_학부졸업이수규정_2026공지용260204.pdf` page-6 table "전공선택 인정 경영대학 과목":

- `251.204A` 중급회계1, `251.205` 회계원리, `M1338.004000` 조직행동론, `251.209` 조직행위론(구코드 alias of 조직행동론), `M1338.004100` 조직이론, `251.301` 재무관리, `251.303` 인사관리, `251.321` 마케팅관리, `251.322` 국제경영, `251.332` 현대경영이론

`251.209` included as an explicit alias for the `M1338.004000` renumber (defensive, in
case `code_equiv.json` lacks the mapping). Page-1 footnote extras (`251.101`, `251.215`)
are **excluded** — older pre-renumber list; including them would over-accept. `recog_max`
15/15/0/0 unchanged (복수/부전공 0 is our existing modeling assumption; the PDF caps at 15
without a track split).

### 6.3 stat — enforce the 9-credit cap

`stat_2021_2022.json`, `stat_2023_2024.json`, `stat_2025.json`, `stat_2026.json`:
set `recog_max:9` on tracks 심화전공(단일전공), 주전공(다전공), 복수전공; `recog_max:0`
on 부전공. `depts` (`수리과학부`,`컴퓨터공학부`) unchanged — dept recognition is correct;
only the cap was missing.

### 6.4 cse — unchanged

No code/data change. Documented as correct (tokens resolve 1:1 against live depts).

## 7. Foldable 전공선택 field (`_gradAuditBlock`, 전공 section)

After the 전공선택 bars (`app.js:2556-2558`), before `sections.push(major)`:

```js
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
```

- Collapsed by default (native `<details>`).
- Offering 학과 shown on every recognized row → mis-recognition visible.
- Rendered only when ≥1 counted course exists.

### CSS (`styles.css`, near the other grad-* rules)

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

## 8. Code-change map

| Concern | Location |
|---|---|
| thread `college` into rows | `app.js:2388-2392` |
| `isRecog(row)` 4-mechanism rewrite + own-major exclusion | `app.js:2416` |
| `recogRows` / `recogCr` | `app.js:2437-2438` |
| foldable `<details>` | after `app.js:2558` |
| econ `courses` (48), drop `depts` | `econ_2026.json`, `econ_2023_2024.json` |
| me `code_prefixes`+`colleges`+`courses`(9), drop label `depts` | `me_2026.json`, `me_2023_2024.json` |
| stat `recog_max:9`/`0` per track | `stat_2021_2022/2023_2024/2025/2026.json` |
| foldable CSS | `styles.css` |

## 9. Edge cases

- **Own-major college overlap (me):** `isRecog` returns false for `isStat(dept)` rows, so
  기계공학부's own 공대 courses are not double-counted via `colleges`.
- **Renumbered code (251.209 → M1338.004000):** ensure `code_equiv.json` maps the old code
  to the new canonical, **or** the allowlist lists both. econ lists `251.209`; me lists both
  `251.209` and `M1338.004000`. Implementation verifies `canon()` resolves a taken course to
  a listed code.
- **Manual rows** (no catalog match): `college` = `""`, never match college-mode; designated
  courses still match by code if the manual row carries the right `sbjt_cd`.
- **400.013 기계공학개론:** the PDF excludes it from the 공대공통 *필수* requirement, not from
  전공선택 recognition; prefix `400.` recognizes it as 전선. Treated as in-scope of `400.*`
  (no special-case) — documented simplification.
- **부전공/복수 caps:** driven by `track.recog_max` (econ 0/12, me 0/15, stat 0/9); unchanged
  logic.
- **cap with `recog_max` null:** still possible for majors that legitimately want no cap; the
  `!= null` guard is preserved.

## 10. Out of scope (YAGNI)

- econ 중복인정 cap ("2026.3.1~ 공통 전공교과목 최대 3개").
- me 공대/자연대 "학부장 승인" gating (we recognize all eligible — upper bound, like existing
  근사 notes).
- me page-1 footnote extras (`251.101`, `251.215`).
- cse token anchoring (no current defect).
- Making the audit code read `approval_max_credits`.
- Byte-exact Korean-name matching for designated courses.

## 11. Verification notes

Preview tab is hidden (per memory) — use `preview_eval` against live globals, not
screenshots. Manual checks:

- Seed a timetable with: an own-major 전선; an econ-designated course (e.g. `251.205` for an
  econ block); a NON-designated course from a listed dept (e.g. some other 경영 course) →
  designated counts, non-designated does **not** (over-acceptance fixed).
- Add a 컴퓨터공학부 course to an econ block → never counts (absent from `courses`).
- me block: add a `400.*` course and a 자연과학대학 전공 course → both recognized; add a
  경영 course not in the 9-list → not recognized; verify recogCr caps at 15.
- me block: add a 기계공학부 (own) 공대 course → counted once via own-major path, not
  double-counted via `colleges`.
- stat block: add >9학점 of 수리/컴공 courses → recogCr caps at 9.
- Foldable: open 전공선택 details → 전선 group + 타과 인정 group, each row shows 학과; summary
  count + 학점 match the bars.
- Reload → data-only spec edits persist (they're static JSON); no console errors.
