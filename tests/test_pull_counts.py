from __future__ import annotations

import sqlite3
import unittest

from scraper import db
from scraper.pull_counts import merge_samples


def _db(rows=(), passes=()):
    """A real catalog schema: the puller now touches count_samples,
    count_passes, and the derived count_latest together."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(db._Conn(conn, "sqlite"))
    conn.executemany(
        "INSERT INTO count_samples VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.executemany(
        "INSERT INTO count_passes (year, term, ts, applied, cart, enrolled) "
        "VALUES (?,?,?,?,?,?)", passes
    )
    conn.commit()
    return conn


ROW_A = ("2026", "T1", "M100", "001", "2026-09-01T09:00:00", 5, None, None, 30, 0)
ROW_B = ("2026", "T1", "M100", "001", "2026-09-01T09:10:00", 6, None, None, 30, 1)
ROW_C = ("2026", "T1", "M200", "002", "2026-09-01T09:10:00", 9, None, None, 40, None)


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

    def test_fetches_source_rows_in_per_ts_pages(self) -> None:
        """One huge SELECT over a remote libsql stream dies with EOF errors;
        the source must be read one ts group at a time."""
        src_raw = _db([ROW_A, ROW_B, ROW_C])
        selects: list[str] = []

        class _SpySrc:
            def execute(self, sql, params=()):
                if sql.lstrip().upper().startswith("SELECT"):
                    selects.append(sql)
                return src_raw.execute(sql, params)

        dst = _db()

        result = merge_samples(_SpySrc(), dst)

        self.assertEqual(result["inserted"], 3)
        self.assertTrue(any("DISTINCT" in s.upper() for s in selects))
        # every row-fetching SELECT is scoped to a single ts
        row_selects = [s for s in selects if "DISTINCT" not in s.upper()]
        self.assertTrue(row_selects)
        self.assertTrue(all("ts = ?" in s or "ts=?" in s for s in row_selects))

    def test_passes_and_derived_latest_come_across(self) -> None:
        # 09:10 records a pass in which M200 did not move: no sample row, but
        # the timestamp still belongs on the trend axis, and M200's current
        # value must survive as the forward-filled count_latest entry.
        src = _db([ROW_A, ROW_B],
                  passes=[("2026", "T1", "2026-09-01T09:00:00", 1, 0, 1),
                          ("2026", "T1", "2026-09-01T09:10:00", 1, 0, 1)])
        dst = _db()

        result = merge_samples(src, dst)

        self.assertEqual(result["passes"], 2)
        axis = [t for (t,) in dst.execute(
            "SELECT ts FROM count_passes ORDER BY ts").fetchall()]
        self.assertEqual(axis, ["2026-09-01T09:00:00", "2026-09-01T09:10:00"])
        latest = dst.execute(
            "SELECT ts, applied, quota, cancel_vacancy FROM count_latest "
            "WHERE sbjt_cd='M100'").fetchone()
        self.assertEqual(tuple(latest), ("2026-09-01T09:10:00", 6, 30, 1))

    def test_null_metrics_do_not_erase_the_derived_value(self) -> None:
        # A pass outside the 장바구니 window stores cart NULL; the last real
        # 장바구니 number must stay in count_latest rather than be nulled out.
        src = _db([("2026", "T1", "M100", "001", "2026-08-04T09:00:00",
                    5, 12, None, 30, None),
                   ("2026", "T1", "M100", "001", "2026-09-01T09:00:00",
                    7, None, 7, 30, 0)])
        dst = _db()

        merge_samples(src, dst)

        latest = dst.execute(
            "SELECT applied, cart, enrolled FROM count_latest").fetchone()
        self.assertEqual(tuple(latest), (7, 12, 7))

    def test_empty_source_keeps_cursor(self) -> None:
        src = _db()
        dst = _db()

        result = merge_samples(src, dst, since_ts="2026-09-01T09:00:00")

        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["max_ts"], "2026-09-01T09:00:00")


if __name__ == "__main__":
    unittest.main()
