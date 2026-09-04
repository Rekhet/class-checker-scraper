# Scheduled Update Without a Local Crawl

## Status

Implemented on 2026-09-04. Verified locally with two `PUBLISH_GIT=0` runs of
`scripts/update.sh` and the unit test suite (113 tests). Live verification of a
scheduled activation (the 11:00 timer run and a post-resume catch-up
activation) was still pending when this document was written.

## Context

The 10-minute 인원 collection moved to GitHub-hosted runners
(cron-job.org → `collect-counts.yml` → `scraper/cloud_collect.py` → cloud
Turso). The hourly local timer kept doing a full sugang crawl anyway, which
made the publication path depend on this laptop's network and session:

- On 2026-09-04 the 09:00 activation was replayed by `Persistent=true` two
  seconds after the lid opened (09:29:34 resume, 09:29:36 failure) and died on
  `Page.goto: net::ERR_INTERNET_DISCONNECTED`. Today's data reached the public
  repository only at 10:12, with the next slot.
- A crawl failure aborted the whole run under `set -e`, so cloud-collected
  counts that were already in the cloud database were not published either.

## Decision

The scheduled run merges and publishes; it does not crawl.

`scripts/update.sh` now runs `pull_counts` → `sync_counts` → `publish.sh full`.
The catalog crawl is opt-in behind `UPDATE_CRAWL=1`, which keeps
`UPDATE_COLLECTIONS` and its fail-closed cart guard for intentional runs.

`db.apply_latest_samples()` (exposed as `python -m scraper.sync_counts`) copies
the newest `count_samples` row for a term onto the catalog's volatile columns.
This is required, not cosmetic: the static export reads
applied/cart/enrolled/quota/cancel_vacancy off `classes`, which no longer moves
on its own without a crawl, so search rows would otherwise freeze at the last
crawl while the trend kept advancing. NULL sample columns (a metric outside its
collection window) leave the stored value alone, matching `db.update_counts`.

A missing `turso-remote.env`, or a failing pull, is now fatal for a
crawl-less run: it is the only source of fresh data, so the run fails loudly
instead of republishing yesterday's numbers. With `UPDATE_CRAWL=1` both
degrade to a warning, because the local crawl produced data of its own.

`scripts/wait-online.sh` runs as `ExecStartPre` for
`class-checker.update.service`. A persistent timer replays a missed activation
the moment the machine resumes, before NetworkManager has configured a link;
the script waits (best-effort `nm-online`, then reachability probes against
github.com and the Turso host parsed out of `turso-remote.env`) up to
`WAIT_ONLINE_TIMEOUT` seconds, and only the URL is read from that file.

## What no longer updates automatically

Only `count_samples` crosses the cloud boundary. Everything else in the catalog
now moves only when someone runs a crawl:

- new, renamed, cancelled (`폐강대상`) classes, professor, room, language,
  quota_returning, classification, and timing/slot data;
- 평가방식 and 평가방식 전환가능여부 (`grading`, `grading_switch`);
- the cloud catalog itself, which `cloud_collect.py` bootstraps from the cloud
  `classes` table seeded by `migrate_to_turso.py`. A class that never reaches
  the cloud roster is never counted there.

Run `UPDATE_CRAWL=1 ./scripts/update.sh` (or `make refresh`) when the timetable
changes. If the roster itself changed, re-seed the collector's cloud catalog
with `scraper/migrate_to_turso.py --src data/turso.db` under
`turso-remote.env`; that refills terms/classes/class_slots/crawl_runs and
leaves `count_samples` alone. `make migrate-remote` is a different database
(`prod-admin.env`).

## Verification

- `PUBLISH_GIT=0 ./scripts/update.sh` — 83 s wall clock (a crawling run took
  about 12 minutes), `pulled … cursor 2026-09-04T10:22:45`, `8649 classes
  synced from sample 2026-09-04T10:22:45`, exported trend `updated`
  2026-09-04T10:22:45.
- Post-sync catalog equals the newest sample for every sampled class
  (0 mismatching rows over the 8,652-class term).
- `tests/test_sync_counts.py` covers newest-sample overwrite, NULL retention,
  0-overrides-stale, unknown-class skip, term isolation, and the no-sample
  no-op. `tests/test_publish_script.py` covers the crawl-less scheduled path,
  the fail-closed missing-credentials path, and the `UPDATE_CRAWL=1` opt-in.

## Follow-up hardening (same day)

Removing the crawl exposed gaps the local pass had been masking:

- **`count_samples(ts)` index.** `pull_counts` walks the tail with
  `ts > cursor`, but `idx_samples_key` leads with the class identity, so every
  hourly pull full-scanned the cloud table — 1.72 s over 3.2M rows for an empty
  tail, growing linearly. Added to `db.SCHEMA` and created on the cloud
  database; the same probe now takes 0.10 s.
- **Staleness is a warning, not a failure.** A stalled cron-job.org dispatch
  produces no local error at all. `sync_counts` warns when a window is open and
  the newest sample is older than 45 minutes (180 in a slow window) and stays
  silent outside every window, because failing off-season would be noise. A
  failing *pull* remains fatal — that is an error, not merely old data.
- **수강취소 is collected, on a slow cadence.** The sugang landing page (read
  2026-09-04) puts 수강취소 at 09-08 ~ 10-20 and 폐강교과목 정원외신청 at
  09-17 ~ 09-22, both past the old 09-09 window end. `ENROLL_SLOW_WINDOWS`
  samples only the run landing in the first `ENROLL_SLOW_SLOT_MINUTES`
  (default 10) of an hour: the six-week tail is recorded at one sample an hour
  instead of six. The window arithmetic moved to `scraper/windows.py` so the
  publisher can evaluate it without importing Playwright.

Cloud `count_samples` is **not** pruned. It grows about 936k rows a day at the
fast cadence (3.18M rows over the first 3.4 days), which is a storage question
to answer without discarding history — the slow cadence cuts the 수강취소
stretch by 6×, and the rows themselves stay.

## Deferred

The timer window remains `Mon..Fri 09..20:00`, so nights and weekends still do
not publish while the cloud keeps collecting. Moving publication itself to
GitHub Actions — which would remove the laptop from the path entirely — is not
part of this change.
