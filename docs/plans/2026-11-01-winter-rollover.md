# Winter collection rollover, 2026-11-01

The operator approved applying the winter rollover audit (`class-checker/D-000064`).
Runtime scope moves from 2026 fall to 2026 winter; frontend winter defaults
remain as deployed. The audit is `docs/audits/2026-11-01-winter-rollover.md`.

## Configuration

`collect.env` is canonical. COUNT_YEAR=2026, COUNT_SEM=winter,
COUNT_MODE=enrollment; UPCOMING_TERMS contains only 2027:spring.
Source: [official SNU schedule](https://ie.snu.ac.kr/notice/?mid=ent_under&mod=document&uid=6517)
and [Biological Sciences notice](https://biosci.snu.ac.kr/board/notice?bbsidx=25596&bm=v&page=1v).

| Window | Dates | Collector |
| --- | --- | --- |
| Preliminary registration | October 20–23 | Past local cart window; no expired timer armed |
| Registration | October 27–30, November 2–3 | GitHub counts every 10 minutes |
| Changes | November 11–13 | GitHub counts every 10 minutes |
| Cancellation | December 11–January 7 | GitHub counts every 10 minutes, including weekends |

January is in 2027. Registration weekends October 31/November 1 are excluded.
The first future active date is November 2. No observation date is falsified
or window broadened to make a Sunday runner collect. Historical missing
winter samples cannot be backfilled from current counts.

## Execution and verification

- Local pre-rollover database backed up to `.tmp/winter-rollover-before.db`.
- Configuration syntax: passed; 16 real-config date-boundary checks passed.
- Relevant existing tests: 92 passed, 22 subtests passed. Full suite:
  `.venv/bin/python -m pytest -q tests`: 310 passed, 59 subtests passed.
- Shell syntax and `git diff --check`: passed.
- Upcoming catalog target resolved to spring only; syllabus targets winter and spring.
- Local winter refresh completed as crawl run 478, November 1 19:49 KST:
  262 classes, 683 slots, 45 timeless; 2 additions and no removals.
- Preseed ID collision check: 0 conflicts. Cloud history before seeding:
  411,742 sample rows, 6,841 fall passes, 8,652 fall latest rows.
- Scoped cloud winter roster seeding completed: 0 -> 262 classes, 661
  distinct room-less slots. IDs had no conflicts. Postseed SELECT confirmed
  all preseed cloud sample/pass/latest aggregates unchanged.
- Local fall history counts also unchanged: 7,660 passes, 7,454,181 sample
  rows, 8,652 latest rows. No winter passes were synthesized on Sunday.
- Collector main commit `b0d3cda` deployed. CI run `41299404573` succeeded
  (310 tests, 59 subtests locally; same suite in Actions).
- Actual runner `41299420016` used `b0d3cda`, succeeded with `active=false`
  at November 1 10:53 UTC, as required for Sunday. This verifies deployment
  and the gate, not a counts crawl. No artificial pass was inserted.
- Independent review corrected operator instructions that still prescribed
  all-table migration. README/maintenance now require scoped term reseeding.
  Historical registration dates are described as configured windows, not samples.
- Full export completed; web commit `56e205d` contains only generated winter
  classes, classes/index.json, rooms-index.json, and explore-index.json.
  Index verified: winter current/262, fall past, exactly one current term.
  Web main push completed. Pages run `41299336419` succeeded; public HTTP
  GETs confirmed winter current/262, fall past, and 262 winter class rows.
  Verification artifact: `.tmp/winter-rollover-public-verification.json`.
- Pending actual in-window runner pass/cloud samples on or after November 2.

## Operational boundaries

No schema change, dense sample writes, history deletion, keyframe changes,
or weakening of collection failure guards. Reseed only the winter roster;
never use the all-table migrate script. Keep cloud count history untouched.
The local scheduled update remains merge/publish at 06:00/12:00/18:00 KST;
it does not automatically crawl winter catalog facts. Refresh locally and
reseed again if the winter roster changes. `web/` changes are generated data
only; existing uncommitted UI work must not be staged or deployed.

Failure handling: a crawl failure leaves the existing catalog or rolls back;
failed roster seeding blocks collector deployment; collector failures remain
loud. On Sunday, skip is expected and cannot prove actual collection success.
After deployment the next real active pass must show coverage and write samples.

## Handoff

Applied commits: scraper `b0d3cda`, public data `56e205d`. Existing uncommitted
UI work remains local. Dates are KST unless explicitly UTC. The next actual
counts pass on an active date (November 2) must still be checked for coverage,
cloud count_passes/count_samples, and local pull/publication. This pending
check is not a claim that a future run has succeeded.

Official-source comparison, real-config boundaries, local crawl output,
cloud roster/history queries, full offline suite, deployment CI and public
HTTP responses support the completed claims. Independent review corrected
all-table reseed instructions and historical-observation wording. Prose check
passed with no warnings. No further numerical estimates were used.
