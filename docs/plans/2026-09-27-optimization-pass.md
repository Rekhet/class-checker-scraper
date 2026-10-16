# Optimization and Hardening Pass (2026-09-27)

## Status

Done except the pending checks listed at the end. Started 2026-09-27 from a read-only audit of the collector,
export/web, and operations. Each item below is committed separately, measured
against real data (not only unit tests), and pushed only after the measurement
shows no regression. Items touching the collector are verified against the
next GitHub Actions runner pass.

## Decisions (owner, 2026-09-27)

- Scope: P0 safety/correctness, P1 export/trend format, P1 Turso query/write
  cost, P2 operations/docs/CI, and four web features (share URLs + detail→trend
  link, change feed, in-tab vacancy alerts, dark mode + accessibility).
- `web/` is in scope, including push.
- Local publish runs daily (weekends included) at 06:00, 12:00, 18:00.
- Local automation failures open/comment a GitHub issue; the issue carries no
  log text — logs stay in local files only.
- `data/turso.db` is backed up weekly to `~/.backup/class-checker/`, maximum
  compression, newest 3 kept.
- `web/` git history is kept; only the payload format shrinks.
- Cloud Turso schema changes may be applied directly (after a read-only
  duplicate count; only fully identical rows are removed).
- The GitHub `schedule:` trigger stays as a backup to cron-job.org.
- Try replacing Playwright session minting with plain requests, keeping
  Playwright as the fallback.
- Finished-window systemd units are disabled; unused local files move to
  `~/.backup/class-checker/attic/` rather than being deleted.

## Items

| # | Item | Status |
|---|------|--------|
| 1 | `.gitignore`: cover `backups/*.env*`, `backups/*.db` | done |
| 2 | Collector: overlay `count_latest` onto the scratch roster, fail on empty/low-coverage fetch, re-raise sampling errors on the cloud path, fail on empty roster | done |
| 3 | Workflow: year/semester from `collect.env`, window gate before setup, `PYTHONUNBUFFERED`, `uv sync --locked`, Node 24 action versions | done |
| 4 | `_slow_slot_minutes` default 60 (matches docs and collect.env) | done |
| 5 | `server.py`: reject cross-origin POSTs when no admin token is set | done |
| 6 | Turso: `classes(year, term)` index, keyframe partial index, bootstrap copies only the latest keyframe | done |
| 7 | Turso: `count_samples` UNIQUE key, `INSERT OR IGNORE` push/pull, pull reuses `db.fold_pass_into_latest` | done |
| 8 | Keyframe pushes only changed `count_latest` rows | done |
| 9 | Export: stop rewriting unchanged files (`generated` churn) | done |
| 10 | Trend change-point format + client decoder, LRU window cache, time-based X axis, KST timestamps | done |
| 11 | Client boot: cacheable assets, parallel partials, `loadMore` sort cache | done (catalog slimming deferred, see log) |
| 12 | Features: share URLs + detail→trend, change feed, vacancy alerts, dark mode + a11y | done |
| 13 | Ops: publish timer 06/12/18, `OnFailure` → GitHub issue, `TimeoutStartSec`, weekly backup | done |
| 14 | CI: test workflow, `dev` extra with pytest | done |
| 15 | Docs drift fixes and a semester rollover checklist | done |
| 16 | Cleanup: disable finished-window units, move unused files to attic | done |
| 17 | Experiment: requests-only session mint with Playwright fallback | not pursued (see log) |

## Verification log

### Items 2–4: collector guards, workflow (2026-09-27)

- Measured the regression the overlay prevents, read-only against the cloud
  database: after bootstrap, **3,471 of 8,652** fall-2026 classes carried
  seeded counts that differ from the current `count_latest`. Under the old
  code a pass whose live fetch came back empty would have recorded all 3,471
  as "changes" back to those stale values, and exited 0.
- `--dry-run` pass against the real cloud and sugang (no push):
  `bootstrap: classes 8652, latest 8652, keyframes 22` →
  `overlay: updated 8652` → `collect: updated 8652, samples 0` →
  `coverage: 100.00%`, 75 s wall time. Recent runner passes also show
  8,652/8,652, so the 95% floor leaves ample margin for 폐강 drop-outs.
- Runner gate: `python3 scraper/windows.py gate` with collect.env prints
  `active=true` today (slow window) and `active=false` with the slow window
  cleared; parity with `crawl._window_active` is tested over 8 cases.
- Runner verification: run 36319941361 on 2da30e8 (workflow_dispatch,
  2026-09-27 12:43Z) succeeded — `active=true`, `bootstrap … classes 8652`,
  `overlay … updated 8652`, `coverage: 100.00%`, `push: passes 1`. The first
  dispatch on 5c6d76e failed at action resolution: setup-uv publishes no
  floating `v10` tag, fixed by pinning `v10.2.0` (2da30e8). Step timings
  match the previous code (collect 2:15 vs 2:09).

### Item 5: server.py cross-origin POSTs (2026-09-27)

- Against a real dev server on port 8765 (local turso.db): curl without
  Origin → 200; `Origin: https://evil.example` on `/api/lookup` and on a
  `text/plain` `/api/refresh` → 403; a same-origin `fetch()` from the page in
  headless Chromium → 200 with real class rows.

### Items 6–8: Turso reads and writes (2026-09-27)

- Read-only duplicate check before the UNIQUE change: cloud `count_samples`
  204,723 rows, local 7,229,722 rows — **0** duplicate keys and 0 exact
  duplicates in both, so no row was deleted anywhere.
- Local `data/turso.db` migrated under the process lock (`init_schema`,
  120.5 s, one-time); snapshot taken first to `~/.backup/class-checker/`.
  Query plans: roster read → `SEARCH classes USING INDEX idx_classes_term
  (year=? AND term=?)`; keyframe lookup → `COVERING INDEX idx_passes_full`.
- Cloud migrated with the same code (`CREATE INDEX` ×2 +
  `ensure_sample_key_unique`), 7.0 s, row count unchanged (204,723). The
  roster read went from `sqlite_autoindex_classes_1 (year=?)` (~16.7k rows
  scanned for 8,652 returned) to `idx_classes_term (year=? AND term=?)`.
- Dry run on the new code: `bootstrap … keyframes: 1` (was 22 and growing
  daily), coverage 100%.
- Forced keyframe in a dry run (`COUNT_KEYFRAME_HOURS=1`): 8,652 samples as
  before, but only **3** `count_latest` rows would be pushed (was 8,652).
- Real pull with the new code: 17,443 rows / 315 passes merged in 85 s, 0
  duplicate keys afterwards, and local `count_latest` for 2026-2 matches the
  cloud's exactly (8,652 rows, 0 mismatches).
- Verified 2026-09-28: the first scheduled keyframe on the runner after
  deployment (13:12:34 KST) rewrote 2 `count_latest` rows (was 8,652).

### Items 9–10: export churn, trend v2 (2026-09-27)

- Full export from the real catalog into a scratch directory: the 17 trend
  files went from ~460 MB to **7.9 MB**; the live file from 27.35 MB to
  343 KB (46 KB gzipped, was 189 KB). Export took 100 s → 82 s.
- Lossless: every window (16 archives + live) was re-derived with
  `_walk_samples` and compared with `decode_trend(_payload(...))` — 18
  windows, 0 mismatches, timestamps included. Against the frozen v1 files,
  w000/w007/w015 decode identically; w001 differs for one class
  (M3239.002000(015)) because that v1 file was generated from dense storage
  before the 2026-09-04 delta migration, which reads a class missing from a
  pass as "unchanged" rather than null — not an encoding difference.
- Found while exporting: `idx_classes_term` changed the order of rows that
  tie on (name, lt_no), rewriting all 28 term files with no content change.
  `db.search` now breaks ties explicitly (sbjt_cd, subh_cd, id — the order
  the old index delivered); re-export leaves every old term byte-identical.
- `explore-index.json`: content-identical re-export keeps the committed file
  and its `generated` stamp (verified by restoring it from git and exporting).
- Web: `web/tests` 16/16 pass (the two trend-window tests had been timing
  out on the 27 MB files). Screenshots with the browser in Europe/London show
  KST labels and tooltips, a time-scaled x axis where the 08-12~08-27 gap in
  w001 takes its real width, and paging back 15 windows leaves 4 in memory.
- Deployed: web b21e92e/ea598a3 built on GitHub Pages 2026-09-27; the live
  site (browser in America/Los_Angeles) loads the v2 live file (50 KB
  gzipped on the wire), labels KST, pages to the previous window, 0 errors.

### Item 11: client boot and search (2026-09-27)

- Repeat visit on the deployed site: app.js 60,809 B → 114 B (revalidated
  304); partials 90–137 B each; partials and app.js now fetched together.
- `searchLocal` result cache (full catalog, seats filter over all years,
  38,834 rows): five "더 보기" pages 154 ms → 0 ms; a name query plus five
  pages 540 ms → 109 ms. Full result order identical to the previous build
  for three queries (38,834 / 1,338 / 1 rows).
- Deferred: columnar/slim catalog files. The wire saving is ~23% (373 KB →
  286 KB gzipped for 2026-2) while every consumer of `termRows` reads the row
  objects; not worth the risk in this pass.

### Item 12: web features (2026-09-27, web 9400e09 · affa2f3 · 8e2e127 · 96bdff3)

- Share links: `#search/…`, `#class/…`, `#trend/…`; a search link reproduces
  the same result count and form state in a fresh page (test).
- Change feed: selection checked against an independent Python computation
  over the decoded live file for 3 h / 24 h / whole window × open/all.
- Seat alerts: a routed live payload moving a watched class from 0 to 3
  seats raises exactly one notification and a toast; the watch survives a
  reload. A concurrency bug (double alert when two checks overlap) was found
  by that test under load and fixed by serializing the checks.
- Dark mode + keyboard: result cards, timetable blocks and chips work by
  keyboard; the drawer is a modal dialog with focus return; theme cycles
  and persists (tests). `el()` had been dropping every aria-*/role attribute.
- web/tests: 24 passed on 3 consecutive runs; the deployed site (96bdff3)
  loads the trend page with 0 errors.

### Items 13–14: operations and CI (2026-09-27)

- `class-checker.update.timer` now `*-*-* 06,12,18:00:00`; reinstalling it
  replayed the missed 18:00 slot immediately (run succeeded, web 20860cb).
- Failure alerts verified end to end with a transient unit running
  `/bin/false` and `OnFailure=class-checker-alert@…`: the first failure
  opened issue #1 (time, result `exit-code`, status 1, local log path; no
  log text), the second commented on it; closed as a test. `--search` was
  replaced with a plain listing because the search index lags.
- First real backup: `turso-20260927-225918.db.xz`, 55.6 MB, 405 s,
  7,247,169 samples; `backup.timer` next Sun 2026-10-04 04:30.
- `update.service`: `TimeoutStartSec=45min`.
- CI: `.github/workflows/tests.yml` (bash -n on scripts, pytest offline);
  `dev` extra with pytest in uv.lock; `py-modules` lists every module.
- CI: the first tests.yml run failed at collection (`No module named
  'scraper'`: the plain `pytest` entry point does not put the root on
  sys.path); fixed with `[tool.pytest.ini_options] pythonpath`, next run
  179 passed. A collect-counts run on the new lock succeeded (coverage 100%).

### Items 15–16: docs drift, cleanup (2026-09-27)

- Fixed: `docs/crawl.html` slot default (10 → 60), `docs/maintenance.html`
  "backups are convenience" (count_samples cannot be re-collected) and the
  `cp … backups/` advice, `systemd/README.md` "collect.env edit is enough"
  (now a rollover checklist), hourly-schedule wording across the docs and
  AGENTS.md, the 2026-09-04 crawl-removal plan's open "pending" (closed with
  journal evidence), `changelog.py` (which runs write `data/logs/update_*`).
- Disabled `class-checker.cart@20260804.timer` and
  `class-checker.cart-cleanup@20260804.timer` (their window ended 08-05).
- Moved to `~/.backup/class-checker/attic/class-checker-attic-20260927.tar.xz`
  (8.9 MB, 414 entries, listing checked before deletion): the June request
  captures at the root, `data/classes.db` (stale SQLite catalog, still the
  default only for `DB_BACKEND=sqlite`), the scratch `cloud-collect*.db`, and
  406 `data/logs/update_*.log` files; removed the empty `_scrape/`,
  `.test-tmp/`.
- Side effect to note: `systemctl --user reset-failed` was run without a unit
  name and also cleared the failed flag of two unrelated user units
  (`snu-feed.service`, an IBus service); their journals are unchanged.

### Item 17: requests-only session mint — not pursued (2026-09-27)

One plain GET of the sugang entry page showed it runs `NetFunnel_Action`
(the site's traffic-queue system) before a session is usable. Playwright
executes that page as a browser would; minting cookies with plain requests
would skip the queue, i.e. sidestep the site's own load control during
registration peaks, and risk an IP block for the collector. The saving
would also be small: on the runner the cached Chromium install step takes
~1 s and the collect step is dominated by 866 page requests. Kept as is.

## Pending live checks

- DONE 2026-09-28: first scheduled keyframe on the runner after 975ff7e
  (pass 2026-09-28T13:12:34, full=1) rewrote 2 `count_latest` rows in the
  cloud (was 8,652 per keyframe).
- DONE 2026-09-28: scheduled publishes at 06:00 and 12:00 under the new
  timer both finished successfully (12:00: 38 passes pulled, drift check
  "already handled", current-term export).
- PENDING: first scheduled weekly backup (2026-10-04 04:30).
- PENDING (from 2026-10-01): pull the 2026-09-04 13:52–16:09 samples
  stranded in the quota-exhausted old cloud database, then retire its
  credential file.

### Follow-up: change feed paging and history (2026-09-28, web 63fb6f1 · f640150)

- Owner request: see more than the top 50, and look further back than the
  live window. The feed now appends 50 rows per '더 보기' and offers 3/7/14
  days and the whole semester, joining the archive windows it needs into one
  change-point series (window-edge re-statements dropped; a window that did
  not collect a metric carries its last value).
- A test checks 3 h / 24 h / 7 days / semester × open/all against an
  independent computation over the decoded windows; web/tests 25 passed ×3.
- Deployed site: 3 days 0.23 s, 7 days 0.51 s, 14 days 0.95 s, whole
  semester 2.3 s sequentially → 0.57 s after fetching all windows in parallel.
- Semantics to know: over the whole semester the start (8/4) falls in the
  cart-only window where 신청 was not collected, so "여석이 생긴 강좌" there
  compares against no starting value and lists every class now open.

### Follow-up: why 14 days loaded slower than the whole semester (2026-09-28)

- Cause: for a bounded period the feed learned where an archive chunk starts
  only by downloading it, so it walked back one chunk per round trip
  (14 days = 7 chunks in series); the whole semester already fetched all 16
  at once. The earlier 0.57 s semester figure was also measured after the
  14-day run had cached half its chunks.
- Fix: the class index carries `trendArchiveStarts` (first pass per chunk,
  epoch s); the feed picks the needed chunks up front and fetches them
  together (scraper 36db56c, web b4fcd6a · 51371cf). A dropped request under that
  parallel load ("Failed to fetch", seen once on the deployed site) is now
  retried up to three times instead of failing the feed.
- Cold-cache medians on the deployed site (fresh browser context per
  period, 3 runs, two rounds): sequential 3d 41–182 ms, 7d 126–394 ms,
  14d 354–611 ms; parallel 3d 55–81 ms, 7d 70–115 ms, 14d 169–182 ms,
  semester 553–586 ms.
- Also: the header title links to the site root (`./`), clearing any route.

### Follow-up: feed rows independent of the period (2026-09-28, web 02147b0)

- Owner report: the newest rows changed with the period chosen. Cause: rows
  were the net difference from the period's start, and '여석이 생긴 강좌' was
  ranked by that gain; a class could also drop out of a longer period
  (5 → 3 → 4 grew over 3 h, fell over 24 h).
- Now each row is the class's newest qualifying step between two consecutive
  passes, newest first; the period only bounds how far back it may lie.
  Checked on the deployed site: for 3 h / 24 h / 3 d / 7 d / 14 d / semester
  each shorter list is exactly the head of the next (open: 30, 92, 180, 813,
  1,663, 5,249 rows; all: 30, 92, 181, 869, 1,738, 7,131). A test also
  matches every row (time, class, seats before/after) against an
  independent computation.

### Follow-up batch (2026-09-28): scope, drift, mobile, alerts, tests, harness

- Export scope: scheduled runs export only the collected term
  (`--current`); the live trend resumes from a checkpoint in
  `data/trend_cache/` (59 s full replay → 2.3 s, identical output). A real
  `update.sh` run: ~45 s → 19 s. Measured split before: classes 1.6 s,
  explore 3.3 s, trend ~28 s of every run.
- Catalog drift: the collector records live/catalog differences
  (`roster_drift`, cloud table created before the push); `update.sh` crawls,
  re-seeds the term's cloud roster and marks it handled only for a
  persistent (≥3 passes), unhandled drift after the last registration/change
  window. First runner pass on 808bd0b recorded +2 (`375.803(042)`,
  `552.439(002)`); marked handled as the baseline (owner: no crawl this
  semester). `reseed-check` on real data: 8,652 classes / 7,898 slots,
  identical to local, 0.2 s.
- Mobile (iPhone 13, light/dark, local and deployed): 0 px horizontal
  overflow on every page; chart drawn at screen width (axis labels ~4 px →
  readable), tap/drag tooltip, theme toggle beside the title. Found and
  fixed: a route change left an open detail drawer covering the page.
- Alerts: watch every bookmarked class at once; option to skip classes on
  or clashing with the timetable (recorded, shown, not announced); clear all.
- Tests: server API (search, lookup, status, path traversal, curated write +
  token), changelog diff/log, CSV/XLSX export, drift/reseed/update hook,
  checkpoint, mobile, alert extensions, and a graduation-audit golden
  snapshot (200 specs × 4 synthetic transcripts; the "graduate" transcript
  passes for 138; the rest trace to catalog availability, and the three
  with no failing bar are the 수리통계 대체 rule shown as a ⚠ warning, not a
  bug). Coverage: server 32→59%, changelog 33→92%, export 29→97%;
  tests/ 211 passed, web/tests 34 passed.
- Harness: `python -m tools.measure` (11 read-only commands) replaces the
  ad-hoc scripts used in this pass; documented in maintenance.html.
