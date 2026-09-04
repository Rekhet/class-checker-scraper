# Delta Storage for the 인원 추이 History

## Status

Implemented 2026-09-04. Verified against the live catalog (6.9M dense rows
migrated in place) and 138 unit tests. The remote collector is still disabled:
the cloud database is over quota until 2026-10-01, and a new database will be
created for it.

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

## Follow-up

- The remote collector stays disabled until the new cloud database exists;
  local collection (`class-checker.update-counts.timer`, COUNT_MODE=enrollment)
  is the bridge.
- Stage 2 (catalog collection in the cloud, publishing from Actions) is
  unchanged by this work and still pending.
