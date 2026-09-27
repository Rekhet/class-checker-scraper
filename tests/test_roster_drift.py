from __future__ import annotations

import json
import sqlite3
import unittest

from scraper import db, roster_drift
from scraper.reseed_roster import reseed


YEAR, TERM = "2026", "T1"
AFTER = {"CART_WINDOWS": "2026-08-04..2026-08-05",
         "ENROLL_WINDOWS": "2026-08-07,2026-09-01..2026-09-09"}


def _db():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    return raw


def _pass(conn, ts):
    db.record_pass(conn, YEAR, TERM, ts, applied=True, cart=False, enrolled=True)
    conn.commit()


class CompareTests(unittest.TestCase):
    def test_added_and_removed_by_stable_identity(self) -> None:
        added, removed = roster_drift.compare(
            [("A", "001"), ("B", "001")], [("A", "001"), ("A", "001"), ("C", "002")])
        self.assertEqual((added, removed), (["C(002)"], ["B(001)"]))

    def test_signature_is_stable_and_distinguishes(self) -> None:
        s = roster_drift.signature(["C(002)"], [])
        self.assertEqual(s, roster_drift.signature(["C(002)"], []))
        self.assertNotEqual(s, roster_drift.signature([], ["C(002)"]))


class RecordAndDecideTests(unittest.TestCase):
    def _seen(self, conn, n, *, added=("C(002)",), start=0):
        for i in range(n):
            ts = f"2026-09-20T10:{start + i:02d}:00"
            _pass(conn, ts)
            roster_drift.record(conn, year=YEAR, term=TERM, added=list(added),
                                removed=[], ts=ts)
        conn.commit()

    def test_no_drift_writes_nothing(self) -> None:
        conn = _db()
        self.assertIsNone(roster_drift.record(conn, year=YEAR, term=TERM, added=[],
                                              removed=[], ts="2026-09-20T10:00:00"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM roster_drift").fetchone()[0], 0)

    def test_persistent_new_drift_after_the_periods_triggers_a_crawl(self) -> None:
        conn = _db()
        self._seen(conn, 3)
        d = roster_drift.decide(conn, year=YEAR, term=TERM, today="2026-09-20", env=AFTER)
        self.assertTrue(d["crawl"], d)
        self.assertEqual(d["added"], ["C(002)"])

    def test_too_few_passes_do_not(self) -> None:
        conn = _db()
        self._seen(conn, 2)
        self.assertFalse(roster_drift.decide(conn, year=YEAR, term=TERM,
                                             today="2026-09-20", env=AFTER)["crawl"])

    def test_during_the_registration_periods_it_does_not(self) -> None:
        conn = _db()
        self._seen(conn, 5)
        d = roster_drift.decide(conn, year=YEAR, term=TERM, today="2026-09-05", env=AFTER)
        self.assertFalse(d["crawl"])
        self.assertIn("not over", d["reason"])

    def test_a_drift_gone_from_the_newest_pass_does_not(self) -> None:
        conn = _db()
        self._seen(conn, 3)
        _pass(conn, "2026-09-20T11:00:00")       # a later pass without drift
        d = roster_drift.decide(conn, year=YEAR, term=TERM, today="2026-09-20", env=AFTER)
        self.assertFalse(d["crawl"])
        self.assertIn("no longer present", d["reason"])

    def test_a_handled_drift_does_not_but_a_new_one_does(self) -> None:
        conn = _db()
        self._seen(conn, 3)
        sig = roster_drift.decide(conn, year=YEAR, term=TERM, today="2026-09-20",
                                  env=AFTER)["signature"]
        roster_drift.mark_handled(conn, year=YEAR, term=TERM, sig=sig,
                                  ts="2026-09-20T10:05:00")
        self.assertFalse(roster_drift.decide(conn, year=YEAR, term=TERM,
                                             today="2026-09-20", env=AFTER)["crawl"])
        self._seen(conn, 3, added=("C(002)", "D(001)"), start=10)
        d = roster_drift.decide(conn, year=YEAR, term=TERM, today="2026-09-20", env=AFTER)
        self.assertTrue(d["crawl"])
        self.assertEqual(d["added"], ["C(002)", "D(001)"])

    def test_sync_copies_rows_and_keeps_local_handled_at(self) -> None:
        cloud, local = _db(), _db()
        self._seen(cloud, 3)
        roster_drift.sync(cloud, local)
        sig = local.execute("SELECT signature FROM roster_drift").fetchone()[0]
        roster_drift.mark_handled(local, year=YEAR, term=TERM, sig=sig, ts="x")
        self._seen(cloud, 1, start=30)             # the cloud keeps counting
        roster_drift.sync(cloud, local)
        row = local.execute("SELECT passes, handled_at FROM roster_drift").fetchone()
        self.assertEqual((row[0], row[1]), (4, "x"))


class ReseedTests(unittest.TestCase):
    def _catalog(self, codes, start_id=1):
        conn = _db()
        for i, code in enumerate(codes):
            conn.execute(
                "INSERT INTO classes (id, year, term, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,"
                " subh_cd, name) VALUES (?,?,?,?,?,?,?,?,?)",
                (start_id + i, YEAR, TERM, "U1", "U2", code, "001", "000", code))
            conn.execute("INSERT INTO class_slots (class_id, day_index, period, start_time,"
                         " end_time) VALUES (?,0,1,'09:00','10:15')", (start_id + i,))
        conn.execute(
            "INSERT INTO classes (id, year, term, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,"
            " subh_cd, name) VALUES (999,'2025',?,?,?,'OLD','001','000','old')",
            (TERM, "U1", "U2"))
        conn.commit()
        return conn

    def test_only_the_term_is_replaced(self) -> None:
        cloud = self._catalog(["A", "B"])
        local = self._catalog(["A", "B", "C"], start_id=10)
        out = reseed(local, cloud, year=YEAR, term=TERM)
        self.assertEqual((out["before"], out["classes"], out["slots"]), (2, 3, 3))
        codes = [r[0] for r in cloud.execute(
            "SELECT sbjt_cd FROM classes WHERE year=? ORDER BY sbjt_cd", (YEAR,))]
        self.assertEqual(codes, ["A", "B", "C"])
        self.assertEqual(cloud.execute(
            "SELECT COUNT(*) FROM classes WHERE year='2025'").fetchone()[0], 1)
        self.assertEqual(cloud.execute("SELECT COUNT(*) FROM class_slots").fetchone()[0], 3)

    def test_a_much_smaller_crawl_is_refused(self) -> None:
        cloud = self._catalog([f"C{i}" for i in range(10)])
        local = self._catalog(["A"], start_id=100)
        with self.assertRaises(SystemExit):
            reseed(local, cloud, year=YEAR, term=TERM)
        self.assertEqual(cloud.execute(
            "SELECT COUNT(*) FROM classes WHERE year=?", (YEAR,)).fetchone()[0], 10)


class CollectorRecordsDriftTests(unittest.TestCase):
    def test_live_keys_come_back_from_a_counts_pass(self) -> None:
        from unittest.mock import patch
        from scraper import crawl

        live = [{"shtm_fg": "U1", "deta_shtm_fg": "U2", "sbjt_cd": s, "lt_no": "001",
                 "subh_cd": "000"} for s in ("A", "C")]
        conn = type("C", (), {"commit": lambda self: None})()
        with patch.object(crawl, "fetch_live_classes", return_value=live), \
             patch.object(crawl.db, "update_counts", side_effect=[True, False]):
            out = crawl.refresh_counts(conn, None, YEAR, TERM, collect_cart=False)
        self.assertEqual(out["live_keys"], [("A", "001"), ("C", "001")])
        self.assertEqual(json.dumps(roster_drift.compare([("A", "001"), ("B", "001")],
                                                         out["live_keys"])),
                         json.dumps([["C(001)"], ["B(001)"]]))


if __name__ == "__main__":
    unittest.main()
