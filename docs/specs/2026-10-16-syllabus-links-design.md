# Syllabus links: original page via proxy + attachment downloads (design, 2026-10-16)

## Status

Design approved section by section by the owner on 2026-10-16 (flow, screen and
Worker, error handling, tests). Decisions: governance log `class-checker`
D-000003 … D-000023.

Implemented 2026-10-17 on branch `feat/syllabus-links` (scraper worktree) and
web branch `feat/syllabus-links`; not deployed yet. Plan:
`docs/plans/2026-10-16-syllabus-links.md`. Changed after approval by the owner
during review (the sections below already include them):

- D-000032 — a target term with no cloud row is stale at once.
- D-000034 and D-000040 — the Worker answers an upstream 3xx, and a 4xx/5xx of
  the page itself, with a Korean notice.
- D-000035 — the drawer retries a failed term file on the next open.
- D-000037 — early abort, a 60 s wait, then one restart.
- D-000038 — failed classes carry their previous entry; `--force` bypasses the
  shrink guard.
- D-000039 — the group is replaced in place; the HTTP session is reused.
- D-000043 — a broken term file is skipped, not the whole export.

The Worker's static-path whitelist was a review-defect fix.

Rollout so far (2026-10-17 … 18): table created, first collection (3 terms,
missing 0), past terms in two runs (26 terms, 4 classes missing: sugang
answers them with an empty body, shown as 미수집, D-000050), web drawer
published. The Worker is not deployed yet (D-000053). Measured: ~195
classes/min, a daily run ~66 min. D-000047 replaced the `--dry-run --past`
check with a roster-only comparison (no sugang requests).

## Goal

A class's detail drawer gets a 강의계획서 group with two independent fields:

1. **원본** — a link that opens sugang's own syllabus page (`cc103.action`) for
   that class and term in a new tab, and actually shows it.
2. **국문 / 영문** — the syllabus attachments, shown inside our page with the
   same file-name text sugang shows (e.g. `강의계획서(초급독일어1).hwp (123.5KB)`).
   Clicking one downloads the file without leaving the page.

We do not rebuild the syllabus page itself.

## Findings this design rests on (verified 2026-10-16)

| Fact | How it was checked |
|---|---|
| `GET /sugang/cc/cc103.action?openSchyy&openShtmFg&openDetaShtmFg&sbjtCd&ltNo[&sbjtSubhCd]` is the standalone syllabus page; it loads its data from `POST cc103ajax.action` (HTTP 201) | page source; curl; Playwright |
| The only gate is the `Referer` header: it must contain `.snu.ac.kr`. Cookies are irrelevant: dropping each of JSESSIONID, enter, NetFunnel_ID, WMONID, or all of them, still gives 200 | curl variants |
| No Referer (typed URL, `rel=noreferrer`) or a foreign Referer → 302 to `/` → main page | curl; Playwright, cold and entered browsers |
| A plain link from our site can't work; SNUTT web relays the page through its own reverse proxy (`wafflestudio/snutt-proxy`), SNUTT iOS sets `Referer` on its WebView request | their public source |
| Attachments are plain GETs on `https://shine.snu.ac.kr/com/download/noflt/downloadWebFile.action?cscLocale=ko_KR&attcFileNo=<uuid>&attcFileDetaNo=<n>`, with no Referer check; `Content-Disposition: attachment; filename=<percent-encoded>` | curl |
| Clicking such a link downloads with the correct Korean name and keeps the page, in Chromium (automated) and Firefox (owner) | Playwright; owner |
| `fetch()` of a shine file fails in the browser (CORS), so downloads must be plain links | Playwright |
| An attachment entry with an empty file name does not download (404 page); sugang's own page does not show such entries | sampled 2026-2 |
| Past attachments still download (2023-1: 29/29 files) | sample |
| sugang's page shows only the last attachment per language (assignment inside its loop); we show all | page source |
| cc103ajax: median 6.6 KB, 0.06 s per class; 2026-2 has 8,652 classes | sample of 40 |

## Design

### Flow (S1)

1. **Collect** — GitHub Actions `collect-syllabus.yml`, daily at 01:40 KST
   (cron `40 16 * * *`, plus `workflow_dispatch`; was 04:40 until D-000060), runs
   `python -m scraper.syllabus_attach collect`.
   - Terms: the current term (`COUNT_YEAR`/`COUNT_SEM`) and `UPCOMING_TERMS`,
     read from `collect.env` as `catalog_snapshot` does (`parse_terms`).
   - Roster: cloud `classes` for current and past terms; the newest cloud
     `catalog_snapshots` row for upcoming terms.
   - Per class, `POST cc103ajax.action` with `Referer: https://sugang.snu.ac.kr/`,
     no session or browser, at 10 requests/s. Keep only `LISTTAB03_LIST_ATTACH`
     entries that have a file name.
2. **Store** — one row per term in cloud table `syllabus_attach_snapshots`
   (zlib JSON + digest). An unchanged term only refreshes `checked_at`.
3. **Pull** — local `scripts/update.sh` runs
   `python -m scraper.syllabus_attach pull --dest <local db>` and copies changed
   rows into the local table of the same name. It does not contact sugang.
4. **Export** — `export_json` writes `web/data/syllabus/<year>_<term>.json` for
   every term that has a snapshot.
5. **Web** — the first time a class of term T is opened in the drawer, the page
   fetches T's syllabus file once (cached in memory for the session).
6. **Past terms** — the same command with `--past`, run once through
   `workflow_dispatch`, covering 2020 … 2026-1 (~101,500 classes, about 8.7 h
   measured, so two runs under the 6 h timeout; D-000047).
   Each term is pushed when it completes, so a cut-off run resumes at the next
   term on rerun (`--past` skips terms that already have a snapshot unless
   `--force`).

### Contracts

**Cloud and local row** — `syllabus_attach_snapshots(year, term, digest,
payload BLOB, classes INTEGER, missing INTEGER, collected_at, checked_at,
PRIMARY KEY(year, term))`. `payload` = zlib of:

```json
{"year": "2026", "term": "U000200002U000300001", "collected_at": "2026-10-17T04:52:10+09:00",
 "classes": {"F22.101|006|000": {"ko": [["강의계획서(초급독일어1).hwp (123.5KB)", "1d8e1d3e-…", 0]]},
             "100.100|001|000": {}},
 "missing": ["M2608.003400|001|000"]}
```

- Every collected class has a key; `{}` means "no attachment".
- `missing` lists classes that failed after the retry.
- A class with no key at all (for example, added after the run) is "not collected".

**Web file** — `web/data/syllabus/<year>_<term>.json`:

```json
{"generated": "…", "year": "2026", "term": "U000200002U000300001", "collected_at": "…",
 "classes": {"F22.101|006": {"ko": [["강의계획서(초급독일어1).hwp (123.5KB)", "1d8e1d3e-…", 0]]}}}
```

- Keys are `sbjt_cd|lt_no`, the pair the web already uses.
- `missing` classes are left out, so they read as "not collected".
- The download URL is built in the browser from the uuid and the detail number.
- Timestamps are ISO 8601 with offset (global R-000008).

**Worker URL** — `web/config.js` gets `window.SYLLABUS_PROXY`
(e.g. `https://class-checker-syllabus.<account>.workers.dev`). The 원본 link is
`${SYLLABUS_PROXY}/sugang/cc/cc103.action?openSchyy=…&openShtmFg=…&openDetaShtmFg=…&sbjtCd=…&ltNo=…&sbjtSubhCd=…`,
opened with `target="_blank" rel="noopener"`. It must not be `noreferrer`,
because the Worker checks the Referer.

### Screen (S2)

The 강의계획서 group in the detail drawer:

| Row | Content |
|---|---|
| 원본 | `수강신청 사이트에서 보기 ↗`, a new tab. Hidden when `SYLLABUS_PROXY` is empty |
| 국문 | every Korean attachment as a link with sugang's file-name text, or a status |
| 영문 | same for English |

Status texts:

- `-` — collected, no attachment in that language.
- `미수집` — the term has no file, or the class has no key.
- `불러오는 중` — the term file is being fetched.
- `불러오지 못함` — the fetch failed. The next opening of a class of that term
  fetches again (D-000035).

The look follows the drawer's existing key/value grid. When the term file
arrives, only the group is replaced, so focus and scroll stay (D-000039).

### Worker (S3)

- A Cloudflare Worker on `workers.dev`, modeled on `snutt-proxy`. It relays only:
  - `GET`/`POST /sugang/cc/cc1NN(ajax)?.action`
  - `GET /kor/*` and `GET /adm/*`. Static paths must match
    `^/(kor|adm)(/[A-Za-z0-9_.-]+)+$`. That refuses any `%`, backslash, empty
    segment or `..`; every static path sugang's page loads fits.
- Requests must carry a Referer whose origin is `https://rekhet.github.io` or
  the Worker's own origin (needed for the relayed page's own subrequests).
  Anything else gets 403. The allow-list is a Worker variable, so an app can be
  added later.
- On the way to sugang: set `Referer: https://sugang.snu.ac.kr/`, drop `Cookie`.
- On the way back: drop `Set-Cookie`. Static files without `Cache-Control` get
  `public, max-age=86400`.
- Lives in `worker/syllabus-proxy/` (`wrangler.toml`, `src/index.js`, `test/`).
  It is deployed by the owner's Cloudflare account through a version-pinned
  `npx wrangler@<version>`.

### Error handling

- **Class fetch** — each class is retried once.
  - As soon as failures pass 2% of the roster, the pass stops. After about 60 s
    the whole term is collected once more.
  - If the second pass also passes 2%, the term is not pushed and the run fails
    (GitHub mails the owner). Other terms still run (D-000037).
  - Classes that fail within 2% carry their entry from the previous snapshot.
    Only classes absent from it go to `missing` (D-000038).
  - Counts and the list are printed (global R-000009).
- **Shrink guard** — a snapshot with fewer than 90% of the previous snapshot's
  classes is refused (`MIN_KEEP`, same as `catalog_snapshot`). A manual
  `--force` run bypasses it (D-000038).
- **Pull staleness** — exit 3 when a target term's `checked_at` is older than
  48 h, or when it has no cloud row at all (D-000032, reported separately).
  `update.sh` raises the existing ops alert. Exit 4 means rows changed; 0 means
  nothing changed. Any pull failure still publishes, as the other pulls do.
- **Export** — a term whose file cannot be built (key collision, broken
  payload) keeps its previous file and is reported. The rest of the export and
  publish go on, and the run ends with exit 5 (D-000043).
- **Worker**:
  - an upstream 3xx on an action route, or a 4xx/5xx of the page itself
    (non-ajax action), gets 502 with a Korean notice and a link to sugang
    (D-000034, D-000040);
  - an ajax or static error passes through;
  - a static 3xx gets a plain 502;
  - timeout → 502;
  - disallowed Referer → 403;
  - quota exhausted → Cloudflare 1027 (fail closed).

  The page itself does not depend on the Worker: only the 원본 link breaks.
- 2% and 48 h are assumptions without data, to be tuned in operation.

## Tech stack

- Python 3 (project `uv` env): `requests`, existing `libsql` / `db` helpers, `zlib`.
- Vanilla JS in `web/app.js`.
- Cloudflare Workers (module syntax).
- Node 24 for Worker tests (`node:test`, no dependencies).
- `wrangler` pinned through npx, for deploy only.

## Commands

- `uv run pytest -q tests` — scraper.
- `uv run pytest -q web/tests` — web, Playwright Chromium.
- `cd worker/syllabus-proxy && node --test` — Worker (a directory argument
  fails on Node 24).
- `python -m scraper.syllabus_attach collect [--past] [--force] [--dry-run]`
- `python -m scraper.syllabus_attach pull --dest data/turso.db`
- `python -m scraper.syllabus_attach init-remote`
- `make export-json`

## Project structure

| Path | Role |
|---|---|
| `scraper/syllabus_attach.py` | collect / pull / init-remote |
| `scraper/export_json.py` | + syllabus files |
| `.github/workflows/collect-syllabus.yml` | daily run + manual `--past` |
| `scripts/update.sh` | + pull step |
| `web/app.js`, `web/config.js`, `web/styles.css` | drawer group, `SYLLABUS_PROXY` |
| `worker/syllabus-proxy/` | Worker + tests |
| `tests/test_syllabus_attach.py`, `tests/fixtures/cc103ajax_*.json` | scraper tests |
| `web/tests/test_syllabus.py` | browser tests |

`web/` is its own Git repository (the Pages site). Its changes stay on a branch
until the owner approves them, because `update.sh` pushes `web/` when it is ahead.

## Code style

Follow `catalog_snapshot.py`: a module docstring that explains the pipeline,
`collect` / `pull` / `init-remote` subcommands. One deliberate difference:
timestamps carry an offset (`+09:00`, global R-000008) instead of
`catalog_snapshot`'s naive UTC. For example:

```python
def build_snapshot(year: str, term: str, attach: dict[str, dict], missing: list[str]) -> dict:
    """One term's attachments. `attach` maps "sbjt|lt|subh" -> {"ko": [...], "en": [...]}."""
```

## Test strategy

- **pytest, no network**:
  - parsing of real cc103ajax fixtures covering five cases: no attachment, Korean
    only, English only, both, and an entry with an empty file name;
  - snapshot build and digest;
  - unchanged term → `checked_at` only;
  - the 2% and 90% guards;
  - pull exit codes 0 / 3 / 4;
  - export file shape and keys.
- **Playwright Chromium (`web/tests`)**: open a detail drawer with a stubbed
  syllabus file, then check:
  - the four status texts;
  - multiple files per language;
  - clicking a file starts a download and the page URL is unchanged;
  - the 원본 link has the right href and no `noreferrer`;
  - the row is hidden without `SYLLABUS_PROXY`.
- **Worker (`node:test`, fake upstream)**:
  - allowed vs refused Referer;
  - Cookie and Set-Cookie dropped;
  - path allow-list and `..` refused;
  - upstream error → 502.
- **After deploy, once**: the 원본 link from the live site opens a filled
  syllabus in Chromium.
- **By the owner**: Firefox checks the 원본 link and the downloads.
- **Not tested**: Safari (owner's decision, D-000019). Its support status is unknown.

## Boundaries

- **Always**:
  - rate-limit sugang to 10 requests/s;
  - keep re-runs safe (idempotent);
  - report skipped or failed classes with counts;
  - never store syllabus body text, only attachment names and numbers.
- **Ask first**:
  - new dependencies;
  - changes to existing cloud tables;
  - creating the Cloudflare account or Worker (the owner's account);
  - pushing `web/`;
  - running `--past`;
  - changing the schedule.
- **Never**:
  - commit secrets or account tokens;
  - send sugang session cookies through the Worker;
  - put upcoming-term classes into cloud `classes`;
  - open the Worker to all Referers.

## Success criteria

1. `collect` fills a snapshot for every target term with `missing` ≤ 2%, and the
   daily workflow succeeds.
2. After `update.sh`, `web/data/syllabus/<term>.json` exists for the target terms.
   After `--past`, it exists for every term from 2020.
3. In Chromium on the live site, the drawer shows the four states correctly, a
   file click downloads with sugang's file name while the page stays, and the
   원본 link opens a filled syllabus page.
4. The Worker answers 403 to a foreign Referer and to none.
5. `pytest tests`, `pytest web/tests` and `node --test` pass.
6. The owner confirms the 원본 link and the downloads in Firefox.

## Open questions

- Whether sugang accepts requests from Cloudflare's egress IPs. Answerable only
  after deploy; if refused, the fallback is a new decision.
- How many relayed requests one 원본 page view costs against the 100,000/day free
  quota. To be measured once the Worker is built.
