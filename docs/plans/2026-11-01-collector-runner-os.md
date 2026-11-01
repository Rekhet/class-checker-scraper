# Collector runner OS failure, 2026-11-01

The repeated catalog failures occur before crawling: `ubuntu-latest` selected
Ubuntu 26.04, where the locked Playwright could not install Chromium. Pin the
two Chromium collectors to the previously successful `ubuntu-24.04` image.

## Observed evidence

- Base commit: `17c84e14c904e7baf2c81417dc9f075e55c2e692`.
- Catalog success [39961829655](https://github.com/Rekhet/class-checker-scraper/actions/runs/39961829655),
  2026-10-23 13:30 UTC, used `ubuntu-24.04`; 2027 spring had 4,929 classes.
- Catalog failure [40038226705](https://github.com/Rekhet/class-checker-scraper/actions/runs/40038226705),
  2026-10-23 23:11 UTC, reported `Playwright does not support chromium on ubuntu26.04-x64`.
- The same error appears in [41292705554](https://github.com/Rekhet/class-checker-scraper/actions/runs/41292705554),
  2026-11-01 09:20 UTC. Run `41252481984` reports image `ubuntu-26.04`.
- Counts [41292691140](https://github.com/Rekhet/class-checker-scraper/actions/runs/41292691140)
  succeeded with `active=false`. Installation and collection were skipped.
- Counts failure `38848350691` on 2026-10-16 redirected session minting to
  `altpage.snu.ac.kr/index_sugang_2026_winter.html` with no cookies. This is a
  separate site availability symptom; the available log does not prove its cause.
- Syllabus failure [40957367522](https://github.com/Rekhet/class-checker-scraper/actions/runs/40957367522),
  2026-10-29 UTC (October 30 KST), had 99 of 4,929 spring classes fail twice
  with HTTP 404 at `cc103ajax.action`. The existing over-2% guard refused the
  term upload. Subsequent runs `41127584380` and `41238035636` succeeded.
  No evidence justifies weakening this guard or changing the endpoint.

## Bounded correction

- Pin `collect-catalog.yml` and `collect-counts.yml` to `ubuntu-24.04`.
- Include image version and architecture in Chromium cache keys to prevent
  cross-image cache reuse. Keep the locked dependencies and installation command.
- Counts is exposed to the same compatibility risk when its window opens:
  it uses the same locked dependencies and Chromium installation command.
  The cited skipped counts pass does not reproduce that failure.
- No schema, collection window, retry, publication, or failure-policy change.
- Official [Playwright requirements](https://playwright.dev/python/docs/intro)
  currently list Ubuntu 24.04 as supported. Current documentation also lists
  26.04; it does not establish compatibility of the locked version on the
  failed runners. The successful and failed runner logs are the decisive evidence.

## Verification and deployment

- `actionlint .github/workflows/collect-catalog.yml .github/workflows/collect-counts.yml`: exit 0.
- `.venv/bin/python -m pytest -q tests/test_catalog_snapshot.py tests/test_cloud_collect.py tests/test_windows.py`: 36 passed, 8 subtests passed.
- `.venv/bin/python -m pytest -q tests`: 310 passed, 59 subtests passed
  after allowing localhost sockets. The initial sandbox run had 12 socket
  permission failures and 298 passes; those were environment restrictions.
- `bash -n scripts/*.sh refresh.sh serve.sh flush.sh` and `git diff --check`: exit 0.
- Independent document review: no blockers; clarified the inferred counts risk
  and recorded the targeted test command. Incident prose check: no warnings.
  Existing README punctuation warnings are outside this change.
- Configuration-only change; no new implementation-mirroring unit test.
- User approved deployment and actual collection on 2026-11-01
  (`class-checker/D-000063`). Fix `4a1d350` was fast-forwarded to `main`
  and pushed at 09:33 UTC. No force push or data deletion.
- Push CI [41293617125](https://github.com/Rekhet/class-checker-scraper/actions/runs/41293617125): success.
- Catalog [41293653161](https://github.com/Rekhet/class-checker-scraper/actions/runs/41293653161):
  success, 46 s, image `ubuntu-24.04`, Chromium installation and both terms
  collected. Cloud SELECT independently confirmed refreshed rows.
- Cloud `checked_at` (UTC): winter `2026-11-01T09:34:05`, spring
  `2026-11-01T09:34:12`. Both previously stopped at October 23, 13:31 UTC.
- Counts actual collection remains unverified: the configured enrollment
  window ended October 20. Cloud and local histories both end at
  `2026-10-20T23:51:55` KST. Do not force a window to manufacture evidence.
- Local catalog and public website have not been manually republished. At
  inspection the local snapshots still held 260/4,929 classes. The next normal
  `class-checker.update.timer` pass (06:00 KST November 2) must pull and publish
  the recovered snapshots. `web/` was not changed in this repair.
- Rollback: restore the preceding workflow configuration through a reviewed
  commit if the pin fails; do not remove failure guards or delete cloud data.

## Data gap and recovery

Read-only cloud queries were taken before and after the recovery pass; the
compressed old payloads were preserved locally before dispatch. Local evidence
(untracked, retain for audit): `.tmp/collector-recovery-before.json`,
`.tmp/collector-before-*.zlib`, `.tmp/collector-recovery-after.json`,
`.tmp/catalog-runs-recovery.json`, `.tmp/count-failures-recovery.json`.
Snapshot
records were compared by `(sbjt_cd, lt_no, subh_cd)`, with unique-key and
before + additions - removals = after checks. These are endpoint differences,
not counts of every event during the outage.

| Term | Before | Recovered | Added | Removed | Existing records changed |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026 winter | 260 | 262 | 2 | 0 | 240 |
| 2027 spring | 4,929 | 4,972 | 43 | 0 | 1 |

- Consecutive failed catalog attempts after the last success: 22, from
  October 23 23:11 UTC to November 1 09:20 UTC (Actions listing, 66 runs
  examined). Last observation to recovery: 8 days 20:02:47 for winter and
  8 days 20:02:46 for spring, approximately 212 hours.
- All 45 currently present additions were recovered. There were no removals
  between these two snapshots. This does not rule out transient removals
  during the interval. Existing snapshot rows had remained in the cloud.
- Winter's 240 changed existing records include volatile Excel values:
  cart 236, applied 218; these overlap. Other changed fields: professor 2,
  department 7, quota 11, quota_returning 11, room 4, slots 5. Spring's one
  existing changed record has quota and quota_returning differences.
- Grading maps differ for both terms; switchable maps differ for spring.
  Map differences can include new classes and are not additional independent
  counts of changed existing records.
- The system keeps one current catalog snapshot per term. Exact change times
  and intermediate states during the unobserved interval cannot be recovered
  from those snapshots. The number of transient changes is unknown.
- No counts samples were expected during this catalog outage: all configured
  count windows ended by October 20, before the OS failure began October 23.
  This finding concerns this outage, not completeness of all historical counts.
- Separate October 16 counts outage: both cloud and local `count_passes` have
  adjacent observations at 09:28:20 and 13:11:46 KST, a 3:43:26 gap. Actions
  lists 22 failures at 09:30–13:00 KST; the first (`38829850120`) reports
  HTTP 404 at `cc100InterfaceSrch.action`, while the last (`38848350691`)
  reports a no-cookie redirect/session failure. The two boundary logs show
  site access failures; they do not establish the site's underlying cause.
  Missed intra-gap count changes and their times cannot be reconstructed
  from later samples. Failure count is not a count of lost class rows because
  counts are deltas. This historical site failure is separate from the fixed OS issue.
- Syllabus target snapshots were refreshed November 1 at 05:55–06:18 KST
  with `missing=0` for fall, winter, and spring. The October 30 failed term
  was not uploaded; later successful runs recovered the latest attachment state.
