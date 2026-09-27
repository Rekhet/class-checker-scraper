# Optimization and Hardening Pass (2026-09-27)

## Status

In progress. Started 2026-09-27 from a read-only audit of the collector,
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
| 8 | Keyframe pushes only changed `count_latest` rows | done (keyframe push pending runner verification ~2026-09-28 13:12 KST) |
| 9 | Export: stop rewriting unchanged files (`generated` churn) | done |
| 10 | Trend change-point format + client decoder, LRU window cache, time-based X axis, KST timestamps | done |
| 11 | Client boot: cacheable assets, parallel partials, `loadMore` sort cache | done (catalog slimming deferred, see log) |
| 12 | Features: share URLs + detail→trend, change feed, vacancy alerts, dark mode + a11y | done |
| 13 | Ops: publish timer 06/12/18, `OnFailure` → GitHub issue, `TimeoutStartSec`, weekly backup | done |
| 14 | CI: test workflow, `dev` extra with pytest | done |
| 15 | Docs drift fixes and a semester rollover checklist | done |
| 16 | Cleanup: disable finished-window units, move unused files to attic | done |
| 17 | Experiment: requests-only session mint with Playwright fallback | pending |

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
- Pending: the first scheduled keyframe on the runner after deployment
  (~2026-09-28 13:12 KST) should log `push: … latest:` in single or double
  digits rather than 8,652.

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
