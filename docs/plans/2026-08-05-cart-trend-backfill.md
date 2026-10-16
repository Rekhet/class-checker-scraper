# 2026-08-05 16:00 cart trend backfill

## Status

Completed on 2026-08-06 (Asia/Seoul).

## Correction

The bounded cart worker scheduled for 16:00 did not produce a sample. It
waited for the full update's shared lock and then failed before scraping when
Playwright could not create its runtime artifact directory. The regular full
update intentionally excludes cart collection, so it did not replace the
missing sample.

Following the requested correction, the sample at
`2026-08-05T15:51:48` was copied to
`2026-08-05T16:00:00` for year 2026 and the fall term. This was an explicit
backfill, not a new scrape or an inferred change in enrollment state.

- Rows copied: 8,628
- Rows with cart values: 8,628
- `applied`, `cart`, `enrolled`, and `quota` matched the source row for all
  8,628 classes

The database operation was run transactionally under the shared process lock.

## Export and Git verification

The trend export now contains 184 timestamps, ending at
`2026-08-05T16:00:00`, across 8,628 class series. The four exported metrics
match the preceding sample for every class.

The private automation used the existing counts publishing path. The public
deployment repository contains one local commit, `11408a7` (`fix(data):
backfill 16:00 cart trend sample`), changing only the intended trend JSON.
It was not pushed; the hourly routine remains the push path.
