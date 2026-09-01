from __future__ import annotations

import sqlite3
import unittest

from scraper.migrate_to_turso import _insert_chunked


class InsertChunkedTests(unittest.TestCase):
    def _dst(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        return conn

    def test_inserts_all_rows_across_chunks(self) -> None:
        dst = self._dst()
        rows = [(i, f"r{i}") for i in range(7)]

        statements = _insert_chunked(dst, "t", ["a", "b"], rows, chunk_rows=3)

        got = dst.execute("SELECT a, b FROM t ORDER BY a").fetchall()
        self.assertEqual(got, rows)
        self.assertEqual(statements, 3)  # 3 + 3 + 1

    def test_handles_empty_rows(self) -> None:
        dst = self._dst()

        statements = _insert_chunked(dst, "t", ["a", "b"], [], chunk_rows=3)

        self.assertEqual(statements, 0)
        self.assertEqual(dst.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)

    def test_preserves_null_values(self) -> None:
        dst = self._dst()
        rows = [(1, None), (None, "x")]

        _insert_chunked(dst, "t", ["a", "b"], rows, chunk_rows=10)

        got = dst.execute("SELECT a, b FROM t ORDER BY rowid").fetchall()
        self.assertEqual(got, rows)


if __name__ == "__main__":
    unittest.main()
