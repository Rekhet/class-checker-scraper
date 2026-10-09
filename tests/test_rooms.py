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
