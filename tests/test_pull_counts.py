from __future__ import annotations

import sqlite3
import unittest

from scraper.pull_counts import merge_samples


SCHEMA = """
CREATE TABLE count_samples (
    year     TEXT NOT NULL,
    term     TEXT NOT NULL,
    sbjt_cd  TEXT NOT NULL,
    lt_no    TEXT NOT NULL,
    ts       TEXT NOT NULL,
    applied  INTEGER,
    cart     INTEGER,
    enrolled INTEGER,
    quota    INTEGER
)
"""


def _db(rows=()):
    conn = sqlite3.connect(":memory:")
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT INTO count_samples VALUES (?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    return conn


ROW_A = ("2026", "T1", "M100", "001", "2026-09-01T09:00:00", 5, None, None, 30)
ROW_B = ("2026", "T1", "M100", "001", "2026-09-01T09:10:00", 6, None, None, 30)
ROW_C = ("2026", "T1", "M200", "002", "2026-09-01T09:10:00", 9, None, None, 40)


class MergeSamplesTests(unittest.TestCase):
    def test_copies_missing_rows_and_reports_cursor(self) -> None:
        src = _db([ROW_A, ROW_B, ROW_C])
        dst = _db([ROW_A])  # already pulled (or locally collected) earlier

        result = merge_samples(src, dst)

        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["max_ts"], "2026-09-01T09:10:00")
        got = dst.execute(
            "SELECT COUNT(*) FROM count_samples"
        ).fetchone()[0]
        self.assertEqual(got, 3)

    def test_is_idempotent(self) -> None:
        src = _db([ROW_A, ROW_B])
        dst = _db()

        merge_samples(src, dst)
        result = merge_samples(src, dst)

        self.assertEqual(result["inserted"], 0)
        got = dst.execute("SELECT COUNT(*) FROM count_samples").fetchone()[0]
        self.assertEqual(got, 2)

    def test_since_ts_skips_already_pulled_rows(self) -> None:
        src = _db([ROW_A, ROW_B, ROW_C])
        dst = _db()

        result = merge_samples(src, dst, since_ts="2026-09-01T09:00:00")

        self.assertEqual(result["inserted"], 2)  # only the 09:10 rows
        got = dst.execute(
            "SELECT ts FROM count_samples ORDER BY ts"
        ).fetchall()
        self.assertEqual({t for (t,) in got}, {"2026-09-01T09:10:00"})

    def test_empty_source_keeps_cursor(self) -> None:
        src = _db()
        dst = _db()

        result = merge_samples(src, dst, since_ts="2026-09-01T09:00:00")

        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["max_ts"], "2026-09-01T09:00:00")


if __name__ == "__main__":
    unittest.main()
