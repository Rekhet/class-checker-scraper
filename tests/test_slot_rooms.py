"""Per-meeting rooms: the Excel pairing, the class_slots migration, and the
past-term room backfill."""
from __future__ import annotations

import sqlite3
import unittest

from scraper import backfill_rooms, db, excel
from scraper.reseed_roster import reseed

YEAR, TERM = "2026", "U1U2"


def _db():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    return raw


def _class(conn, cid, code, *, lt_no="001", slots=()):
    conn.execute(
        "INSERT INTO classes (id, year, term, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,"
        " subh_cd, name) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, YEAR, TERM, "U1", "U2", code, lt_no, "000", code))
    for day, start, end in slots:
        conn.execute("INSERT INTO class_slots (class_id, day_index, period, start_time,"
                     " end_time) VALUES (?,?,1,?,?)", (cid, day, start, end))
    conn.commit()


def _rec(code, times, rooms, *, lt_no="001"):
    return {"sbjt_cd": code, "lt_no": lt_no, "room": excel._room(rooms),
            "slots": excel._meetings(times, rooms)}


def _rooms(conn, cid):
    return [tuple(r) for r in conn.execute(
        "SELECT day_index, start_time, end_time, room FROM class_slots"
        " WHERE class_id=? ORDER BY day_index, start_time, room", (cid,))]


class MeetingsTests(unittest.TestCase):
    def test_pairs_each_block_with_the_room_at_its_position(self) -> None:
        got = excel._meetings("월(12:00~14:50)/화(11:00~12:15)",
                              "3-213(무선랜제공)/3-116(무선랜제공)")
        self.assertEqual([(s["day_index"], s["start_time"], s["room"]) for s in got],
                         [(0, "12:00", "3-213"), (1, "11:00", "3-116")])

    def test_one_time_in_two_rooms_keeps_both(self) -> None:
        got = excel._meetings("화(12:30~13:45)/화(12:30~13:45)", "2-110/3-108")
        self.assertEqual([s["room"] for s in got], ["2-110", "3-108"])

    def test_misaligned_columns_use_a_single_room_or_none(self) -> None:
        one = excel._meetings("월(09:00~10:15)/수(09:00~10:15)", "38-422")
        self.assertEqual([s["room"] for s in one], ["38-422", "38-422"])
        two = excel._meetings("월(09:00~10:15)/수(09:00~10:15)/금(09:00~10:15)", "1-1/2-2")
        self.assertEqual([s["room"] for s in two], ["", "", ""])

    def test_blank_room_entries_and_timeless_rows(self) -> None:
        got = excel._meetings("월(09:00~10:15)/수(09:00~10:15)", " / ")
        self.assertEqual([s["room"] for s in got], ["", ""])
        self.assertEqual(excel._meetings("", "/ /"), [])


class SlotSchemaTests(unittest.TestCase):
    def test_old_class_slots_gain_a_room_column_and_keep_their_rows(self) -> None:
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.executescript(
            "CREATE TABLE class_slots (class_id INTEGER NOT NULL, day_index INTEGER,"
            " period INTEGER, start_time TEXT, end_time TEXT,"
            " UNIQUE(class_id, day_index, start_time, end_time));"
            "CREATE INDEX idx_slots_cell ON class_slots(day_index, period);"
            "INSERT INTO class_slots VALUES (1, 0, 1, '09:00', '10:15');")
        db.init_schema(db._Conn(raw, "sqlite"))
        self.assertEqual([tuple(r) for r in raw.execute("SELECT * FROM class_slots")],
                         [(1, 0, 1, "09:00", "10:15", "")])
        raw.execute("INSERT INTO class_slots VALUES (1, 0, 1, '09:00', '10:15', '2-110')")
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM class_slots").fetchone()[0], 2)
        self.assertIn("idx_slots_cell", [r[1] for r in raw.execute(
            "PRAGMA index_list(class_slots)")])

    def test_readers_see_one_slot_per_time_however_many_rooms(self) -> None:
        conn = _db()
        _class(conn, 1, "A")
        c = db._Conn(conn, "sqlite")
        for room in ("2-110", "3-108"):
            db.add_slot(c, 1, {"day_index": 1, "period": 4, "start_time": "12:30",
                               "end_time": "13:45", "room": room})
        found = db.search(c, year=YEAR, term=TERM, limit=None)
        self.assertEqual(len(found[0]["slots"]), 1)
        # one slot per meeting time; its rooms joined like classes.room
        self.assertEqual(found[0]["slots"][0]["room"], "2-110/3-108")
        looked = db.lookup(c, [(YEAR, TERM, "A", "001")])[0]["slots"]
        self.assertEqual([s["room"] for s in looked], ["2-110/3-108"])

    def test_each_meeting_carries_its_own_room_and_unknown_is_empty(self) -> None:
        conn = _db()
        _class(conn, 1, "A", slots=[(2, "09:00", "10:15")])       # no room known
        c = db._Conn(conn, "sqlite")
        db.add_slot(c, 1, {"day_index": 0, "period": 1, "start_time": "09:00",
                           "end_time": "10:15", "room": "3-213"})
        slots = db.search(c, year=YEAR, term=TERM, limit=None)[0]["slots"]
        self.assertEqual([(s["day_index"], s["room"]) for s in slots],
                         [(0, "3-213"), (2, "")])
        self.assertEqual(sorted(slots[0]),
                         ["class_id", "day_index", "end_time", "period", "room",
                          "start_time"])

    def test_reseed_sends_the_cloud_one_row_per_time(self) -> None:
        local, cloud = _db(), _db()
        _class(local, 1, "A")
        for room in ("2-110", "3-108"):
            local.execute("INSERT INTO class_slots VALUES (1, 1, 4, '12:30', '13:45', ?)",
                          (room,))
        local.commit()
        self.assertEqual(reseed(local, cloud, year=YEAR, term=TERM)["slots"], 1)


class ApplyTermTests(unittest.TestCase):
    def test_matching_timing_gets_rooms_and_the_class_room(self) -> None:
        conn = _db()
        _class(conn, 1, "A", slots=[(0, "09:00", "10:15"), (2, "09:00", "10:15")])
        out = backfill_rooms.apply_term(
            conn, [_rec("A", "월(09:00~10:15)/수(09:00~10:15)", "24-101/24-103")],
            year=YEAR, term=TERM)
        self.assertEqual(out["slots_roomed"], 1)
        self.assertEqual(_rooms(conn, 1), [(0, "09:00", "10:15", "24-101"),
                                           (2, "09:00", "10:15", "24-103")])
        self.assertEqual(conn.execute("SELECT room FROM classes").fetchone()[0],
                         "24-101/24-103")

    def test_a_timing_difference_leaves_the_slots_alone(self) -> None:
        conn = _db()
        _class(conn, 1, "A", slots=[(0, "09:00", "10:15")])
        out = backfill_rooms.apply_term(
            conn, [_rec("A", "화(09:00~10:15)", "24-101")], year=YEAR, term=TERM)
        self.assertEqual(out["timing_differs"], 1)
        self.assertEqual(_rooms(conn, 1), [(0, "09:00", "10:15", "")])
        self.assertEqual(conn.execute("SELECT room FROM classes").fetchone()[0], "24-101")

    def test_classes_missing_from_the_catalog_are_counted_not_added(self) -> None:
        conn = _db()
        _class(conn, 1, "A", slots=[(0, "09:00", "10:15")])
        out = backfill_rooms.apply_term(
            conn, [_rec("B", "월(09:00~10:15)", "24-101")], year=YEAR, term=TERM)
        self.assertEqual(out["unmatched"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0], 1)

    def test_a_timeless_row_only_sets_the_class_room(self) -> None:
        conn = _db()
        _class(conn, 1, "A")
        out = backfill_rooms.apply_term(conn, [_rec("A", "", "24-101")],
                                        year=YEAR, term=TERM)
        self.assertEqual((out["matched"], out["slots_roomed"], out["timing_differs"]),
                         (1, 0, 0))
        self.assertEqual(conn.execute("SELECT room FROM classes").fetchone()[0], "24-101")


if __name__ == "__main__":
    unittest.main()
