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
