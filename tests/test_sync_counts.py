from __future__ import annotations

import sqlite3
import unittest

from scraper import db
from scraper.sync_counts import main


YEAR = "2026"
TERM = "U000200002U000300001"


def _conn():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    return raw


def _add_class(conn, sbjt_cd, lt_no, **cols):
    values = {"applied": None, "cart": None, "enrolled": None,
              "quota": None, "cancel_vacancy": None}
    values.update(cols)
    conn.execute(
        "INSERT INTO classes (term, year, shtm_fg, deta_shtm_fg, sbjt_cd,"
        " lt_no, subh_cd, name, applied, cart, enrolled, quota, cancel_vacancy)"
        " VALUES (?,?,'U000200002','U000300001',?,?,'000','과목',?,?,?,?,?)",
        (TERM, YEAR, sbjt_cd, lt_no, values["applied"], values["cart"],
         values["enrolled"], values["quota"], values["cancel_vacancy"]))
    conn.commit()


def _add_sample(conn, sbjt_cd, lt_no, ts, **cols):
    values = {"applied": None, "cart": None, "enrolled": None,
              "quota": None, "cancel_vacancy": None}
    values.update(cols)
    conn.execute(
        "INSERT INTO count_samples (year, term, sbjt_cd, lt_no, ts, applied,"
        " cart, enrolled, quota, cancel_vacancy) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (YEAR, TERM, sbjt_cd, lt_no, ts, values["applied"], values["cart"],
         values["enrolled"], values["quota"], values["cancel_vacancy"]))
    db.record_pass(conn, YEAR, TERM, ts, applied=True, cart=True, enrolled=True)
    conn.commit()
    # count_latest is derived state: fold the new delta in, exactly as the
    # puller does after merging rows collected elsewhere.
    db.fold_pass_into_latest(conn, ts)
    conn.commit()


def _row(conn, sbjt_cd):
    return conn.execute(
        "SELECT applied, cart, enrolled, quota, cancel_vacancy FROM classes"
        " WHERE sbjt_cd=?", (sbjt_cd,)).fetchone()


class ApplyLatestSamplesTests(unittest.TestCase):
    def test_newest_sample_overwrites_the_catalog_counts(self) -> None:
        conn = _conn()
        _add_class(conn, "100.100", "001", applied=1, enrolled=1, quota=30,
                   cancel_vacancy=0)
        _add_sample(conn, "100.100", "001", "2026-09-04T09:00:00",
                    applied=5, enrolled=5, quota=30, cancel_vacancy=0)
        _add_sample(conn, "100.100", "001", "2026-09-04T10:00:00",
                    applied=9, enrolled=8, quota=30, cancel_vacancy=1)

        out = db.apply_latest_samples(conn, YEAR, TERM)

        self.assertEqual(out, {"ts": "2026-09-04T10:00:00", "updated": 1})
        self.assertEqual(tuple(_row(conn, "100.100")), (9, None, 8, 30, 1))

    def test_null_sample_columns_keep_the_stored_value(self) -> None:
        conn = _conn()
        # 장바구니 was collected earlier in the term; the enrolment-window
        # sample leaves that column NULL and must not erase it.
        _add_class(conn, "100.100", "001", applied=1, cart=12, enrolled=1,
                   quota=30, cancel_vacancy=1)
        _add_sample(conn, "100.100", "001", "2026-09-04T10:00:00",
                    applied=9, enrolled=8, quota=30)

        db.apply_latest_samples(conn, YEAR, TERM)

        self.assertEqual(tuple(_row(conn, "100.100")), (9, 12, 8, 30, 1))

    def test_zero_overrides_a_stale_positive_count(self) -> None:
        conn = _conn()
        _add_class(conn, "100.100", "001", applied=7, cancel_vacancy=1)
        _add_sample(conn, "100.100", "001", "2026-09-04T10:00:00",
                    applied=0, cancel_vacancy=0)

        db.apply_latest_samples(conn, YEAR, TERM)

        row = _row(conn, "100.100")
        self.assertEqual(row["applied"], 0)
        self.assertEqual(row["cancel_vacancy"], 0)

    def test_sample_for_an_unknown_class_is_skipped(self) -> None:
        conn = _conn()
        _add_class(conn, "100.100", "001", applied=1)
        _add_sample(conn, "100.100", "001", "2026-09-04T10:00:00", applied=4)
        _add_sample(conn, "999.999", "001", "2026-09-04T10:00:00", applied=4)

        out = db.apply_latest_samples(conn, YEAR, TERM)

        self.assertEqual(out["updated"], 1)

    def test_other_terms_are_left_alone(self) -> None:
        conn = _conn()
        _add_class(conn, "100.100", "001", applied=1)
        conn.execute(
            "INSERT INTO classes (term, year, shtm_fg, deta_shtm_fg, sbjt_cd,"
            " lt_no, subh_cd, name, applied) VALUES"
            " ('U000200001U000300001','2026','U000200001','U000300001',"
            "  '100.100','001','000','과목',1)")
        conn.commit()
        _add_sample(conn, "100.100", "001", "2026-09-04T10:00:00", applied=4)

        db.apply_latest_samples(conn, YEAR, TERM)

        other = conn.execute(
            "SELECT applied FROM classes WHERE term='U000200001U000300001'"
        ).fetchone()
        self.assertEqual(other["applied"], 1)

    def test_no_samples_is_a_no_op(self) -> None:
        conn = _conn()
        _add_class(conn, "100.100", "001", applied=3)

        out = db.apply_latest_samples(conn, YEAR, TERM)

        self.assertEqual(out, {"ts": None, "updated": 0})
        self.assertEqual(_row(conn, "100.100")["applied"], 3)


class CliTests(unittest.TestCase):
    def test_missing_scope_is_rejected(self) -> None:
        self.assertEqual(main(["--year", "", "--semester", ""]), 2)


if __name__ == "__main__":
    unittest.main()
