from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import export_json as ej  # noqa: E402

SPRING, SUMMER, FALL, WINTER = (ej.TERM_CODES[k] for k in ("spring", "summer", "fall", "winter"))
TERMS = [  # db.list_terms order: newest year first
    {"year": "2027", "term": SPRING, "label": "2027 1학기"},
    {"year": "2026", "term": SPRING, "label": "2026 1학기"},
    {"year": "2026", "term": SUMMER, "label": "2026 여름학기"},
    {"year": "2026", "term": FALL, "label": "2026 2학기"},
    {"year": "2026", "term": WINTER, "label": "2026 겨울학기"},
    {"year": "2025", "term": WINTER, "label": "2025 겨울학기"},
]
WANT = {("2027", SPRING): "upcoming", ("2026", SPRING): "past", ("2026", SUMMER): "past",
        ("2026", FALL): "current", ("2026", WINTER): "upcoming", ("2025", WINTER): "past"}


class CurrentTermTest(unittest.TestCase):
    def test_counted_term_wins_over_a_re_crawled_year_sem(self) -> None:
        env = {"YEAR": "2025", "SEM": "spring", "COUNT_YEAR": "2026", "COUNT_SEM": "fall"}
        self.assertEqual(ej.current_term(env), ("2026", FALL))

    def test_falls_back_to_year_sem_and_takes_codes(self) -> None:
        self.assertEqual(ej.current_term({"YEAR": "2026", "SEM": WINTER}), ("2026", WINTER))
        self.assertEqual(ej.current_term({"COUNT_YEAR": "", "COUNT_SEM": "", "YEAR": "2026",
                                          "SEM": "Summer"}), ("2026", SUMMER))

    def test_missing_or_bad_values_stop(self) -> None:
        for env in ({}, {"YEAR": "2026"}, {"SEM": "fall"}, {"YEAR": "26", "SEM": "fall"},
                    {"YEAR": "2026", "SEM": "autumn"}):
            with self.subTest(env=env), self.assertRaises(SystemExit) as cm:
                ej.current_term(env)
            self.assertIn("COUNT_YEAR/COUNT_SEM (collect.env) or YEAR/SEM", str(cm.exception))


class TermStatusTest(unittest.TestCase):
    def test_calendar_order_within_and_across_years(self) -> None:
        for t in TERMS:
            with self.subTest(t=t):
                self.assertEqual(ej.term_status(t["year"], t["term"], ("2026", FALL)),
                                 WANT[(t["year"], t["term"])])


class IndexStatusTest(unittest.TestCase):
    def _index(self, tmp: Path) -> dict:
        return json.loads((tmp / "classes" / "index.json").read_text(encoding="utf-8"))

    def test_full_export_marks_every_term(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(ej.os.environ, {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}), \
             patch.object(ej, "OUT", Path(tmp)), \
             patch.object(ej.db, "connect"), \
             patch.object(ej.db, "list_terms", return_value=TERMS), \
             patch.object(ej.db, "search", return_value=[]), \
             patch.multiple(ej.db, list_departments=lambda c: [], list_classifications=lambda c: [],
                            list_grades=lambda c: [], list_gradings=lambda c: []), \
             patch.object(ej, "export_trend", return_value=None), \
             patch.object(ej, "_export_explore"), \
             patch.object(ej.rooms, "build_rooms_index", return_value={}), \
             patch.object(ej, "export_syllabus", return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ej.main([]), 0)
            index = self._index(Path(tmp))
        self.assertEqual({(t["year"], t["term"]): t["status"] for t in index["terms"]}, WANT)

    def test_current_export_recomputes_every_entry(self) -> None:
        stale = [{**t, "count": 0, "file": "x.json", "status": "upcoming"} for t in TERMS]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(ej.os.environ, {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}), \
             patch.object(ej, "OUT", Path(tmp)), \
             patch.object(ej.db, "connect"), \
             patch.object(ej.db, "list_terms", return_value=[]), \
             patch.object(ej, "export_syllabus", return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            (Path(tmp) / "classes").mkdir()
            (Path(tmp) / "classes" / "index.json").write_text(json.dumps({"terms": stale}),
                                                              encoding="utf-8")
            self.assertEqual(ej.main(["--current", "--years", "2026", "--terms", "fall"]), 0)
            index = self._index(Path(tmp))
        self.assertEqual({(t["year"], t["term"]): t["status"] for t in index["terms"]}, WANT)

    def test_trend_only_without_current_leaves_status_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(ej, "OUT", Path(tmp)), \
             patch.object(ej.db, "list_terms", return_value=[]):
            (Path(tmp) / "classes").mkdir()
            (Path(tmp) / "classes" / "index.json").write_text(
                json.dumps({"terms": [{"year": "2026", "term": FALL, "status": "upcoming"}]}),
                encoding="utf-8")
            ej.export_trend_only(object(), years=["2026"], terms=[FALL])
            index = self._index(Path(tmp))
        self.assertEqual(index["terms"][0]["status"], "upcoming")

    def test_unknown_current_term_writes_nothing(self) -> None:
        env = {k: v for k, v in ej.os.environ.items()
               if k not in ("YEAR", "SEM", "COUNT_YEAR", "COUNT_SEM")}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(ej.os.environ, env, clear=True), \
             patch.object(ej, "OUT", Path(tmp) / "out"), \
             patch.object(ej.db, "connect") as connect:
            with self.assertRaises(SystemExit):
                ej.main([])
            self.assertFalse((Path(tmp) / "out").exists())
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
