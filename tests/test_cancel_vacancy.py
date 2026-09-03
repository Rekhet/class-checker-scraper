from __future__ import annotations

import sqlite3
import unittest

from scraper import db


def _conn():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    return raw


class SchemaMigrationTests(unittest.TestCase):
    def test_init_schema_adds_cancel_vacancy_to_existing_tables(self) -> None:
        raw = sqlite3.connect(":memory:")
        # pre-migration table shapes (no cancel_vacancy; indexed columns present)
        raw.execute(
            "CREATE TABLE classes (id INTEGER PRIMARY KEY, year TEXT,"
            " name TEXT, professor TEXT, department TEXT, grade TEXT,"
            " college TEXT, quota_returning INTEGER, cart INTEGER, room TEXT,"
            " language TEXT, status TEXT, grading TEXT, grading_switch TEXT)"
        )
        raw.execute("CREATE TABLE count_samples (year TEXT, term TEXT,"
                    " sbjt_cd TEXT, lt_no TEXT, ts TEXT)")

        db.init_schema(db._Conn(raw, "sqlite"))

        cls_cols = {r[1] for r in raw.execute("PRAGMA table_info(classes)")}
        smp_cols = {r[1] for r in raw.execute("PRAGMA table_info(count_samples)")}
        self.assertIn("cancel_vacancy", cls_cols)
        self.assertIn("cancel_vacancy", smp_cols)


class UpdateCountsTests(unittest.TestCase):
    def test_update_counts_persists_cancel_vacancy(self) -> None:
        raw = _conn()
        raw.execute(
            "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
            " lt_no, subh_cd, name) VALUES ('2026','U1','U2','T1','M1','001','000','c')"
        )
        raw.commit()

        ok = db.update_counts(
            raw, "2026", "U1", "U2", "M1", "001", "000",
            applied=29, quota=30, cancel_vacancy=1,
        )

        self.assertTrue(ok)
        row = raw.execute(
            "SELECT applied, cancel_vacancy FROM classes WHERE sbjt_cd='M1'"
        ).fetchone()
        self.assertEqual((row["applied"], row["cancel_vacancy"]), (29, 1))

    def test_none_leaves_existing_flag(self) -> None:
        raw = _conn()
        raw.execute(
            "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
            " lt_no, subh_cd, name, cancel_vacancy)"
            " VALUES ('2026','U1','U2','T1','M1','001','000','c',1)"
        )
        raw.commit()

        db.update_counts(raw, "2026", "U1", "U2", "M1", "001", "000", applied=30)

        row = raw.execute(
            "SELECT cancel_vacancy FROM classes WHERE sbjt_cd='M1'"
        ).fetchone()
        self.assertEqual(row["cancel_vacancy"], 1)


class SampleCountsTests(unittest.TestCase):
    def test_samples_carry_cancel_vacancy_during_enrollment(self) -> None:
        raw = _conn()
        raw.execute(
            "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
            " lt_no, subh_cd, name, applied, quota, cancel_vacancy)"
            " VALUES ('2026','U1','U2','T1','M1','001','000','c',29,30,1)"
        )
        raw.commit()

        db.sample_counts(raw, [("2026", "T1")], ts="2026-09-03T10:00:00",
                         collect_cart=False, collect_enrolled=True)

        row = raw.execute(
            "SELECT cancel_vacancy FROM count_samples WHERE sbjt_cd='M1'"
        ).fetchone()
        self.assertEqual(row["cancel_vacancy"], 1)

    def test_cart_only_pass_stores_null_flag(self) -> None:
        raw = _conn()
        raw.execute(
            "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
            " lt_no, subh_cd, name, cart, cancel_vacancy)"
            " VALUES ('2026','U1','U2','T1','M1','001','000','c',5,1)"
        )
        raw.commit()

        db.sample_counts(raw, [("2026", "T1")], ts="2026-09-03T10:00:00",
                         collect_cart=True, collect_enrolled=False,
                         collect_applied=False)

        row = raw.execute(
            "SELECT cart, cancel_vacancy FROM count_samples WHERE sbjt_cd='M1'"
        ).fetchone()
        self.assertEqual(row["cart"], 5)
        self.assertIsNone(row["cancel_vacancy"])


if __name__ == "__main__":
    unittest.main()
