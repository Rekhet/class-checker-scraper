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
| 6 | Turso: `classes(year, term)` index, keyframe partial index, bootstrap copies only the latest keyframe | pending |
| 7 | Turso: `count_samples` UNIQUE key, `INSERT OR IGNORE` push/pull, pull reuses `db.fold_pass_into_latest` | pending |
| 8 | Keyframe pushes only changed `count_latest` rows | pending |
| 9 | Export: stop rewriting unchanged files (`generated` churn) | pending |
| 10 | Trend change-point format + client decoder, LRU window cache, time-based X axis, KST timestamps | pending |
| 11 | Client boot: cacheable assets, parallel partials, `loadMore` sort cache | pending |
| 12 | Features: share URLs + detail→trend, change feed, vacancy alerts, dark mode + a11y | pending |
| 13 | Ops: publish timer 06/12/18, `OnFailure` → GitHub issue, `TimeoutStartSec`, weekly backup | pending |
| 14 | CI: test workflow, `dev` extra with pytest | pending |
| 15 | Docs drift fixes and a semester rollover checklist | pending |
| 16 | Cleanup: disable finished-window units, move unused files to attic | pending |
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
