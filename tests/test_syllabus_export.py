from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

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

    def test_a_bad_term_is_skipped_and_reported_others_are_written(self) -> None:
        conn = _conn({"A001|001|000": {}, "A001|001|001": {}}, [])
        good = sa.build_snapshot("2027", "U000200001U000300001", {"B|001|000": {}}, [],
                                 collected_at="2026-10-17T04:40:00+09:00")
        conn.execute("INSERT INTO syllabus_attach_snapshots VALUES (?,?,?,?,?,?,?,?)",
                     ("2027", "U000200001U000300001", good["digest"], 1, 0,
                      good["collected_at"], good["collected_at"], good["payload"]))
        conn.execute("INSERT INTO syllabus_attach_snapshots VALUES"
                     " ('2028','U000200001U000300001','d',1,0,'t','t',x'00ff')")
        skipped: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "syllabus" / "2026_U000200002U000300001.json"
            old.parent.mkdir()
            old.write_text("previous", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                n = ej.export_syllabus(conn, Path(tmp), skipped)
            files = sorted(f.name for f in (Path(tmp) / "syllabus").iterdir())
            self.assertEqual(old.read_text(encoding="utf-8"), "previous")   # kept
        self.assertEqual(n, 1)
        self.assertEqual(skipped, ["2026/U000200002U000300001", "2028/U000200001U000300001"])
        self.assertEqual(files, ["2026_U000200002U000300001.json",
                                 "2027_U000200001U000300001.json"])
        self.assertIn("warn: syllabus export skipped 2026/U000200002U000300001: "
                      "syllabus 2026 U000200002U000300001: A001|001 has more than one subh",
                      err.getvalue())
        self.assertIn("warn: syllabus export skipped 2028/U000200001U000300001:",
                      err.getvalue())

    def test_main_exits_5_after_writing_everything_else_when_a_term_was_skipped(self) -> None:
        conn = _conn({"A001|001|000": {}, "A001|001|001": {}}, [])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(ej.os.environ, {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}), \
             patch.object(ej.db, "connect", return_value=conn), \
             patch.object(ej, "OUT", Path(tmp)), \
             patch.object(ej, "export_trend_only", return_value=1) as trend, \
             contextlib.redirect_stderr(io.StringIO()):
            rc = ej.main(["--current", "--years", "2026", "--terms", "fall"])
        trend.assert_called_once()
        self.assertEqual(rc, ej.SYLLABUS_SKIPPED_EXIT)
        self.assertEqual(ej.SYLLABUS_SKIPPED_EXIT, 5)

    def test_main_returns_0_when_nothing_was_skipped(self) -> None:
        conn = _conn(CLASSES, [])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(ej.os.environ, {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}), \
             patch.object(ej.db, "connect", return_value=conn), \
             patch.object(ej, "OUT", Path(tmp)), \
             patch.object(ej, "export_trend_only", return_value=1):
            self.assertEqual(ej.main(["--current", "--years", "2026", "--terms", "fall"]), 0)


if __name__ == "__main__":
    unittest.main()
