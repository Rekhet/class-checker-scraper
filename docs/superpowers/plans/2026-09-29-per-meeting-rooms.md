# Per-meeting rooms and the past-term room backfill (2026-09-29)

## Status

Code, local schema migration and the local backfill are done. Committed and
pushed to `main` on 2026-10-08 (full suite 256 passed that day). The
collector (GitHub runner) only meets the new schema in its scratch SQLite
(`bootstrap_local` → `init_schema`; it copies no `class_slots`), so the cloud
database is untouched; the first runner pass after the push is checked to be
green (see the upcoming-terms plan, 2026-10-08).

## Why

An empty-room lookup ("which 24동 rooms are free Tuesday 18:00–21:00") showed
two gaps in the catalog:

- Only 2026 2학기 had rooms at all (4,806 of 8,652 classes); every earlier term
  had `room` NULL because the column was added after those terms were crawled.
  A room pool built from past terms was therefore impossible.
- `classes.room` is a deduplicated list, so for the 199 multi-room classes of
  2026 2학기 (`200-3220/200-1024`) the room each meeting uses was lost.

## Decisions (owner, 2026-09-29)

- Store the room per meeting (`class_slots.room`) and collect it for past
  terms by re-reading their Excel exports.

Made during implementation:

- The Excel `강의실(동-호)` column is positionally aligned with `수업교시`
  (one entry per `/`-separated block). Checked on `data/sample_excel.bin`
  (2026 여름학기): 356/356 rows aligned. A block repeated with two rooms is
  the class using both rooms at once, so `room` is part of the UNIQUE key.
  Misaligned rows fall back to the single distinct room or `''`, never a
  guess.
- `room` is `NOT NULL DEFAULT ''` so INSERT OR IGNORE still dedupes
  room-less (search-recovered) slots.
- Timing readers (`search`/export, `lookup`, `reseed_roster`) select
  DISTINCT day/start/end: the exported JSON is unchanged and the cloud
  `class_slots` keeps its room-less shape (no cloud migration, no extra
  cloud writes).
- Past terms are filled by `scraper/backfill_rooms.py`, not by a catalog
  rebuild, so each old term's roster stays as it was crawled. Slots are
  rewritten only when the Excel meeting times equal the stored ones.

## Verification

- Tests: `tests/test_slot_rooms.py` (pairing, migration, DISTINCT readers,
  reseed, backfill rules); full suite 256 passed.
- Migration on a copy of `data/turso.db` (libsql): 0.2 s, 115,636 slot rows
  kept with `room=''`, `idx_slots_cell` recreated, idempotent.
- Backfill run on `data/turso.db` 2026-09-29 09:53 KST, all 28 stored terms
  (2020 1학기 – 2026 겨울학기), exit 0. A pre-change copy of the database
  was kept for comparison.
  - 2020–2025, all 24 terms: every Excel row matched, `timing_differs` 0.
  - 2026 1학기: 1 Excel row not in the catalog (not inserted).
  - 2026 여름학기: 1 timing difference (`T2184.000200(001)`), slots left alone.
  - 2026 2학기: 2 Excel rows not in the catalog and 8 timing differences.
    The catalog is from the 2026-09-04 crawl, so these are timetable changes
    since then. Their slots were left alone and still have `room=''`.
  - 2026 겨울학기: empty Excel.
  - Totals: 115,636 slot rows became 117,336. The 1,700 extra rows are
    meetings held in two rooms at once. 108,040 slot rows have a room, with
    1,738 distinct rooms across all terms.
  - 100 classes of 2026 2학기 have a different `classes.room` than the
    2026-09-04 crawl recorded (e.g. `222-B204` → `222-422`). The backfill
    writes today's Excel room, so these reassignments reach the site at the
    next publish of that term, without an `UPDATE_CRAWL`.
  - 24동 has the same 12 rooms in every term (11–21 terms each), so for 24동
    the past-term pool adds no room that 2026 2학기 lacks. The Tue/Wed
    18:00–21:00 answers are unchanged with per-meeting rooms.

## Deferred

- Time-picker empty-room search and a room pool that lists rooms seen in past
  terms (labelled with the last term seen, as an estimate). Needs the slot
  room in the export and a `web/` change, which is out of this scope.
- `server.py` admin stats count `class_slots` rows, so a two-room meeting
  counts twice there.
