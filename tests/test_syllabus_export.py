from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import db  # noqa: E402
import export_json as ej  # noqa: E402
import syllabus_attach as sa  # noqa: E402


def _conn(classes: dict, missing: list[str], year="2026", term="U000200002U000300001"):
    conn = sqlite3.connect(":memory:")
    conn.execute(db.SYLLABUS_ATTACH_DDL)
    snap = sa.build_snapshot(year, term, classes, missing,
                             collected_at="2026-10-17T04:40:00+09:00")
    conn.execute("INSERT INTO syllabus_attach_snapshots VALUES (?,?,?,?,?,?,?,?)",
                 (year, term, snap["digest"], snap["classes"], snap["missing"],
                  snap["collected_at"], snap["collected_at"], snap["payload"]))
    return conn


CLASSES = {"A001|001|000": {"ko": [["a.pdf", "1", "2"]]},
           "A002|001|000": {},
           "A003|001|000": {"en": [["c.pdf", "3", "4"]]}}


class ExportSyllabusTest(unittest.TestCase):
    def test_keys_reduced_missing_omitted_empty_kept(self) -> None:
        conn = _conn(CLASSES, ["A003|001|000"])
        with tempfile.TemporaryDirectory() as tmp:
            n = ej.export_syllabus(conn, Path(tmp))
            f = Path(tmp) / "syllabus" / "2026_U000200002U000300001.json"
            data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(n, 1)
        self.assertEqual(data["classes"], {"A001|001": {"ko": [["a.pdf", "1", "2"]]},
                                           "A002|001": {}})
        self.assertEqual((data["year"], data["collected_at"]),
                         ("2026", "2026-10-17T04:40:00+09:00"))
        self.assertIn("generated", data)

    def test_unchanged_export_is_byte_identical(self) -> None:
        conn = _conn(CLASSES, [])
        with tempfile.TemporaryDirectory() as tmp:
            ej.export_syllabus(conn, Path(tmp))
            f = next((Path(tmp) / "syllabus").iterdir())
            first = f.read_bytes()
            ej.export_syllabus(conn, Path(tmp))
            self.assertEqual(f.read_bytes(), first)

    def test_missing_table_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ej.export_syllabus(sqlite3.connect(":memory:"), Path(tmp)), 0)

    def test_subh_collision_fails_loudly(self) -> None:
        conn = _conn({"A001|001|000": {}, "A001|001|001": {}}, [])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "A001\\|001"):
                ej.export_syllabus(conn, Path(tmp))


if __name__ == "__main__":
    unittest.main()
