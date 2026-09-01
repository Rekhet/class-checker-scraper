from __future__ import annotations

import sqlite3
import unittest

from scraper import db


class _CountingConn:
    """sqlite3 wrapper that counts INSERT statements sent to execute()."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw
        self.insert_statements = 0

    def execute(self, sql: str, params=()):
        if sql.lstrip().upper().startswith("INSERT"):
            self.insert_statements += 1
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, seq):
        raise AssertionError(
            "row-at-a-time executemany is a per-row network round trip on a "
            "remote libSQL connection; use db.insert_chunked instead"
        )

    def commit(self) -> None:
        self._raw.commit()


class SampleCountsBatchingTests(unittest.TestCase):
    def _conn(self, n_classes: int) -> _CountingConn:
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        db.init_schema(db._Conn(raw, "sqlite"))
        raw.executemany(
            "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
            " lt_no, subh_cd, name, applied, quota)"
            " VALUES ('2026','U1','U2','T1',?,?,'000','c',?,30)",
            [(f"M{i:05d}", "001", i) for i in range(n_classes)],
        )
        raw.commit()
        return _CountingConn(raw)

    def test_samples_insert_in_chunks_not_per_row(self) -> None:
        conn = self._conn(250)

        inserted = db.sample_counts(
            conn, [("2026", "T1")], ts="2026-09-01T09:00:00",
            collect_cart=False, collect_enrolled=True,
        )

        self.assertEqual(inserted, 250)
        got = conn.execute("SELECT COUNT(*) FROM count_samples").fetchone()[0]
        self.assertEqual(got, 250)
        # 250 rows at ~100 rows per statement must land in a handful of
        # INSERTs, never one per row
        self.assertLessEqual(conn.insert_statements, 5)

    def test_insert_chunked_available_from_db(self) -> None:
        raw = sqlite3.connect(":memory:")
        raw.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        statements = db.insert_chunked(
            raw, "t", ["a", "b"], [(i, "x") for i in range(7)], chunk_rows=3
        )
        self.assertEqual(statements, 3)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM t").fetchone()[0], 7)


if __name__ == "__main__":
    unittest.main()
