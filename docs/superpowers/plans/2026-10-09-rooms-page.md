# Rooms Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `강의실` tab to the site: an empty-room finder (`#rooms`) and a per-room weekly schedule (`#room/<name>`), on the per-meeting rooms already exported in `slots[].room`.

**Architecture:** The scraper gains one room-name normalizer (`scraper/rooms.py`) and writes `web/data/classes/rooms-index.json` (every room seen since 2020, with its last term) on each full export. The browser normalizes the selected term's `slots[].room` with a JavaScript twin of the normalizer (both checked against one fixture), computes occupancy itself, and paints the weekly grid with the timetable's grid painter, extracted from `renderTTNow` into a shared function.

**Tech Stack:** Python 3.14 (scraper, pytest/unittest), vanilla JS single file `web/app.js`, HTML partials, `web/styles.css`, Playwright tests in `web/tests/`.

**Spec:** `docs/superpowers/specs/2026-10-09-rooms-page-design.md`

## Global Constraints

- `web/` is a separate public repository and **a push to it is a deploy**. Web commits stay local until the owner approves the local preview (Task 7).
- Room normalization rules (spec Section 1), identical in Python and JS: campus from the first char (`#` → 연건, `*` → 평창, else 관악, prefix stripped); split on `-`, drop empty segments, strip leading zeros of all-ASCII-digit segments (`"0"` stays `"0"`); building = first two segments joined when there are ≥ 3 segments and the second is 1–2 ASCII digits, else the first segment; name = all segments joined by `-`; key = `campus|name`; label = name for 관악, `"<campus> <name>"` otherwise.
- "Free" = no class meeting of that room overlaps [start, end) on that day. Pages must state that events/exams/rentals are unknown.
- Past-term-only rooms carry the `추정` badge with their last term (owner rule: inferred data is labeled).
- Follow existing code style: `el()` builder, `$()`, `_assertJson`/`_isRecord`/`_isString`/`_isInteger` validators, Korean UI copy, CSS tokens `--cp-*`.
- Every change that alters behaviour updates the docs listed in Task 7 before it is declared done (AGENTS.md).

## File Structure

| File | Responsibility |
|---|---|
| `scraper/rooms.py` (new) | `normalize_room`, `building_rank`, `build_rooms_index` |
| `tests/fixtures/room_names.json` (new) | raw → campus/building/name cases shared by Python and JS tests |
| `tests/test_rooms.py` (new) | normalizer + index builder tests |
| `scraper/export_json.py` | write `classes/rooms-index.json` in the full export |
| `web/app.js` | `paintWeekGrid` (extracted), rooms section (normalizer, index loader, occupancy, finder, weekly view), routes, room links |
| `web/partials/rooms.html` (new) | the page skeleton |
| `web/index.html`, `web/index-dev.html` | mount the partial |
| `web/styles.css` | rooms styles |
| `web/tests/test_rooms.py` (new) | page tests on real data |

---

### Task 1: Room-name normalizer (scraper)

**Files:**
- Create: `scraper/rooms.py`, `tests/fixtures/room_names.json`, `tests/test_rooms.py`

**Interfaces:**
- Produces: `rooms.Room(campus: str, building: str, name: str)` (NamedTuple, `.key -> "campus|name"`); `rooms.normalize_room(raw: str) -> Room | None`; `rooms.building_rank(building: str) -> tuple`.

- [ ] **Step 1: Write the fixture** `tests/fixtures/room_names.json`

```json
[
  {"raw": "24-102", "campus": "관악", "building": "24", "name": "24-102"},
  {"raw": "001-101", "campus": "관악", "building": "1", "name": "1-101"},
  {"raw": "1-101", "campus": "관악", "building": "1", "name": "1-101"},
  {"raw": "10-1-101", "campus": "관악", "building": "10-1", "name": "10-1-101"},
  {"raw": "010-1-101", "campus": "관악", "building": "10-1", "name": "10-1-101"},
  {"raw": "140-2-201", "campus": "관악", "building": "140-2", "name": "140-2-201"},
  {"raw": "2-101-1", "campus": "관악", "building": "2", "name": "2-101-1"},
  {"raw": "002-101-1", "campus": "관악", "building": "2", "name": "2-101-1"},
  {"raw": "16-M304-2", "campus": "관악", "building": "16", "name": "16-M304-2"},
  {"raw": "007-110-A", "campus": "관악", "building": "7", "name": "7-110-A"},
  {"raw": "103-B101", "campus": "관악", "building": "103", "name": "103-B101"},
  {"raw": "#12-102", "campus": "연건", "building": "12", "name": "12-102"},
  {"raw": "#012-102", "campus": "연건", "building": "12", "name": "12-102"},
  {"raw": "#17--108", "campus": "연건", "building": "17", "name": "17-108"},
  {"raw": "#017--108", "campus": "연건", "building": "17", "name": "17-108"},
  {"raw": "*102-103-218", "campus": "평창", "building": "102", "name": "102-103-218"},
  {"raw": "*100-101-201", "campus": "평창", "building": "100", "name": "100-101-201"}
]
```

- [ ] **Step 2: Write the failing tests** `tests/test_rooms.py`

```python
"""Room-name normalization and the rooms index (spec 2026-10-09-rooms-page-design)."""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from scraper import db, rooms

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "room_names.json"


class NormalizeTests(unittest.TestCase):
    def test_fixture_cases(self) -> None:
        for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
            with self.subTest(raw=case["raw"]):
                r = rooms.normalize_room(case["raw"])
                self.assertEqual((r.campus, r.building, r.name),
                                 (case["campus"], case["building"], case["name"]))
                self.assertEqual(r.key, f"{case['campus']}|{case['name']}")

    def test_blank_is_none(self) -> None:
        for raw in ("", "  ", "#", "-", None):
            self.assertIsNone(rooms.normalize_room(raw))

    def test_building_rank_orders_numerically(self) -> None:
        got = sorted(["10", "2", "10-1", "M", "9-2", "100"], key=rooms.building_rank)
        self.assertEqual(got, ["2", "9-2", "10", "10-1", "100", "M"])
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest -q tests/test_rooms.py`
Expected: FAIL — `ImportError: cannot import name 'rooms'`.

- [ ] **Step 4: Implement** `scraper/rooms.py`

```python
"""Room names in the catalog: campus, building, normalized name.

The Excel's 강의실 value carries the campus as a prefix (header legend
`강의실(동-호)(#연건, *평창)`); the 2020 terms and 2021 여름학기 zero-pad
segments (`001-101`) that every later term writes `1-101`. Normalizing both
makes one room one key across terms. The building is an INFERENCE used only
for grouping (spec docs/superpowers/specs/2026-10-09-rooms-page-design.md);
web/app.js `normRoom` is the JavaScript twin, and both are tested against
tests/fixtures/room_names.json.
"""
from __future__ import annotations

import re
from typing import NamedTuple

CAMPUS_MARK = {"#": "연건", "*": "평창"}
DEFAULT_CAMPUS = "관악"
_DIGITS = re.compile(r"[0-9]+")
_SUB_BUILDING = re.compile(r"[0-9]{1,2}")


class Room(NamedTuple):
    campus: str
    building: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.campus}|{self.name}"


def normalize_room(raw: str | None) -> Room | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    campus = CAMPUS_MARK.get(raw[0], DEFAULT_CAMPUS)
    body = raw[1:] if raw[0] in CAMPUS_MARK else raw
    parts = [(p.lstrip("0") or "0") if _DIGITS.fullmatch(p) else p
             for p in body.split("-") if p]
    if not parts:
        return None
    if len(parts) >= 3 and _SUB_BUILDING.fullmatch(parts[1]):
        building = f"{parts[0]}-{parts[1]}"
    else:
        building = parts[0]
    return Room(campus, building, "-".join(parts))


def building_rank(building: str) -> tuple:
    """Numeric building order: 2 < 9-2 < 10 < 10-1 < 100 < non-numeric."""
    nums = building.split("-")
    if all(_DIGITS.fullmatch(n) for n in nums):
        return (0, *[int(n) for n in nums])
    return (1, building)
```

- [ ] **Step 5: Run tests** — `uv run pytest -q tests/test_rooms.py` → PASS.

- [ ] **Step 6: Commit**

```bash
git add scraper/rooms.py tests/fixtures/room_names.json tests/test_rooms.py
git commit -m "feat: normalize room names (campus, padding, building)"
```

### Task 2: `rooms-index.json` export (scraper) + push

**Files:**
- Modify: `scraper/rooms.py` (add `build_rooms_index`), `scraper/export_json.py` (main, full-export branch, after the explore index)
- Test: `tests/test_rooms.py`
- Docs: `docs/database.html` (exported files), `docs/crawl.html` (#rooms paragraph)

**Interfaces:**
- Consumes: `normalize_room`, `building_rank` (Task 1).
- Produces: `rooms.build_rooms_index(conn) -> {"version": 1, "rooms": [[campus, building, name, last_year, last_term, terms_seen], ...]}` sorted by (campus order 관악/연건/평창, `building_rank`, name); file `web/data/classes/rooms-index.json` with a `generated` stamp.

- [ ] **Step 1: Failing test** (append to `tests/test_rooms.py`)

```python
def _catalog():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = db._Conn(raw, "sqlite")
    db.init_schema(conn)
    rows = [  # (id, year, term, rooms of its one meeting)
        (1, "2020", "U000200001U000300001", ["001-101"]),
        (2, "2025", "U000200002U000300001", ["1-101", "#12-102"]),
        (3, "2026", "U000200001U000300001", ["24-102"]),
        (4, "2026", "U000200002U000300001", [""]),
    ]
    for cid, year, term, rms in rows:
        raw.execute("INSERT INTO classes (id, year, term, shtm_fg, deta_shtm_fg, sbjt_cd,"
                    " lt_no, subh_cd, name) VALUES (?,?,?,?,?,?,?,?,?)",
                    (cid, year, term, term[:10], term[10:], f"C{cid}", "001", "000", "x"))
        for r in rms:
            raw.execute("INSERT INTO class_slots (class_id, day_index, period, start_time,"
                        " end_time, room) VALUES (?,0,1,'09:00','10:15',?)", (cid, r))
    raw.commit()
    return conn


class IndexTests(unittest.TestCase):
    def test_index_merges_padding_and_keeps_the_last_term(self) -> None:
        idx = rooms.build_rooms_index(_catalog())
        self.assertEqual(idx["version"], 1)
        self.assertEqual(idx["rooms"], [
            ["관악", "1", "1-101", "2025", "U000200002U000300001", 2],
            ["관악", "24", "24-102", "2026", "U000200001U000300001", 1],
            ["연건", "12", "12-102", "2025", "U000200002U000300001", 1],
        ])
```

- [ ] **Step 2: Run** — FAIL (`build_rooms_index` missing).

- [ ] **Step 3: Implement** (append to `scraper/rooms.py`)

```python
CAMPUS_ORDER = {DEFAULT_CAMPUS: 0, "연건": 1, "평창": 2}


def build_rooms_index(conn) -> dict:
    """Every room any stored term's meetings used, with its last term.

    Term codes sort chronologically within a year (1학기 < 여름 < 2학기 <
    겨울), so max((year, term)) is the latest term."""
    seen: dict[str, tuple[Room, set]] = {}
    for row in conn.execute(
            "SELECT DISTINCT c.year, c.term, s.room FROM class_slots s"
            " JOIN classes c ON c.id = s.class_id WHERE s.room <> ''").fetchall():
        room = normalize_room(row[2])
        if room is None:
            continue
        seen.setdefault(room.key, (room, set()))[1].add((row[0], row[1]))
    out = []
    for room, terms in sorted(seen.values(), key=lambda v: (
            CAMPUS_ORDER.get(v[0].campus, 9), building_rank(v[0].building), v[0].name)):
        year, term = max(terms)
        out.append([room.campus, room.building, room.name, year, term, len(terms)])
    return {"version": 1, "rooms": out}
```

- [ ] **Step 4: Wire the export.** In `scraper/export_json.py` add `import rooms` next to `import db`, and right after the `_export_explore(...)` call in `main()` (full export only):

```python
        _write_stamped(classes_dir / "rooms-index.json", rooms.build_rooms_index(conn))
```

- [ ] **Step 5: Run** `uv run pytest -q tests` → all pass.

- [ ] **Step 6: Real export + check**

```bash
DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db .venv/bin/python -m scraper.process_lock --timeout 120 -- .venv/bin/python scraper/export_json.py
python3 -c "import json;d=json.load(open('web/data/classes/rooms-index.json'));from collections import Counter;print(len(d['rooms']),Counter(r[0] for r in d['rooms']))"
```
Expected: 1,144 rooms — 관악 1,094, 연건 39, 평창 11 (the mock's count, 2026-10-09). File size tens of KB.

- [ ] **Step 7: Docs.** `docs/database.html`: list `classes/rooms-index.json` with its row shape. `docs/crawl.html` (#rooms): campus prefixes, 2020 zero padding, building inference, the index.

- [ ] **Step 8: Commit + push scraper; publish data**

```bash
git add scraper/rooms.py scraper/export_json.py tests/test_rooms.py docs/database.html docs/crawl.html
git commit -m "feat: export rooms-index.json (every room since 2020, last term)"
git push origin main
PUBLISH_COMMIT_MESSAGE="chore(data): add rooms-index.json" ./scripts/publish.sh full
```
(The site gains one file; nothing reads it yet.)

### Task 3: Extract the week-grid painter (web, no behaviour change)

**Files:**
- Modify: `web/app.js` — `renderTTNow` (≈1969–2095)

**Interfaces:**
- Produces: `paintWeekGrid(meetings, { dayN, startMin, endMin, conflicts = true, decorate = null, column = null }) -> { head: HTMLElement, ttx: HTMLElement }`. `meetings`: `[{ c, day, a, b }]` (minutes). `decorate(node, m, h)` adds content to a block after the default name/time/professor; `column(d, col, y)` appends extras to a day column, `y(min)` = px offset.

- [ ] **Step 1: Baseline** — `uv run pytest -q web/tests --deselect web/tests/test_grad_golden.py::GraduationAuditGoldenTests::test_audit_matches_the_golden_snapshot --deselect web/tests/test_change_feed.py::ChangeFeedTests::test_more_pages_and_older_periods_render` → 32 passed (2026-10-09).

- [ ] **Step 2: Extract.** Move the head/gutter/column/block code of `renderTTNow` into:

```js
// The proportional week grid (time gutter, hour/half-hour lines, packed class
// blocks). Shared by the timetable and the room schedule; the caller decides
// the meetings, the time range, and what else a block or column carries.
function paintWeekGrid(meetings, { dayN, startMin, endMin, conflicts = true,
                                    decorate = null, column = null } = {}) {
  const H = (endMin - startMin) / 60 * HOUR_PX;
  const y = (min) => (min - startMin) / 60 * HOUR_PX;
  const cols = `44px repeat(${dayN}, minmax(0, 1fr))`;
  const head = el("div", { className: "ttx-head" });
  head.style.gridTemplateColumns = cols;
  head.append(el("div", {}));
  for (let d = 0; d < dayN; d++)
    head.append(el("div", { className: "ttx-hd" }, DAYS[d],
      el("span", { className: "en" }, DAY_EN[d].toUpperCase())));
  const ttx = el("div", { className: "ttx" });
  ttx.style.gridTemplateColumns = cols;
  const gutter = el("div", { className: "ttx-gutter" });
  gutter.style.height = H + "px";
  for (let m = startMin; m <= endMin; m += 60) {
    const lab = el("div", { className: "ttx-hour" }, hhmm(m));
    lab.style.top = y(m) + "px";
    gutter.append(lab);
  }
  ttx.append(gutter);
  // Pack each day first and flag every class that has a clashing meeting, so all
  // of that lecture's boxes get outlined — even the non-overlapping ones on other
  // days/periods — making the conflicting lecture obvious across the whole grid.
  const packedByDay = [];
  const conflictKeys = new Set();
  for (let d = 0; d < dayN; d++) {
    const packed = packDay(meetings.filter((x) => x.day === d));
    packedByDay.push(packed);
    if (conflicts) for (const m of packed) if (m.lanes > 1) conflictKeys.add(classKey(m.c));
  }
  const hourCount = (endMin - startMin) / 60;
  for (let d = 0; d < dayN; d++) {
    const col = el("div", { className: "ttx-col" });
    col.style.height = H + "px";
    for (let i = 1; i < hourCount; i++) {   // hour gridlines
      const line = el("div", { className: "ttx-line" });
      line.style.top = (i * HOUR_PX) + "px";
      col.append(line);
    }
    for (let i = 0; i < hourCount; i++) {   // half-hour gridlines (dashed, fainter)
      const half = el("div", { className: "ttx-line half" });
      half.style.top = (i * HOUR_PX + HOUR_PX / 2) + "px";
      col.append(half);
    }
    for (const m of packedByDay[d]) {
      const c = m.c, conflict = conflictKeys.has(classKey(c));
      const h = Math.max(20, (m.b - m.a) / 60 * HOUR_PX - 2);
      const b = el("div", {
        className: "ttx-block" + (conflict ? " conflict" : "")
          + (c.timeChanged ? " changed" : "") + (c.removed ? " removed" : ""),
        title: `${c.name}\n${c.professor || "미정"} · ${c.sbjt_cd}(${c.lt_no})`
          + `\n${hhmm(m.a)}~${hhmm(m.b)}`
          + (c.timeChanged ? "\n⚠ 시간 변경됨" : "")
          + (c.removed ? "\n⚠ 폐강/삭제됨" : ""),
      });
      b.style.background = colorFor(c);
      b.style.top = (y(m.a) + 1) + "px";
      b.style.height = h + "px";
      b.style.left = `calc(${m.lane / m.lanes * 100}% + 1px)`;
      b.style.width = `calc(${100 / m.lanes}% - 2px)`;
      b.append(el("div", { className: "b-name" }, c.name));
      if (h > 34) b.append(el("small", {}, `${hhmm(m.a)}~${hhmm(m.b)}`));
      if (h > 52 && c.professor) b.append(el("small", { className: "ttx-prof" }, c.professor));
      if (decorate) decorate(b, m, h);
      b.addEventListener("click", () => openDetail(c));   // open the detail drawer
      activatable(b, `${c.name} ${DAYS[d]} ${hhmm(m.a)}~${hhmm(m.b)}`);
      col.append(b);
    }
    if (column) column(d, col, y);
    ttx.append(col);
  }
  return { head, ttx };
}
```

In `renderTTNow`, replace the moved code with (keeping the preview blocks via `column`):

```js
  const { head, ttx } = paintWeekGrid(meetings, { dayN, startMin, endMin,
    column: (d, col, y) => {
      for (const m of preview.filter((x) => x.day === d)) {
        const pb = el("div", { className: "ttx-block preview",
          title: `${hoverPreview.name} (미리보기)` }, "미리보기");
        pb.style.top = (y(m.a) + 1) + "px";
        pb.style.height = Math.max(20, (m.b - m.a) / 60 * HOUR_PX - 2) + "px";
        pb.style.left = "1px"; pb.style.width = "calc(100% - 2px)";
        col.append(pb);
      }
    } });
  grid.append(head);
```
and keep the existing `bodyWrap` / empty overlay / TBA code, appending `ttx` as before. Delete the now-unused locals (`H`, `cols`) in `renderTTNow` only if nothing else there uses them.

- [ ] **Step 3: Run the Step 1 command** → same 32 passed.

- [ ] **Step 4: Local web commit (no push)**

```bash
git -C web add app.js
git -C web commit -m "refactor: extract the week-grid painter from renderTTNow"
```

### Task 4: Room normalizer, index loader, occupancy (web)

**Files:**
- Modify: `web/app.js` (new section `// ---------- 강의실 (rooms) ----------` before the page router)
- Test: `web/tests/test_rooms.py` (new)

**Interfaces:**
- Consumes: `tests/fixtures/room_names.json` (Task 1), `rooms-index.json` (Task 2), `termRows(year, term)`, `toMin`.
- Produces: `normRoom(raw) -> {campus, building, name, key} | null`; `roomLabel(r)`; `roomParam(r)` / `parseRoomParam(param) -> key`; `buildingRank(b) -> array` (same order as Python); `roomsIndex() -> Promise<[{campus, building, name, key, year, term, terms}]>`; `roomMeetings(rows) -> Map<key, {room, meets:[{c, day, a, b, others:[label]}]}>`; `freeRooms(meetings, pool, q) -> [{room, current, next, last}]` where `q = {campus, building, day, a, b, past}`, `next = {a, c} | null`, `last = {year, term} | null`.

- [ ] **Step 1: Failing tests** `web/tests/test_rooms.py` — harness copied from `test_change_feed.py` (`_Quiet`, `setUpClass` serving `WEB_ROOT`, base `index.html#rooms`, `_run(fn)` collecting `pageerror`). First two tests:

```python
SCRAPER = WEB_ROOT.parent
FIXTURE = SCRAPER / "tests" / "fixtures" / "room_names.json"

    def test_js_normalizer_matches_the_shared_fixture(self) -> None:
        if not FIXTURE.exists():
            self.skipTest("scraper checkout not next to web/")
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        def steps(page):
            got = page.evaluate("(cs) => cs.map((c) => { const r = normRoom(c.raw);"
                                " return [r.campus, r.building, r.name]; })", cases)
            self.assertEqual(got, [[c["campus"], c["building"], c["name"]] for c in cases])
            self.assertIsNone(page.evaluate("normRoom('#')"))
        self._run(steps)

    def test_rooms_index_loads(self) -> None:
        def steps(page):
            n = page.evaluate("async () => (await roomsIndex()).length")
            self.assertEqual(n, len(json.loads((WEB_ROOT / "data/classes/rooms-index.json")
                                               .read_text())["rooms"]))
        self._run(steps)
```

- [ ] **Step 2: Run** — FAIL (`normRoom is not defined`).

- [ ] **Step 3: Implement** in `web/app.js`:

```js
// ---------- 강의실 (rooms) ----------
// Room names: twin of scraper/rooms.py (tested against the same fixture).
const ROOM_CAMPUS = { "#": "연건", "*": "평창" };
function normRoom(raw) {
  raw = String(raw ?? "").trim();
  if (!raw) return null;
  const campus = ROOM_CAMPUS[raw[0]] || "관악";
  const body = ROOM_CAMPUS[raw[0]] ? raw.slice(1) : raw;
  const parts = body.split("-").filter(Boolean)
    .map((p) => (/^[0-9]+$/.test(p) ? (p.replace(/^0+/, "") || "0") : p));
  if (!parts.length) return null;
  const building = parts.length >= 3 && /^[0-9]{1,2}$/.test(parts[1])
    ? `${parts[0]}-${parts[1]}` : parts[0];
  const name = parts.join("-");
  return { campus, building, name, key: `${campus}|${name}` };
}
const roomLabel = (r) => (r.campus === "관악" ? r.name : `${r.campus} ${r.name}`);
const roomParam = (r) => (r.campus === "관악" ? r.name : `${r.campus}:${r.name}`);
function parseRoomParam(param) {
  const i = param.indexOf(":");
  return i === -1 ? `관악|${param}` : `${param.slice(0, i)}|${param.slice(i + 1)}`;
}
// 2 < 9-2 < 10 < 10-1 < 100 < non-numeric (scraper/rooms.py building_rank)
function buildingRank(b) {
  const nums = b.split("-");
  return nums.every((n) => /^[0-9]+$/.test(n)) ? [0, ...nums.map(Number)] : [1, b];
}
function cmpRank(x, y) {
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] === undefined) return -1;
    if (y[i] === undefined) return 1;
    if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
  }
  return 0;
}
function _validateRoomsIndex(raw, source = "rooms index") {
  _assertJson(_isRecord(raw) && Array.isArray(raw.rooms), source, "rooms must be an array");
  return raw.rooms
    .filter((r) => Array.isArray(r) && r.length === 6 && r.slice(0, 5).every(_isString)
      && _isInteger(r[5]))
    .map(([campus, building, name, year, term, terms]) =>
      ({ campus, building, name, key: `${campus}|${name}`, year, term, terms }));
}
let _roomsIndex = null;
async function roomsIndex() {
  if (!_roomsIndex) {
    try {
      const r = await fetch("data/classes/rooms-index.json", { cache: "no-cache" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _roomsIndex = _validateRoomsIndex(await r.json());
    } catch (e) {
      console.warn(`rooms index unavailable — past-term rooms hidden (${e.message})`);
      _roomsIndex = [];
    }
  }
  return _roomsIndex;
}
// key -> {room, meets}: every timed meeting of one term's rows, per room. A
// meeting held in two rooms at once counts in both and names the other.
function roomMeetings(rows) {
  const out = new Map();
  for (const c of rows) for (const s of c.slots || []) {
    if (s.day_index == null || !s.start_time || !s.end_time || !s.room) continue;
    const rs = s.room.split("/").map(normRoom).filter(Boolean);
    for (const r of rs) {
      if (!out.has(r.key)) out.set(r.key, { room: r, meets: [] });
      out.get(r.key).meets.push({ c, day: s.day_index, a: toMin(s.start_time),
        b: toMin(s.end_time), others: rs.filter((o) => o.key !== r.key).map(roomLabel) });
    }
  }
  return out;
}
// Rooms with no meeting overlapping [q.a, q.b) on q.day. `pool` adds rooms
// seen only in other terms (q.past), which carry `last` and no meetings.
function freeRooms(meetings, pool, q) {
  const inScope = (r) => r.campus === q.campus && (!q.building
    || r.building === q.building || r.building.startsWith(q.building + "-"));
  const out = [];
  for (const { room, meets } of meetings.values()) {
    if (!inScope(room)) continue;
    const today = meets.filter((m) => m.day === q.day);
    if (today.some((m) => m.a < q.b && m.b > q.a)) continue;
    const after = today.filter((m) => m.a >= q.b).sort((x, y) => x.a - y.a)[0];
    out.push({ room, current: true, next: after ? { a: after.a, c: after.c } : null, last: null });
  }
  if (q.past) for (const r of pool) {
    if (!inScope(r) || meetings.has(r.key)) continue;
    out.push({ room: r, current: false, next: null, last: { year: r.year, term: r.term } });
  }
  return out.sort((x, y) => cmpRank(buildingRank(x.room.building), buildingRank(y.room.building))
    || (x.room.name < y.room.name ? -1 : x.room.name > y.room.name ? 1 : 0));
}
```

- [ ] **Step 4: Add the occupancy test** (independent computation in Python using `scraper/rooms.py`):

```python
YEAR, TERM = "2026", "U000200002U000300001"


def expected_free(day, a, b, campus, building="", past=True):
    sys.path.insert(0, str(SCRAPER))
    from scraper.rooms import normalize_room
    rows = json.loads((WEB_ROOT / f"data/classes/{YEAR}_{TERM}.json").read_text())
    busy, cur = set(), set()
    for c in rows:
        for s in c.get("slots") or []:
            if s.get("day_index") is None or not s.get("start_time") or not s.get("room"):
                continue
            for raw in s["room"].split("/"):
                r = normalize_room(raw)
                if not r:
                    continue
                cur.add(r)
                sa, sb = (int(t[:2]) * 60 + int(t[3:]) for t in (s["start_time"], s["end_time"]))
                if s["day_index"] == day and sa < b and sb > a:
                    busy.add(r.key)
    pool = json.loads((WEB_ROOT / "data/classes/rooms-index.json").read_text())["rooms"]
    keys = {r.key for r in cur if r.key not in busy}
    if past:
        keys |= {f"{p[0]}|{p[2]}" for p in pool} - {r.key for r in cur}
    def ok(k):
        camp, name = k.split("|")
        rr = normalize_room(("#" if camp == "연건" else "*" if camp == "평창" else "") + name)
        return camp == campus and (not building or rr.building == building
                                   or rr.building.startswith(building + "-"))
    return sorted(k for k in keys if ok(k))

    def test_free_rooms_match_an_independent_computation(self) -> None:
        queries = [(1, 18 * 60, 21 * 60, "관악", ""), (0, 9 * 60, 10 * 60 + 30, "관악", "24"),
                   (2, 13 * 60, 15 * 60, "연건", "")]
        def steps(page):
            for day, a, b, campus, bld in queries:
                got = page.evaluate(
                    """async ([day, a, b, campus, building]) => {
                      const m = roomMeetings(await termRows('2026', 'U000200002U000300001'));
                      return freeRooms(m, await roomsIndex(), {day, a, b, campus, building, past: true})
                        .map((f) => f.room.key).sort(); }""", [day, a, b, campus, bld])
                with self.subTest(q=(day, a, b, campus, bld)):
                    self.assertEqual(got, expected_free(day, a, b, campus, bld))
        self._run(steps)
```
(Sorting both sides makes the comparison order-free; ordering is covered by `buildingRank` in Task 1's Python twin and the screen test of Task 5.)

- [ ] **Step 5: Run** `uv run pytest -q web/tests/test_rooms.py` → PASS.

- [ ] **Step 6: Local web commit** — `git -C web add app.js tests/test_rooms.py && git -C web commit -m "feat: room normalizer, rooms index and occupancy"`

### Task 5: The 강의실 tab — finder screen (web)

**Files:**
- Create: `web/partials/rooms.html`
- Modify: `web/index.html`, `web/index-dev.html` (add `partials/rooms.html` before `partials/legal.html` in `data-partials`), `web/app.js` (finder + routing), `web/styles.css`
- Test: `web/tests/test_rooms.py`

**Interfaces:**
- Consumes: Task 4 functions, `dataIndex()`, `meta.cur` (`"year|term"`), `DAYS`, `hhmm`.
- Produces: `renderRooms(route, param)` (router entry), `roomsState = {term, campus, building, day, a, b, past}`, DOM ids `roomTerm roomCampus roomBuilding roomDay roomFrom roomTo roomNow roomPast roomsSummary roomsResults roomsFinder roomWeek`.

- [ ] **Step 1: Partial** `web/partials/rooms.html`

```html
<!-- 강의실 page: empty-room finder (#rooms) and one room's weekly schedule
     (#room/<name>, #room/<campus>:<name>). Built in app.js (renderRooms) from the
     selected term's class file and data/classes/rooms-index.json. -->
<div class="page" data-page="rooms" data-title="강의실">
<div class="tt-wrap rooms-wrap">
  <section id="roomsFinder">
    <div class="tt-title"><h2>빈 강의실 찾기 <span class="sub">Rooms</span></h2></div>
    <div class="rooms-form">
      <label>학기 <select id="roomTerm"></select></label>
      <label>캠퍼스 <select id="roomCampus">
        <option>관악</option><option>연건</option><option>평창</option></select></label>
      <label>건물 <input id="roomBuilding" placeholder="예: 24" size="6" autocomplete="off"></label>
      <label>요일 <select id="roomDay"></select></label>
      <span class="rooms-time"><label>시간 <select id="roomFrom"></select></label>~<select id="roomTo" aria-label="끝 시간"></select></span>
      <button id="roomNow" type="button" class="btn-ghost">지금</button>
      <label class="rooms-past"><input type="checkbox" id="roomPast" checked> 과거 학기 방 포함(추정)</label>
    </div>
    <div id="roomsSummary" class="rooms-summary" role="status" aria-live="polite"></div>
    <div id="roomsResults"></div>
  </section>
  <section id="roomWeek" class="hidden">
    <a href="#rooms" class="rooms-back">← 빈 강의실 찾기</a>
    <div id="roomHead" class="room-head"></div>
    <div id="roomGrid" class="tt-grid room-grid"></div>
    <div id="roomNote" class="rooms-note"></div>
  </section>
</div>
</div>
```

- [ ] **Step 2: Failing screen tests** (append to `web/tests/test_rooms.py`; base URL `index.html#rooms`):

```python
    def test_finder_lists_the_same_rooms_grouped_by_building(self) -> None:
        def steps(page):
            page.wait_for_function("() => /빈 강의실/.test(document.querySelector('#roomsSummary').textContent)")
            page.select_option("#roomDay", "1"); page.select_option("#roomFrom", str(18 * 60))
            page.select_option("#roomTo", str(21 * 60))
            page.wait_for_timeout(200)
            names = page.eval_on_selector_all("#roomsResults .room-row a", "ns => ns.map(n => n.dataset.key)")
            self.assertEqual(sorted(names), expected_free(1, 18 * 60, 21 * 60, "관악"))
            self.assertIn(f"빈 강의실 {len(names)}개", page.text_content("#roomsSummary"))
            self.assertEqual(page.locator("details.room-bld[open]").count(), 3)
        self._run(steps)

    def test_campus_change_clears_the_building(self) -> None:
        def steps(page):
            page.fill("#roomBuilding", "24")
            page.select_option("#roomCampus", "연건")
            self.assertEqual(page.input_value("#roomBuilding"), "")
            page.wait_for_timeout(200)
            self.assertGreater(page.locator("#roomsResults .room-row").count(), 0)
        self._run(steps)

    def test_a_term_without_rooms_says_so(self) -> None:
        def steps(page):
            page.select_option("#roomTerm", "2027|U000200001U000300001")
            page.wait_for_function("() => /공개되지 않았습니다/.test(document.querySelector('#roomsSummary').textContent)")
            self.assertEqual(page.locator("#roomsResults .room-row").count(), 0)
        self._run(steps)

    def test_phone_width_has_no_horizontal_scroll(self) -> None:
        def steps(page):
            page.set_viewport_size({"width": 390, "height": 800})
            page.wait_for_timeout(300)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self._run(steps)
```

- [ ] **Step 3: Run** — FAIL.

- [ ] **Step 4: Implement** in `web/app.js` (rooms section):

```js
const roomsState = { term: "", campus: "관악", building: "", day: 0, a: 9 * 60, b: 10 * 60, past: true };
let _roomsWired = false;
// Terms that have at least one class file; the default follows the site's semester.
async function roomTermOptions() {
  const idx = await dataIndex();
  return idx.terms.map((t) => ({ value: `${t.year}|${t.term}`, label: t.label }));
}
function _roomTimeOptions(sel, value) {
  sel.replaceChildren();
  for (let m = 8 * 60; m <= 23 * 60; m += 30)
    sel.append(el("option", { value: String(m), selected: m === value }, hhmm(m)));
}
async function wireRooms() {
  if (_roomsWired) return;
  _roomsWired = true;
  const terms = await roomTermOptions();
  const termSel = $("#roomTerm");
  for (const t of terms) termSel.append(el("option", { value: t.value }, t.label));
  roomsState.term = terms.some((t) => t.value === meta.cur) ? meta.cur : (terms[0]?.value || "");
  termSel.value = roomsState.term;
  const daySel = $("#roomDay");
  DAYS.slice(0, 6).forEach((d, i) => daySel.append(el("option", { value: String(i) }, d)));
  const now = new Date();
  roomsState.day = Math.min((now.getDay() + 6) % 7, 5);
  daySel.value = String(roomsState.day);
  _roomTimeOptions($("#roomFrom"), roomsState.a);
  _roomTimeOptions($("#roomTo"), roomsState.b);
  const rerun = () => renderRoomsFinder();
  termSel.onchange = () => { roomsState.term = termSel.value; rerun(); };
  $("#roomCampus").onchange = (e) => {   // another campus has other buildings
    roomsState.campus = e.target.value; roomsState.building = ""; $("#roomBuilding").value = ""; rerun(); };
  $("#roomBuilding").oninput = (e) => { roomsState.building = e.target.value.trim(); rerun(); };
  daySel.onchange = () => { roomsState.day = Number(daySel.value); rerun(); };
  $("#roomFrom").onchange = (e) => {
    roomsState.a = Number(e.target.value);
    if (roomsState.b <= roomsState.a) { roomsState.b = roomsState.a + 60; $("#roomTo").value = String(roomsState.b); }
    rerun(); };
  $("#roomTo").onchange = (e) => {
    roomsState.b = Number(e.target.value);
    if (roomsState.b <= roomsState.a) { roomsState.a = roomsState.b - 60; $("#roomFrom").value = String(roomsState.a); }
    rerun(); };
  $("#roomPast").onchange = (e) => { roomsState.past = e.target.checked; rerun(); };
  $("#roomNow").onclick = () => {
    const d = new Date(), day = (d.getDay() + 6) % 7;
    const m = Math.floor((d.getHours() * 60 + d.getMinutes()) / 30) * 30;
    const off = day > 5 || m < 8 * 60 || m >= 22 * 60;
    roomsState.day = Math.min(day, 5);
    roomsState.a = Math.max(8 * 60, Math.min(m, 22 * 60)); roomsState.b = roomsState.a + 60;
    daySel.value = String(roomsState.day);
    $("#roomFrom").value = String(roomsState.a); $("#roomTo").value = String(roomsState.b);
    rerun();
    if (off) $("#roomsSummary").prepend(el("div", { className: "rooms-note" },
      "지금은 수업 시간이 아닙니다 — 가장 가까운 시간으로 맞췄어요."));
  };
}
const _roomMeetCache = new Map();   // "year|term" -> roomMeetings(rows)
async function roomMeetingsFor(termKey) {
  if (!_roomMeetCache.has(termKey)) {
    const [y, t] = termKey.split("|");
    _roomMeetCache.set(termKey, roomMeetings(await termRows(y, t)));
  }
  return _roomMeetCache.get(termKey);
}
let _roomsRender = 0;
async function renderRoomsFinder() {
  const token = ++_roomsRender;
  const q = roomsState;
  const [meetings, pool] = await Promise.all([roomMeetingsFor(q.term), roomsIndex()]);
  if (token !== _roomsRender) return;
  const sum = $("#roomsSummary"), box = $("#roomsResults");
  box.replaceChildren();
  if (!meetings.size) {
    sum.textContent = "이 학기는 아직 강의실이 공개되지 않았습니다. 시간표가 공개되면 자동으로 채워집니다.";
    return;
  }
  const free = freeRooms(meetings, pool, q);
  const groups = new Map();
  for (const f of free) {
    if (!groups.has(f.room.building)) groups.set(f.room.building, []);
    groups.get(f.room.building).push(f);
  }
  const past = free.filter((f) => !f.current).length;
  sum.replaceChildren(
    el("div", { className: "rooms-count" },
      `${DAYS[q.day]} ${hhmm(q.a)}~${hhmm(q.b)} · ${q.campus} · 빈 강의실 ${free.length}개 · ${groups.size}개 건물`),
    el("div", { className: "rooms-note" }, "수업 기준입니다. 행사·시험·대관 예약은 알 수 없어요."
      + (past ? ` 과거 학기에만 있던 방 ${past}개는 추정입니다.` : "")));
  if (!free.length) { box.append(el("div", { className: "rooms-empty" }, "조건에 맞는 빈 강의실이 없습니다.")); return; }
  let i = 0;
  for (const [bld, list] of groups) {
    const det = el("details", { className: "room-bld", open: !!q.building || i < 3 },
      el("summary", {}, `${bld}동 `, el("span", { className: "cnt" }, `빈 방 ${list.length}`)));
    const ul = el("div", { className: "room-list" });
    for (const f of list) {
      const a = el("a", { href: "#room/" + encodeURIComponent(roomParam(f.room)), "data-key": f.room.key },
        f.room.name);
      const info = !f.current
        ? [el("span", { className: "rtag est" }, "추정"),
           ` 마지막 수업 ${f.last.year} ${(SEMESTER_LABEL[f.last.term] || "").split(" ")[0]}`]
        : [f.next ? `${hhmm(f.next.a)} 「${f.next.c.name}」 시작` : "이후 수업 없음"];
      ul.append(el("div", { className: "room-row" }, a, el("span", { className: "room-next" }, ...info)));
    }
    det.append(ul); box.append(det); i++;
  }
}
async function renderRooms(route, param) {
  await wireRooms();
  const week = route === "room" && param;
  $("#roomsFinder").classList.toggle("hidden", !!week);
  $("#roomWeek").classList.toggle("hidden", !week);
  if (week) await renderRoomWeek(param);
  else await renderRoomsFinder();
}
```

Router (`app.js` pages section): add `room: "rooms"` to `PAGE_FOR_ROUTE`; in `route()` add `if (page === "rooms") renderRooms(r, param);`. Until Task 6 lands, define a stub-free path by implementing Task 6 in the same commit if `renderRoomWeek` would otherwise be undefined — do NOT ship a reference to an undefined function: commit Tasks 5 and 6 together if needed.

`web/styles.css` (append):

```css
/* ---------- 강의실 ---------- */
.rooms-form { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; margin: 8px 0 12px; }
.rooms-form label { font-size: 12px; color: var(--cp-muted); display: inline-flex; gap: 6px; align-items: center; }
.rooms-time { display: inline-flex; gap: 4px; align-items: center; }
.rooms-summary { margin: 4px 2px 10px; }
.rooms-count { font-weight: 700; }
.rooms-note { font-size: 12px; color: var(--cp-muted); }
.rooms-empty { padding: 18px; text-align: center; color: var(--cp-muted); }
details.room-bld { background: var(--cp-paper); border: 1px solid var(--cp-line); border-radius: 10px; margin-bottom: 8px; }
details.room-bld > summary { cursor: pointer; padding: 10px 14px; font-weight: 700; }
details.room-bld .cnt { font-weight: 500; color: var(--cp-muted); font-size: 12px; }
.room-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0 18px; padding: 0 14px 10px 30px; }
.room-row { display: flex; gap: 10px; align-items: baseline; padding: 6px 0; border-top: 1px dashed var(--cp-gridLine); min-width: 0; }
.room-row a { font-weight: 700; color: var(--cp-ink); min-width: 84px; font-variant-numeric: tabular-nums; }
.room-next { color: var(--cp-muted); font-size: 12px; overflow-wrap: anywhere; }
.rtag.est { color: var(--cp-warn); }
.rooms-back { color: var(--cp-muted); font-size: 12px; }
.room-head { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: baseline; margin: 8px 0 14px; }
.room-head h2 { margin: 0; }
.ttx-window { position: absolute; left: 1px; right: 1px; border-radius: 6px; pointer-events: none;
  outline: 2px dashed var(--cp-accent); outline-offset: -2px; background: color-mix(in srgb, var(--cp-accent) 10%, transparent); }
.ttx-block .co { display: block; font-size: 10px; font-weight: 700; }
@media (max-width: 640px) { .room-list { grid-template-columns: 1fr; padding-left: 14px; } }
```

- [ ] **Step 5–6:** run `uv run pytest -q web/tests/test_rooms.py` → PASS (with Task 6 if committed together); local web commit `feat: 강의실 tab — empty-room finder`.

### Task 6: Weekly schedule + links from existing screens (web)

**Files:**
- Modify: `web/app.js` (`renderRoomWeek`, class detail `kv("강의실", …)` ≈1904, search row `rmeta` ≈1318)
- Test: `web/tests/test_rooms.py`

**Interfaces:**
- Consumes: `paintWeekGrid` (Task 3), Task 4/5 functions.
- Produces: `renderRoomWeek(param)`; `roomLinks(raw) -> DocumentFragment` (one `<a class="room-link">` per '/'-separated room).

- [ ] **Step 1: Failing tests**

```python
    def test_week_shows_the_rooms_meetings_and_shared_rooms(self) -> None:
        def steps(page):
            page.evaluate("location.hash = 'room/222-216'")
            page.wait_for_selector("#roomGrid .ttx-block")
            blocks = page.locator("#roomGrid .ttx-block:not(.preview)").count()
            rows = json.loads((WEB_ROOT / f"data/classes/{YEAR}_{TERM}.json").read_text())
            want = sum(1 for c in rows for s in c["slots"] if s.get("day_index") is not None
                       and s.get("start_time") and "222-216" in (s.get("room") or "").split("/"))
            self.assertEqual(blocks, want)
            self.assertIn("동시 사용", page.text_content("#roomGrid"))
            page.locator("#roomGrid .ttx-block").first.click()
            page.wait_for_selector("#detailTitle")
        self._run(steps)

    def test_class_detail_room_links_to_the_week(self) -> None:
        def steps(page):
            rows = json.loads((WEB_ROOT / f"data/classes/{YEAR}_{TERM}.json").read_text())
            c = next(x for x in rows if x.get("room") and "/" not in x["room"] and not x["room"][0] in "#*")
            # parseClassParam (app.js): "<year>|<term>/<sbjt_cd>(<lt_no>)"
            param = f"{YEAR}|{TERM}/{c['sbjt_cd']}({c['lt_no']})"
            page.evaluate("(p) => { location.hash = 'class/' + encodeURIComponent(p); }", param)
            page.wait_for_selector(".d-grid .room-link")
            page.locator(".d-grid .room-link").first.click()
            page.wait_for_function("() => location.hash.startsWith('#room/')")
        self._run(steps)
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

```js
// One link per room of a '/'-joined room string (class detail, search rows).
function roomLinks(raw) {
  const frag = document.createDocumentFragment();
  String(raw || "").split("/").filter(Boolean).forEach((part, i) => {
    if (i) frag.append("/");
    const r = normRoom(part);
    if (!r) { frag.append(part); return; }
    frag.append(el("a", { className: "room-link", href: "#room/" + encodeURIComponent(roomParam(r)),
      title: `${roomLabel(r)} 주간 사용표`, onclick: (e) => e.stopPropagation() }, part));
  });
  return frag;
}
async function renderRoomWeek(param) {
  const key = parseRoomParam(param);
  const [meetings, pool] = await Promise.all([roomMeetingsFor(roomsState.term), roomsIndex()]);
  const hit = meetings.get(key), known = pool.find((r) => r.key === key);
  const room = hit?.room || known || null;
  const head = $("#roomHead"), grid = $("#roomGrid"), note = $("#roomNote");
  head.replaceChildren(); grid.replaceChildren(); note.textContent = "";
  if (!room) { head.append(el("h2", {}, param)); note.textContent = "강의실을 찾을 수 없습니다."; return; }
  const [y, t] = roomsState.term.split("|");
  const termLabel = `${y} ${(SEMESTER_LABEL[t] || "").split(" ")[0]}`;
  const classes = new Set((hit?.meets || []).map((m) => classKey(m.c)));
  head.append(el("h2", {}, roomLabel(room)),
    el("span", { className: "rooms-note" },
      `${room.campus} · ${room.building}동 · ${termLabel} 수업 ${classes.size}개`
      + (known ? ` · 수업이 있던 학기 ${known.terms}개` : "")));
  // the schedule's own term select (spec): mirrors #roomTerm
  const sel = el("select", { "aria-label": "학기" });
  for (const o of $("#roomTerm").options)
    sel.append(el("option", { value: o.value, selected: o.value === roomsState.term }, o.textContent));
  sel.onchange = () => { roomsState.term = sel.value; $("#roomTerm").value = sel.value; renderRoomWeek(param); };
  head.append(sel);
  if (!hit) {
    note.append(`이번 학기(${termLabel}) 이 방에 배정된 수업이 없습니다. `);
    if (known) note.append(el("span", { className: "rtag est" }, "추정"),
      ` 마지막 수업 ${known.year} ${(SEMESTER_LABEL[known.term] || "").split(" ")[0]}`);
    return;
  }
  const ms = hit.meets;
  const dayN = Math.max(5, ...ms.map((m) => m.day + 1));
  const startMin = Math.min(9 * 60, Math.floor(Math.min(...ms.map((m) => m.a)) / 60) * 60);
  const endMin = Math.max(18 * 60, Math.ceil(Math.max(...ms.map((m) => m.b)) / 60) * 60);
  const q = roomsState;
  const { head: gh, ttx } = paintWeekGrid(ms, { dayN, startMin, endMin, conflicts: false,
    decorate: (node, m) => { if (m.others?.length) node.append(el("span", { className: "co" }, `+${m.others.join(", ")} 동시 사용`)); },
    column: (d, col, yOf) => {
      if (d !== q.day) return;
      const w = el("div", { className: "ttx-window", title: "찾기에서 고른 시간" });
      w.style.top = yOf(Math.max(q.a, startMin)) + "px";
      w.style.height = Math.max(0, yOf(Math.min(q.b, endMin)) - yOf(Math.max(q.a, startMin))) + "px";
      col.append(w);
    } });
  grid.append(gh, ttx);
  note.textContent = `점선 = 찾기에서 고른 시간 (${DAYS[q.day]} ${hhmm(q.a)}~${hhmm(q.b)}). 수업 블록을 누르면 강의 상세가 열립니다.`;
}
```

`packDay` (app.js) sorts and annotates the SAME meeting objects with `lane`/`lanes`, so `others` reaches `decorate` unchanged; the cached `roomMeetings` objects just carry stale lane fields until the next paint, which recomputes them.

Class detail (≈1904): replace `if (c.room) kv("강의실", c.room);` with
`if (c.room) kv("강의실", el("span", {}, roomLinks(c.room)));`

Search row (≈1318): build `rmeta` as
```js
el("div", { className: "rmeta" },
  `${sem ? sem + " · " : ""}${c.professor || "미정"} · ${c.department || "-"} · ${c.credits ?? "?"}학점`,
  ...(c.room ? [" · ", roomLinks(c.room)] : []), seats)
```
(keep `seats` as the same string it is today).

- [ ] **Step 4: Run** `uv run pytest -q web/tests` (with the two known unrelated failures deselected) → all pass.

- [ ] **Step 5: Local web commit** — `feat: room weekly schedule and room links`.

### Task 7: Owner preview, publish, docs

- [ ] **Step 1: Preview.** `make serve-prod` (static, exactly what ships); open `#rooms` and `#room/222-216` desktop and 390 px; take screenshots (Playwright) for the owner. **Stop and wait for the owner's approval.**
- [ ] **Step 2: Publish after approval:** `git -C web push` (the web commits of Tasks 3–6 and the data commit).
- [ ] **Step 3: Docs (scraper repo):** `docs/api.html` (static JSON list gains `classes/rooms-index.json`; `slots[].room`), the spec's Status → implemented with commit IDs, `docs/superpowers/plans/2026-09-29-per-meeting-rooms.md` Deferred → done, this plan's checkboxes; commit + push.
- [ ] **Step 4: Verify live:** fetch `https://<site>/#rooms` data files (`data/classes/rooms-index.json` 200) after Pages deploys; record the date.
