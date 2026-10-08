# Upcoming terms: catalog before the timetable (2026-10-08)

## Status

Remote since 2026-10-08 (same day as the first, local version): upcoming
terms are read on a GitHub runner and only pulled and published here — see
"Move to remote collection". The local crawl described next was the first
version and is gone from `update.sh`.

## Why

sugang started listing 2026 겨울학기 and 2027 1학기. The owner wants them
collected and on the site now, ahead of their timetables.

## Sampling (read-only, 2026-10-08 ~14:00 KST)

| | 2026 겨울 `U000200002U000300002` | 2027 1학기 `U000200001U000300001` |
|---|---|---|
| In the cc100ajax term list | yes | yes (all four 2027 terms) |
| Excel rows | 212 (past winters 237–260) | 3,952 (2026 1학기: 7,728) |
| Catalog columns (번호, 이름, 학점, 학년, 대학·학과, 이수구분, 정원, 언어, 상태) | 100% | 100% (1 row 정원 0) |
| 주담당교수 | 152 (72%) | 2,637 (67%) |
| 평가방식 sweeps | A~F 192 + S/U 20 = 212; 전환가능 40 | A~F 2,704 + S/U 1,248 = 3,952; 전환가능 991 |
| cc103 detail | 상대/절대평가, e-mail; no 강의계획 | same |
| 수업교시 / 수업형태 / 강의실 | 0% | 0% |
| Search-result timing | none | none |
| Counts | all 0 | all 0 |

Control: 2026 2학기 in the same session had times for 5,437/8,654 and rooms
for 4,813, so the gaps are sugang's, not the crawler's.

Cost of one catalog + grading read: 5 Excel requests per term. 2027 1학기
3.5 MB in 6.8 s; the first Excel after a fresh session took 61 s once
(interstitial + retry). Live runs: 12 s and 26 s per term.

## Decisions (owner, 2026-10-08)

- Collect both terms and publish them, with classes shown as 시간미정. No
  `web/` change: the site already renders time-less classes as 시간미정/TBA
  (list, detail, timetable TBA box, `timedOnly` filter, .ics export), and
  `config.js` keeps the default semester at 2026 2학기.
- Re-read them on every scheduled run (cheap; new classes appear the same
  day).
- A failure raises the ops alert.
- Cloud 10-minute collection stays on 2026 2학기; moving it is decided after
  수강취소 ends (2026-10-20), when sugang updates.

## What was built

- `collect.env` `UPCOMING_TERMS="2026:winter 2027:spring"`.
- `scripts/update.sh`: per entry, `refresh.sh --collect catalog,grading
  --no-search-timing --min-keep 0.9`; skips the collected term; one retry
  after 30 s; on success exports every term; on failure publishes anyway and
  exits 1 at the end (OnFailure → `notify-failure.sh`). A separate
  `UPCOMING_CRAWLED` flag selects the full export instead of setting
  `UPDATE_CRAWL=1`, which would also have disabled `--fail-if-stale`.
- `scraper/crawl.py --min-keep RATIO` (`refresh_all(min_keep=)`): downloads
  each term's Excel before the wipe and refuses one below RATIO × stored,
  recording the run as `error`; the checked Excel is reused for the rebuild.
  Found while planning: `fetch_excel` returns `b""` for a reply that looks
  like an empty term, and `refresh_all` commits the wipe before downloading,
  so without the guard one bad reply would publish a term empty.
- `refresh.sh` passes `--min-keep` through.

## Verification

- Tests: 3 `min_keep` tests (`tests/test_counts_pipeline.py`), 6 update.sh
  tests (`tests/test_publish_script.py`). Full suite: 262 passed; the 2
  failures are `web/` tests, below.
- Live crawl 14:49 KST: 2026 겨울 212 classes, 2027 1학기 3,952; `grading`
  NULL 0; 전환가능 40 / 991; 0 `count_samples`, 0 `class_slots` for either.
- Guard live: `--min-keep 1.5` on 2026 겨울 refused (`Excel has 212
  classes, 212 stored`), rc 1, term still 212 rows, crawl_runs 453 = error.
- Full `update.sh` 14:50 KST: both terms re-read (no changes), all 29 terms
  exported, web `cac0b2d` pushed (adds `2027_U000200001U000300001.json`).
- Second run 14:57 KST (`PUBLISH_PUSH=0`): 2027 1학기 session mint timed out
  (`Page.goto: Timeout 30000ms`) before any wipe; the run published and
  exited 1 as designed. The retry was added after this. The local web commit
  `2e42e96` is pushed by the next scheduled run.
- Scraper commit `665c93b` (per-meeting rooms, committed first): the GitHub
  collector meets the new schema only in its scratch SQLite. Runner passes on
  `665c93b` at 05:50Z and 06:00Z (14:50/15:00 KST) both succeeded.

## Found along the way (not changed)

- `web/tests/test_grad_golden.py` builds its synthetic transcripts from the
  newest year in `index.json`, which is now the partial 2027 catalog, so 189
  specs differ from the golden. The same test passes on the pre-publish data
  (`91a39c6`). Production is unaffected (`_gradRequired` keys on the batch
  year). Fix belongs in `web/` (pin the year or pick the year with the most
  classes) and needs the owner's go-ahead.
- `web/tests/test_change_feed.py::test_more_pages_and_older_periods_render`
  asserts unique row text; today's 2026 2학기 trend has two sections
  (`E43.107(004)`/`(005)`) with identical label, numbers, and time. Unrelated
  to the new terms; the test should compare class keys. Also `web/`.
- 2026-10-08 06:00 the scheduled update failed with `disk I/O error`; the
  system journal shows `No space left on device` at that minute (8.7 GB free
  by 14:00). The alert unit also failed (`notify-failure.sh` printed `EOF`),
  so no ops-alert issue was opened for it. Not investigated further; the alert
  path last worked on 2026-09-27 (issue #1).


## Move to remote collection (owner, 2026-10-08, same day)

The owner asked for the upcoming-term crawl to run remotely, with this
machine only pulling and publishing (scope: upcoming terms only; current-term
drift re-crawl, rollover, and 장바구니 stay local for now). The local crawl
above stays on until the remote one is verified.

Design:

- The runner cannot write upcoming classes into the cloud `classes` table:
  `reseed_roster` copies LOCAL class ids there, and runner-inserted rows would
  collide. Instead each term is one row of a new table `catalog_snapshots`
  (zlib JSON of the parsed Excel records + 평가방식 sets; 2027 1학기 ≈ 90 KB,
  a full 2학기 ≈ 315 KB). An unchanged term only updates `checked_at`, so a
  run costs one cloud row write per term.
- `.github/workflows/collect-catalog.yml` runs
  `python -m scraper.catalog_snapshot collect` at 05:20/11:20/17:20 KST
  (GitHub `schedule`) and on dispatch. Refuses (and fails the run) on an empty
  term, no 평가방식 tags, or < 90% of the previous snapshot's classes.
- `python -m scraper.catalog_snapshot pull` rebuilds each term whose digest
  differs from the last applied one, like a local crawl (classes, slots with
  rooms, 평가방식, change log, crawl_runs). It exits 3 when a snapshot has not
  been refreshed for 30 h (stopped schedule, including GitHub disabling it
  after 60 days without repository activity).
- `crawl.refresh_grading` was split so `grading_methods` /
  `grading_switchable` serve both paths.

Steps done:

- Cloud schema: `catalog_snapshots` created with
  `python -m scraper.catalog_snapshot init-remote` before pushing code that
  writes it; local `data/turso.db` via `init_schema`.
- Local dry run of `collect` against sugang: 2026 겨울학기 212 classes / 212
  graded, 2027 1학기 3,952 / 3,952, 7 s.
- Runner pass (dispatch, run 37738478543, commit `34f4a41`, 06:35Z):
  2026 겨울학기 212 classes / 212 graded, 6 KB, new; 2027 1학기 3,952 /
  3,952, 92 KB, new; run green.
- Pull into the local catalog (after the fix below): both terms rebuilt with
  change log `new=0 removed=0 changed=0`, i.e. identical to the local crawl;
  digests equal the local dry run (`8856d93f5489`, `4c3ce7305e9d`); grading
  NULL 0, 전환가능 40 / 991; crawl_runs 457, 458.
- Cutover: `update.sh` no longer crawls upcoming terms; it runs
  `catalog_snapshot pull --dest "$LOCAL_DB"` inside the cloud-credential
  subshell (exit 0 unchanged → current-term export, 4 rebuilt → full export,
  3 stale or other → publish, then exit 1). Live run 15:3x KST: 17 s, `applied
  []`, current-term export, web `49c12b2` pushed (with the held `2e42e96`).
- `pull` only considers `UPCOMING_TERMS` minus the counted term, so after a
  rollover the counted term's leftover snapshot neither overwrites the crawled
  catalog nor raises a stale alert.

### Incident: init_schema on the cloud (2026-10-08 ~15:40 KST)

The first `pull` ran with `turso-remote.env` sourced and opened the "local"
catalog with `db.connect()`, which follows `TURSO_DATABASE_URL` — the cloud.
`init_schema` then ran there: the per-meeting-room migration rebuilt the cloud
`class_slots` (now has `room`, 115,636 rows kept, `room=''`), and the pull
compared the cloud with itself (`applied []`). Nothing else was written. The
collector never reads `class_slots` and `reseed_roster` inserts room-less rows
(default `''`), so behaviour is unchanged; the cost is one rewrite of that
table (~116k rows) against the plan's write quota. Usage could not be checked
(`turso` CLI not logged in). Fix: `pull` opens `--dest` by path
(`open_local`, refuses a URL), with a regression test; AGENTS.md now states
that `db.connect()` is the cloud whenever the cloud credentials are sourced.

## Live verification after the cutover

- 2026-10-08 18:00 KST local update: `catalog snapshots: 2 term(s), applied
  []`, finished 18:00:47, exit 0.
- First GitHub `schedule` run of `collect-catalog`: 2026-10-08 15:41 UTC
  (00:41 KST), success, both terms `checked_at` refreshed. The 08:20 UTC
  (17:20 KST) slot did not fire on time — the run came 7 h 21 m late, after
  the 18:00 local update. GitHub cron is best-effort (the reason
  collect-counts is dispatched by cron-job.org); the 30 h stale bar absorbs
  this, but a cron-job.org dispatch would make the schedule reliable (owner
  action, not done).

Checked 2026-10-08 after the owner logged in the `turso` CLI: database
`class-checker` (`libsql://class-checker-jasonr.aws-ap-northeast-1.turso.io`)
is the one `turso-remote.env` points at; delete protection is on. October
usage on the starter plan: rows written 0.6M / 10M (6%, including the
`class_slots` rewrite), rows read 19.2M / 500M, storage 106 MB / 5 GB; resets
2026-11-01 09:00 KST.
