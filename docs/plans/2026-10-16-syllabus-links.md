# Syllabus links implementation plan

**Goal:** add two fields to the class detail drawer:
- a 원본 link that opens sugang's syllabus page through a Cloudflare Worker;
- 국문 / 영문 attachment downloads, from a daily-collected attachment list.

**Approach:**
- A new `scraper/syllabus_attach.py`, modeled on `catalog_snapshot.py`, reads
  every target class's `cc103ajax` on a GitHub runner. It stores one zlib-JSON
  row per term in the cloud.
- The local `update.sh` pulls those rows. `export_json` writes
  `web/data/syllabus/<year>_<term>.json`, which the drawer loads on first open.
- A small Worker relays sugang's page for the 원본 link.

**Tech stack:**
- Python 3 (`uv`), `requests`, `libsql`, `zlib`
- vanilla JS
- Cloudflare Workers (module syntax)
- Node 24 `node:test`
- `wrangler@4.147.0` through `npx`

**Spec:** `docs/specs/2026-10-16-syllabus-links-design.md` (approved, governance
`class-checker` D-000026)

## Global Constraints

- **Rate:** sugang at most 10 requests/s. Requests are sequential, with a 0.1 s
  minimum interval.
- **Request shape:** `POST https://sugang.snu.ac.kr/sugang/cc/cc103ajax.action`
  - form fields: `openSchyy, openShtmFg, openDetaShtmFg, sbjtCd, ltNo, sbjtSubhCd`
  - header: `Referer: https://sugang.snu.ac.kr/`
  - no cookies, no browser
- **What is kept:** only `LISTTAB03_LIST_ATTACH` entries with a non-empty
  `korFileNm` / `engFileNm`. Syllabus body text is never stored.
- **Cloud and local table:** `syllabus_attach_snapshots(year, term, digest,
  classes, missing, collected_at, checked_at, payload, PRIMARY KEY(year, term))`.
- **Payload:** zlib of
  `{"year","term","collected_at","classes":{"sbjt|lt|subh":{"ko":[[name,no,deta]],"en":[...]}},"missing":[keys]}`.
  - `{}` means no attachment.
  - Languages with no file are omitted.
  - Keys are sorted.
- **Web file:** `web/data/syllabus/<year>_<term>.json`:
  `{"generated","year","term","collected_at","classes":{"sbjt|lt":{"ko":[...],"en":[...]}}}`.
  - Missing classes are omitted.
  - Written with `_write_stamped`.
- **Timestamps:** ISO 8601 with `+09:00` (R-000008).
- **Guards:**
  - more than 2% of a term's classes failing after one retry → the term is not pushed and the run exits 1;
  - fewer than 90% of the previous snapshot's classes → refused;
  - pull staleness 48 h, checked only for target terms (current and upcoming).
- **Pull exit codes:** 0 nothing changed, 4 applied, 3 stale target term,
  other = failure. `update.sh` publishes anyway.
- **Download URL:**
  `https://shine.snu.ac.kr/com/download/noflt/downloadWebFile.action?cscLocale=ko_KR&attcFileNo=<no>&attcFileDetaNo=<deta>`
- **Drawer statuses:** `-`, `미수집`, `불러오는 중`, `불러오지 못함`.
- **원본 link:** `${SYLLABUS_PROXY}/sugang/cc/cc103.action?...`, opened with
  `target=_blank rel=noopener` (never `noreferrer`). The row is hidden while
  `SYLLABUS_PROXY` is empty, which is the default until deploy.
- **Worker:**
  - relays only `GET|POST /sugang/cc/cc1\d{2}(ajax)?\.action` and `GET /kor/*`, `GET /adm/*` (refuses `..`);
  - Referer origin must be in `ALLOWED_ORIGINS` (`https://rekhet.github.io`) or be the Worker's own origin, else 403;
  - drops `Cookie`, sets `Referer: https://sugang.snu.ac.kr/`, drops `Set-Cookie`;
  - static files without `Cache-Control` get `public, max-age=86400`;
  - upstream error or a 15 s timeout → 502.
- **Where to work:**
  - The scheduled `update.sh` runs the code checked out in the main working
    tree, so all scraper work happens in a git worktree,
    `.tmp/wt/syllabus` (branch `feat/syllabus-links`). The main working tree
    goes back to `main`.
  - `web/` is a separate repository: work on branch `feat/syllabus-links` in a
    worktree `.tmp/wt/web-syllabus`.
  - Never push `web/`.
  - Leave the owner's untracked files in `web/` (`graphify-out/`,
    `index-dev.html`, `partials/dev.html`, `tmp/`) alone.
- **Ask-first operations** (Task 8 only): creating cloud tables, the first real
  collect, `--past`, pushing either repository, Cloudflare deploy, and setting
  `SYLLABUS_PROXY`.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| sugang refuses Cloudflare egress IPs | high (원본 link dead) | known only after deploy (Task 8). Attachments do not depend on the Worker |
| Cloud `classes` lacks some past terms | medium | Task 2 `--dry-run --past` prints per-term roster counts; compare with local `classes` before the real run |
| GitHub `schedule` fires late or skips | low | 48 h stale alert in pull; `workflow_dispatch` available |
| A half-built change reaches the live site | high | worktrees; `SYLLABUS_PROXY` empty by default; `web/` never pushed without approval |
| One page view costs many Worker requests | medium | measured in Task 8 against the 100,000/day quota |

## Open questions

None blocking. The Cloudflare account and the `workers.dev` subdomain come from
the owner in Task 8.

---

### Task 0: Worktrees

**Files:** none (git state only). **Depends on:** none. **Size:** XS

- [ ] In the main working tree, `git switch main` first (a branch can't be
  checked out twice), then `git worktree add .tmp/wt/syllabus feat/syllabus-links`.
- [ ] `git -C web worktree add -b feat/syllabus-links ../.tmp/wt/web-syllabus main`
  (path relative to `web/`). Check with `git -C web worktree list`.
- [ ] Acceptance:
  - both worktrees exist;
  - `git branch --show-current` in the main tree prints `main`;
  - `uv run pytest -q tests` passes in `.tmp/wt/syllabus`.

### Task 1: Attachment parsing and snapshot

**Files:**
- Create: `scraper/syllabus_attach.py` (parsing, snapshot, guard, push, init-remote parts)
- Modify: `scraper/db.py` (add `SYLLABUS_ATTACH_DDL` next to `CATALOG_SNAPSHOTS_DDL`, include it in `SCHEMA`)
- Create: `tests/fixtures/cc103ajax/` with 5 real responses: `none.json`,
  `ko_only.json`, `en_only.json`, `both.json`, `empty_name.json`
- Create: `tests/test_syllabus_attach.py`

**Interfaces (provides):**
- `parse_attach(resp: dict) -> dict[str, list[list]]`: `{"ko": [[name, no, deta], ...], "en": [...]}`.
  Languages with no file are omitted. An empty name is skipped. `deta` defaults to 0 when null.
- `build_snapshot(year: str, term: str, attach: dict[str, dict], missing: list[str], *, collected_at: str) -> dict`
  returns `{"year","term","digest","classes","missing","collected_at","payload"}`.
  - `digest` = sha256 of the canonical JSON **without** `collected_at`, so an
    unchanged term keeps its digest.
  - `classes` = `len(attach)`, the collected classes; `missing` = `len(missing)`.
- `check_snapshot(remote, snap: dict, *, total: int, max_fail: float = 0.02, min_keep: float = 0.9) -> None`
  raises `SnapshotError`:
  - when `snap["missing"] > total * max_fail`;
  - when `snap["classes"] + snap["missing"] < previous * min_keep`, comparing
    with the previous row's `classes + missing`.
- `push(remote, snap: dict, *, now: str) -> str` returns `'new' | 'changed' | 'unchanged'`;
  `unchanged` updates only `checked_at`.
- `init_remote(remote) -> None`
- `_now() -> str`: `datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")`
- Keys: `key(sbjt, lt, subh) -> "sbjt|lt|subh"` (`subh or "000"`).

**Acceptance criteria:**
- [ ] Each of the 5 fixtures parses to the expected dict (written out in the test).
- [ ] The digest ignores `collected_at` and insertion order; it changes when a file name changes.
- [ ] The guard rejects 3 failures of 100, accepts 2 of 100, and rejects a shrink from 100 to 89.

**Depends on:** 0. **Size:** S

- [ ] Capture the fixtures once, by hand, from the live endpoint (request shape
  above). Candidates seen 2026-10-16:
  - `F22.101/006` (2026-2 Korean),
  - `430.836/001` (English only),
  - `M2868.007600/001` (both),
  - `M2608.003400` (empty-name entry; 2026-2, section from the local `classes`),
  - `100.100/001` (none).
  Keep only `LISTTAB03_LIST_ATTACH` and `LISTTAB03_LIST_ATTACH_ROWCNT` in the
  saved files, so no syllabus text is committed.
- [ ] Write the failing tests: `python -m pytest tests/test_syllabus_attach.py -q`
  → FAIL (module missing).
- [ ] Implement:

```python
def parse_attach(resp: dict) -> dict:
    out = {}
    for row in resp.get("LISTTAB03_LIST_ATTACH") or []:
        for lang, p in (("ko", "kor"), ("en", "eng")):
            name, no = (row.get(f"{p}FileNm") or "").strip(), row.get(f"{p}AttachNo")
            if name and no:
                out.setdefault(lang, []).append([name, no, row.get(f"{p}AttachDetaNo") or 0])
    return out
```

- [ ] Run the tests → PASS. Also run `uv run pytest -q tests`.

### Task 2: `collect` command

**Files:**
- Modify: `scraper/syllabus_attach.py` (roster, fetch loop, `collect`, `main`)
- Test: `tests/test_syllabus_attach.py`

**Interfaces:**
- Uses: Task 1 functions; `catalog_snapshot.parse_terms`, `catalog_snapshot.TERM_CODES`, `catalog_snapshot._remote` (import, do not copy).
- Provides:
  - `target_terms(env: dict) -> list[tuple[str, str]]`: the current
    `(COUNT_YEAR, TERM_CODES[COUNT_SEM])` plus `parse_terms(UPCOMING_TERMS, ...)`.
  - `past_terms(remote, targets) -> list[tuple[str, str]]`: distinct cloud
    `classes(year, term)` with `year >= "2020"`, minus targets, oldest first.
  - `roster(remote, year, term) -> list[tuple[str, str, str]]`: `(sbjt_cd, lt_no, subh_cd)`.
    It reads cloud `classes`; when that is empty, it reads the `catalog_snapshots`
    payload's `classes`.
  - `fetch_term(http, year, term, keys, *, interval=0.1, sleep=time.sleep) -> tuple[dict, list]`
    returns `(attach, missing)`. Each key is tried, then retried once after 1 s.
  - `collect(terms, *, dry_run=False, force=False, skip_existing=False, http=None, remote_factory=_remote) -> int`
- CLI: `collect [--past] [--force] [--dry-run] [--terms SPEC]`, `pull --dest PATH [--max-age-hours 48]`, `init-remote`.

**Acceptance criteria:**
- [ ] With a fake http (a `post` returning fixture JSON, raising for chosen
  keys), a 50-key term with 1 failure is pushed with `missing == 1`. With 2
  failures (4%) it is not pushed and `collect` returns 1.
- [ ] `--past` skips terms that already have a cloud row unless `--force`.
  It pushes each term as soon as it completes; a crash on term 3 leaves terms 1–2 stored.
- [ ] `--dry-run` prints `year/term: N classes, roster M, missing K` per term and writes nothing.
- [ ] The remote connection is opened per push, never held across a fetch loop
  (the Hrana stream expiry noted in `cloud_collect.py`).

**Depends on:** 1. **Size:** M

- [ ] Failing tests → implement → `uv run pytest -q tests` PASS.

### Task 3: `pull` command

**Files:**
- Modify: `scraper/syllabus_attach.py` (`pull`; `main` uses `catalog_snapshot.open_local` for `--dest`)
- Test: `tests/test_syllabus_attach.py`

**Interfaces:**
- Provides: `pull(remote, local, *, now: str, max_age_hours: float = 48, targets: list[tuple[str, str]]) -> dict`
  returns `{"terms": n, "applied": [(y, t)], "stale": [(y, t)]}`.
  - Copies every cloud row whose digest differs from the local one (all terms, past included).
  - `stale` covers only rows in `targets`.

**Acceptance criteria:**
- [ ] A changed digest is copied; an equal one is skipped.
- [ ] A past term with an old `checked_at` is not stale.
- [ ] A target term older than 48 h is stale and makes the CLI exit 3.
- [ ] The CLI exits 4 when anything was applied, 0 otherwise.
- [ ] `--dest` must be a local file (same check as `catalog_snapshot.open_local`).

**Depends on:** 1. **Size:** S

- [ ] Failing tests → implement → `uv run pytest -q tests` PASS.

### Checkpoint: after Tasks 1–3

- [ ] `uv run pytest -q tests` passes.
- [ ] Live dry run (read-only on sugang, no cloud write):
  `uv run python -m scraper.syllabus_attach collect --terms 2026:winter --dry-run`
  ends with `missing` ≤ 2%. This term has 260 classes, about 30 s.

### Task 4: Export and local wiring

**Files:**
- Modify: `scraper/export_json.py`. Add `export_syllabus(conn, out_dir: Path) -> int`
  and call it in both the full and the `--current` paths of `main`.
- Modify: `scripts/publish.sh`. In full mode, `git add` also `data/syllabus/`.
- Modify: `scripts/update.sh`. After the catalog pull, add the pull step
  (same `( set -a; . turso-remote.env; ... )` pattern and `--dest "$LOCAL_DB"`):
  - `SYLLABUS_FAILED=1` on exit 3 or any other failure;
  - the final error lines mirror `CATALOG_FAILED`.
- Test: `tests/test_syllabus_attach.py`; `tests/test_publish_script.py` if it
  checks the add list.

**Interfaces:**
- Provides: `export_syllabus(conn, out_dir)`. For each local snapshot row, it
  writes `out_dir/"syllabus"/f"{year}_{term}.json"` per the global web-file
  shape and returns the number of files.

**Acceptance criteria:**
- [ ] The test DB with one snapshot gives a file whose keys are `sbjt|lt`, with
  missing classes absent and `{}` kept for no-attachment classes.
- [ ] A second export with unchanged data keeps the file byte-identical (`_write_stamped`).
- [ ] `bash -n scripts/update.sh scripts/publish.sh` passes.

**Depends on:** 3. **Size:** M

### Task 5: Workflow and docs

**Files:**
- Create: `.github/workflows/collect-syllabus.yml`
- Modify: `collect.env.example` (a comment on what the syllabus run reads)
- Modify: `README.md` (one line in the pipeline section)

**Workflow content:**
- Copy the `collect-catalog.yml` structure, without the Playwright steps.
- `on: schedule: cron "40 19 * * *"` plus `workflow_dispatch` with input `past`
  (boolean, default false).
- `concurrency: collect-syllabus`, one job, `timeout-minutes: 360` (the
  `--past` run is about 2.8 h; a daily run is about 21 min).
- Run: `set -a; . ./collect.env; set +a; uv run python -m scraper.syllabus_attach collect ${{ inputs.past && '--past' || '' }}`
- Secrets: `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.

**Acceptance criteria:**
- [ ] `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/collect-syllabus.yml'))"` passes.
- [ ] No Playwright step.

**Depends on:** 2. **Size:** S

### Task 6: Drawer group (web repository)

**Files** (worktree `.tmp/wt/web-syllabus`):
- Modify: `config.js` — `window.SYLLABUS_PROXY = "";` with a comment.
- Modify: `app.js`:
  - `loadSyllabus(year, term)`: a cached promise per term, fetching
    `data/syllabus/${year}_${term}.json`; a 404 resolves to `null`;
  - `syllabusBlock(c)`: the DOM for the group;
  - call it in `renderDetail` after 수업 시간.
- Modify: `styles.css` (only if the existing `d-section` / `d-rows` / `d-row`
  rules are not enough).
- Create: `tests/test_syllabus.py`. Use `unittest` and Playwright, served like
  `test_rooms.py`. Stub `data/syllabus/*` with `page.route`.

**Behavior:**
- Section heading `강의계획서`, then rows `원본` (only when `SYLLABUS_PROXY` is
  set), `국문`, `영문`.
- Row value:
  - while loading → `불러오는 중`;
  - fetch error → `불러오지 못함`;
  - file `null` or no key → `미수집`;
  - key present, no files in that language → `-`;
  - otherwise one `<a href=shine-url>` per file, the text being the file name as given.
- The 원본 link carries the query from `c.year`, `c.shtm_fg`, `c.deta_shtm_fg`,
  `c.sbjt_cd`, `c.lt_no`, `c.subh_cd || "000"`.
- Re-render only if the drawer still shows the same class (`detailClass`) when
  the fetch resolves.

**Acceptance criteria:**
- [ ] Playwright Chromium:
  - all four statuses appear for the matching stub states;
  - two Korean files render as two links;
  - clicking a link to a stubbed `Content-Disposition: attachment` response
    fires a download and leaves `page.url` unchanged;
  - the 원본 `href` and `rel` (no `noreferrer`) are correct when `SYLLABUS_PROXY` is set;
  - the row is absent when it is empty.
- [ ] From `.tmp/wt/syllabus`: `uv run pytest -q ../web-syllabus/tests/test_syllabus.py`
  passes. The full `web/tests` run happens after the merge (Task 8 step 5),
  because other web tests read scraper fixtures through the real `web/` location.

**Depends on:** 4 (file shape only, so it can start in parallel once the shape
above is fixed). **Size:** M

### Task 7: Worker

**Files:**
- Create: `worker/syllabus-proxy/src/index.js`. Export `default { fetch }` and
  `handle(request, env, upstreamFetch)`.
- Create: `worker/syllabus-proxy/wrangler.toml`:
  - `name = "class-checker-syllabus"`
  - `main = "src/index.js"`
  - `compatibility_date = "2026-10-01"`
  - `[vars] ALLOWED_ORIGINS = "https://rekhet.github.io"`
- Create: `worker/syllabus-proxy/test/index.test.js` (`node:test`, fake upstream)
- Delete nothing in `.tmp/refpage/` (it is a reference only).

**Interfaces:**
- Provides: `handle(request: Request, env: {ALLOWED_ORIGINS: string}, upstreamFetch = fetch) -> Promise<Response>`

**Acceptance criteria:**
- [ ] Referer `https://rekhet.github.io/class-checker/` → relayed. The Worker's
  own origin → relayed. `https://evil.example/` or none → 403.
- [ ] The upstream request has `Referer: https://sugang.snu.ac.kr/` and no `Cookie`.
  The client response has no `Set-Cookie`.
- [ ] `/sugang/cc/cc103.action` and `/sugang/cc/cc103ajax.action` (POST, body
  passed through) are relayed. `/sugang/cc/cc999x.action`, `/etc/passwd` and
  `/kor/../x` give 404.
- [ ] `/kor/js/common.js` without upstream `Cache-Control` gets `public, max-age=86400`.
- [ ] Upstream throws or times out → 502.
- [ ] `node --test worker/syllabus-proxy/test/index.test.js` passes.
- [ ] Local live smoke: `npx wrangler@4.147.0 dev` (no account needed for local
  dev) plus Playwright Chromium opening
  `http://127.0.0.1:8787/sugang/cc/cc103.action?...` with Referer
  `https://rekhet.github.io/`, and the filled syllabus shows (professor name present).

**Depends on:** 0. **Size:** M

### Checkpoint: after Tasks 4–7

- [ ] `uv run pytest -q tests` and `uv run pytest -q web/tests` pass, and so does `node --test worker/syllabus-proxy/test/index.test.js`.
- [ ] Final review (opus) of the full diff of both repositories.

### Task 8: Rollout (each step asks the owner first)

1. Merge `feat/syllabus-links` into `main` and push the scraper repository.
   The workflow must be on `main` to run.
2. `uv run python -m scraper.syllabus_attach init-remote`, with `turso-remote.env`.
3. `workflow_dispatch` collect-syllabus (targets). Check the run log: per-term
   counts and missing ≤ 2%.
4. `--dry-run --past` locally: compare per-term roster counts with local
   `classes`. Then `workflow_dispatch` with `past=true`.
5. Local `update.sh`, or the pull + export by hand: `web/data/syllabus/*.json`
   appear. Merge the web branch, approve, publish.
6. The owner creates the Cloudflare account and runs `npx wrangler@4.147.0 login`;
   `deploy` from `worker/syllabus-proxy/`.
   - Live check: Chromium opens the 원본 link from the live site with data, and
     a foreign Referer gets 403.
   - Measure the Worker requests per page view (dashboard or `wrangler tail`).
7. Set `SYLLABUS_PROXY` in `web/config.js` to the deployed URL. Publish.
8. The owner checks Firefox. Update the spec Status and the memory notes.
