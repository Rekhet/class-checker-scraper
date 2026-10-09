# Rooms page: empty-room finder + per-room weekly schedule (design, 2026-10-09)

## Status

Design approved section by section by the owner on 2026-10-09 (3 sections +
an interactive HTML mock on real 2026 2학기 data). Not implemented. Next: the
implementation plan (superpowers:writing-plans).

## Why

Per-meeting rooms are collected (`class_slots.room`, 2026-09-29) and exported
(`slots[].room`, 2026-10-09, scraper `a25542d`, web data `98196a1`), but the
site has no page that is about rooms: a room appears only as an attribute of a
class (search row, class detail, the 강의실 text filter). The owner wants a
page that answers "which rooms are free then?" and "what happens in this room
during the week?".

## Decisions (owner, 2026-10-09)

| Question | Decision |
|---|---|
| Purpose | Both on one page: find empty rooms; a room opens its weekly schedule |
| Room pool | Rooms with classes in the selected term **plus** rooms seen in any term since 2020, the latter badged `추정` with the last term seen |
| Time input | Day + start–end time (30-min steps) + a `지금` button |
| Results | Grouped by building, with a building filter |
| Placement | New top-nav tab `강의실` |
| Data approach | A: compute occupancy in the browser from the term file it already loads; export one small `rooms-index.json` for the pool |
| Campus | 연건 / 평창 must be handled (campus selector, labels) |

Defaults chosen without a question (stated to the owner): the page follows the
semester the site already has selected and has its own term select; a term
whose rooms are not published yet (2026 겨울학기, 2027 1학기 today) shows
"강의실 미공개"; "free" always means "no class scheduled" — events, exams and
rentals are unknown, and the page says so.

## Data facts this design rests on (measured 2026-10-09, `data/turso.db`)

- Raw `class_slots.room` values: 1,738 distinct across 2020–2026.
- Campus is a prefix **inside the value** (the Excel header legend
  `강의실(동-호)(#연건, *평창)`): no prefix = 관악 (1,668 raw), `#` = 연건 (59),
  `*` = 평창 (11). (An earlier statement that the values carry no marker was
  wrong.)
- Zero-padded segments (`001-101`) occur only in the 2020 terms and 2021
  여름학기; every later term writes `1-101`. Same rooms, so padding is
  normalized away. After normalization: **1,144 rooms** (관악 1,094, 연건 39,
  평창 11); 787 have classes in 2026 2학기.
- Shapes: `24-102`; sub-buildings `10-1-101`, `140-2-201`; suffixed rooms
  `2-101-1`, `16-M304-2`, `007-110-A`; 평창 `*102-103-218`; one 연건 oddity
  `17--108`.
- 2026 2학기: 7,898 timed slots, 7,154 with a room, 83 two-room slots; 10
  classes have a class room but `''` slot rooms (timing differed during the
  room backfill) — those meetings cannot be placed and are ignored.

## Section 1 — data (scraper)

**Room name normalization** (one definition, two implementations):

1. Campus from the first character: `#` → 연건, `*` → 평창, else 관악; strip it.
2. Split on `-`; strip leading zeros of each all-digit segment (`001` → `1`,
   `010` → `10`); drop empty segments (`17--108` → `17-108`).
3. Building (an inference, used only for grouping): the first segment, plus
   the second when the second is 1–2 digits (`10-1-101` → `10-1`,
   `140-2-201` → `140-2`); otherwise the first segment alone (`2-101-1` → `2`,
   `16-M304-2` → `16`, 평창 `102-103-218` → `102`).
4. Identity key: `campus|normalized name`; display: the name for 관악,
   `연건 12-102` / `평창 102-103-218` otherwise.

The Python implementation lives in the scraper (export); the JavaScript one in
`web/app.js`. Both are tested against one shared fixture
(`tests/fixtures/room_names.json`: raw → campus, building, name) so they cannot
drift.

**`web/data/classes/rooms-index.json`** (new, written by `export_json.py` on every
full export; under `data/classes/` because `scripts/publish.sh` stages only
`data/classes/`, `data/trend/` and `explore-index.json`): one entry per
normalized room —
`[campus, building, name, last_year, last_term, terms_seen]` — plus a
`generated` stamp. Built from `class_slots.room` of every stored term
(including '/'-joined slot rooms split apart). Expected size: tens of KB.
Validated in the browser like the other JSON files (a malformed file disables
the `추정` pool, never the page).

Occupancy is **not** exported: the browser derives it from the selected
term's class file (`slots[].day_index/start_time/end_time/room`), which the
timetable and search already load.

## Section 2 — screens (`web/`)

New partial `web/partials/rooms.html` (`data-page="rooms"`,
`data-title="강의실"`) — the top nav picks it up automatically. Routes:
`#rooms` (finder) and `#room/<name>` / `#room/<campus>:<name>` (weekly
schedule), added to `PAGE_FOR_ROUTE`.

**Finder (`#rooms`)**

- Controls: 학기 · 캠퍼스 (관악/연건/평창) · 건물 (text; `24` matches `24`
  and `24-*`, not `240`) · 요일 (월–토) · 시작~끝 (08:00–23:00, 30 min) ·
  `지금` · `☑ 과거 학기 방 포함(추정)` (on by default).
- `지금` fills today's weekday and now→+1 h, clamped to class hours; outside
  them it says "수업 시간이 아닙니다" and uses the nearest slot.
- Changing campus clears the building filter (another campus has other
  buildings; found while testing the mock).
- A room is free when none of its meetings that day overlaps [start, end).
- Summary line: day/time · campus · N free rooms · M buildings; a note that
  only classes are known, and how many free rooms are `추정`.
- Groups by building, ordered numerically (`10-1` after `10`); first three
  open, the rest collapsed; with a building filter, all open.
- Each room: name (link to its schedule) and either the next class that day
  after the window (`21:30 「회계원리」 시작`), `이후 수업 없음`, or
  `추정 · 마지막 수업 2025 1학기` for a pool-only room.
- A term without published rooms: "이 학기는 아직 강의실이 공개되지 않았습니다",
  no results.

**Weekly schedule (`#room/…`)**

- Back link to the finder (state kept), title (`24-101`, `연건 1-301`), meta
  (campus · building · classes this term · terms seen), term select.
- The grid looks and behaves like the timetable's: the grid painter in
  `renderTTNow` (`app.js:1969`) is extracted into a function that takes
  meetings and a container, used by both pages. Blocks show name, professor,
  time; a two-room meeting adds `+222-219 동시 사용`; the finder's window is a
  dashed overlay on its day. Clicking a block opens the existing class detail.
- No classes this term: "이번 학기 수업 없음 · 마지막 수업 … (추정)".

**Links from existing screens**: the 강의실 value in the class detail
(`app.js:1904`) and in search result rows (`app.js:1318`) becomes a link to
the room's schedule (each room of a '/' list separately).

**Mobile**: controls wrap; groups become one column; the grid fits the width
like the timetable. No horizontal page scroll at 390 px.

The interactive mock used for approval is not committed (it embeds ~700 KB
of data); its generator and template were scratch files.

## Section 3 — testing and rollout

**Python (`tests/`)**: normalization cases (campus, padding, building rule,
`17--108`); `rooms-index.json` content (last term, terms seen, 2020 padding
merged with later spelling); the shared fixture.

**Web (`web/tests/`, playwright like the existing suites)**: finder results
equal an independent computation in the test on real 2026 2학기 data (a few
fixed queries, incl. 연건); weekly grid blocks/times, two-room label, block →
class detail; links from class detail and search rows; unpublished-term and
no-classes states; campus change clears building; 390 px without horizontal
scroll; zero page errors; the existing timetable tests still pass after the
grid-painter extraction; the JS normalizer matches the shared fixture.

**Rollout** (a push to the web repository is a deploy):

1. Scraper: normalizer, `rooms-index.json` export, tests → commit + push. The
   site gains a file, no visible change.
2. Web: extract the grid painter (no behavior change), existing tests →
   local commit only.
3. Web: rooms tab, two screens, links, tests → local commit only.
4. Local preview (`make serve-prod`, desktop + phone) for the owner; **push
   the web repository only after the owner approves.**
5. Docs with the change: `docs/crawl.html` (rooms), `docs/database.html`
   (export files), `docs/api.html` (static JSON list), this spec's status, the
   per-meeting-rooms plan's "Deferred"; `.superpowers/` into `.gitignore`.

## Out of scope

- Building names and a map (no data; a map also needs coordinates and a key).
- Non-class bookings (events, exams, rentals).
- The two known unrelated `web/` test failures (`test_grad_golden` newest-year
  choice, `test_change_feed` row-text uniqueness) — tracked separately.
