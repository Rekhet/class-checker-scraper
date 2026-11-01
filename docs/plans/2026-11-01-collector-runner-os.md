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
- Pending: push the fix to `main`; no deployment or manual dispatch authorized yet.
- Pending: actual catalog runner pass, confirming Ubuntu 24.04, Chromium install,
  successful term collection and refreshed cloud `catalog_snapshots.checked_at`.
- Pending: an in-window counts pass and its cloud samples; off-season skips
  cannot establish collector recovery. Do not force a window to manufacture evidence.
- Rollback: restore the preceding workflow configuration through a reviewed
  commit if the pin fails; do not remove failure guards or delete cloud data.
