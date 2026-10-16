# Delta Storage for the 인원 추이 History

## Status

Implemented and deployed 2026-09-04. Verified against the live catalog (6.9M
dense rows migrated in place) and 140 unit tests. The collector now runs
against a new cloud database (`class-checker-jasonr`, Tokyo) seeded from the
local catalog; the exhausted one is left untouched until its quota resets on
2026-10-01, when the samples stranded in it (13:52~15:10 that day) can still be
pulled.

## Why

The collector wrote one `count_samples` row per class per pass: 8,652 rows
every 10 minutes, ~1.25M rows a day. On 2026-09-04 the cloud Turso plan ran out
of both budgets mid-semester —

```
rows read     762.8M / 500M   153%
rows written   11.2M /  10M   112%
```

— and the database began refusing reads AND writes, so collection stopped
entirely during a 수강신청변경 day. Measured on that day's own history, only
**0.9% of classes change between two consecutive passes**. Storing the other
99.1% again every 10 minutes is what made a semester unaffordable.

Pruning was rejected: the history is the product. The fix is to stop writing
what we already know.

## Model

- `count_passes(year, term, ts, applied, cart, enrolled)` — one row per pass.
  It is the trend's time axis (a pass where nothing moved still happened) and
  it records which metrics that pass looked at, so a closed 장바구니 window
  stays a gap in the chart instead of a forward-filled flat line.
- `count_samples` — unchanged shape, but a class appears only when one of its
  collected numbers changed. A row with **every** metric NULL is a tombstone:
  the class left the roster, and its series stops rather than being
  forward-filled forever.
- `count_latest(year, term, sbjt_cd, lt_no, ts, …)` — the materialised current
  value per class. It is the baseline a pass compares against, the source for
  the catalog overlay (`db.apply_latest_samples`, now a single
  `UPDATE … FROM`), and derived state: it is never shipped between databases,
  it is rebuilt from merged deltas by `db.fold_pass_into_latest`.

The published JSON is unchanged. `export_json` replays the samples over the
pass axis, forward-filling each metric, so the frontend needs no change.

## Verified

- A pass now writes ~50 rows instead of 8,652 (2026-09-04T16:37: 43 changes +
  7 tombstones). At the fast cadence that is ~7k rows a day against the old
  1.25M — the write budget stops being the binding constraint.
- Re-exporting the live catalog reproduces the previously published trend file:
  identical class set, and identical values on all 8.2M compared cells except
  three classes that exist locally but not in the cloud roster, whose gaps at
  cloud-collected passes are now forward-filled from their local samples.
- `db.backfill_delta_tables()` migrates a pre-delta catalog on first
  connection: 816 passes and 8,652 baselines derived from 6.9M dense rows, plus
  7 classes retired at the pass following their last sample. Verified against
  the raw history (0 mismatches over a 40-class sample); a fresh database is a
  no-op.
- `migrate_to_turso` now seeds `count_latest`, so a newly created collector
  database compares its first pass against real numbers instead of rewriting
  the whole roster.

## Deployment (2026-09-04)

1. Pushed the delta code — the runners check out `main`, so this had to land
   before the collector was re-enabled.
2. `turso db create class-checker` on the new account, then
   `migrate_to_turso.py --src data/turso.db` seeded 28 terms, 110,162 classes,
   115,636 slots and 8,652 count_latest baselines. The roster drift is gone as
   a side effect: the cloud now knows all 8,652 classes, not 8,649.
3. `gh secret set TURSO_DATABASE_URL/TURSO_AUTH_TOKEN`, then
   `gh workflow enable collect-counts`.
4. First runner pass: `bootstrap {'classes': 8652, 'latest': 8652}` ->
   `push {'pushed': 254, 'passes': 1, 'latest': 254}`. The local pull merged
   254 rows and 1 pass, the overlay refreshed all 8,652 classes, and the site
   published. Two consecutive cron-job.org dispatches then succeeded, so the
   local bridge timer was disabled again.

## Follow-up

- Stage 2 (catalog collection in the cloud, publishing from Actions) is
  unchanged by this work and still pending.
- `class-checker.update-counts.service.d/cloud-outage.conf` (COUNT_MODE=
  enrollment) stays on disk for the next outage; it only takes effect while
  that timer is enabled.
