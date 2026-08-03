from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import db  # noqa: E402
import crawl  # noqa: E402
import export_json  # noqa: E402


class CountsPipelineTests(unittest.TestCase):
    def test_collection_window_uses_explicit_timezone(self) -> None:
        instant = datetime(2026, 8, 4, 15, 30, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"COLLECTION_TIMEZONE": "Asia/Seoul"}, clear=False):
            self.assertEqual(crawl._today_iso(instant), "2026-08-05")

    def _connection_with_class(self, path: Path):
        conn = db._connect_sqlite(path)
        db.init_schema(conn)
        conn.execute(
            "INSERT INTO terms(term, year, label) VALUES(?,?,?)",
            ("fall", "2026", "2026 2학기"),
        )
        conn.execute(
            """INSERT INTO classes
               (term, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,
                subh_cd, name, quota, applied, cart, enrolled)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("fall", "2026", "fall", "", "C101", "001", "000",
             "테스트", 30, 12, 7, 4),
        )
        conn.commit()
        return conn

    def test_cart_only_sample_does_not_copy_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self._connection_with_class(Path(tmp) / "test.db") as conn:

                inserted = db.sample_counts(
                    conn,
                    [("2026", "fall")],
                    ts="2026-08-04T09:00:00",
                    collect_cart=True,
                    collect_enrolled=False,
                    collect_applied=False,
                )

                row = conn.execute(
                    "SELECT applied, cart, enrolled, quota FROM count_samples"
                ).fetchone()
                self.assertEqual(inserted, 1)
                self.assertIsNone(row[0])
                self.assertEqual(row[1], 7)
                self.assertIsNone(row[2])
                self.assertEqual(row[3], 30)

    def test_cart_only_refresh_does_not_update_other_live_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self._connection_with_class(Path(tmp) / "test.db") as conn:
                response = {
                    "total": 1,
                    "page_count": 0,
                    "classes": [{
                        "shtm_fg": "fall", "deta_shtm_fg": "", "sbjt_cd": "C101",
                        "lt_no": "001", "subh_cd": "000", "applied": 99,
                        "quota": 99, "enrolled": 99, "cart": 21,
                    }],
                }

                with patch.object(crawl.parse, "parse_response", return_value=response):
                    crawl.refresh_counts(
                        conn,
                        type("Client", (), {"search_page": lambda *_args, **_kwargs: ""})(),
                        "2026",
                        "fall",
                        cart_only=True,
                    )

                row = conn.execute(
                    "SELECT applied, quota, enrolled, cart FROM classes"
                ).fetchone()
                self.assertEqual(tuple(row), (12, 30, 4, 21))

    def test_enrollment_refresh_does_not_update_cart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self._connection_with_class(Path(tmp) / "test.db") as conn:
                response = {
                    "total": 1,
                    "page_count": 0,
                    "classes": [{
                        "shtm_fg": "fall", "deta_shtm_fg": "", "sbjt_cd": "C101",
                        "lt_no": "001", "subh_cd": "000", "applied": 99,
                        "quota": 99, "enrolled": 99, "cart": 21,
                    }],
                }

                with patch.object(crawl.parse, "parse_response", return_value=response):
                    crawl.refresh_counts(
                        conn,
                        type("Client", (), {"search_page": lambda *_args, **_kwargs: ""})(),
                        "2026",
                        "fall",
                        collect_cart=False,
                        collect_enrollment=True,
                    )

                row = conn.execute(
                    "SELECT applied, quota, enrolled, cart FROM classes"
                ).fetchone()
                self.assertEqual(tuple(row), (99, 99, 99, 7))

    def test_collection_argument_selects_components(self) -> None:
        args = crawl.parse_args([
            "--years", "2026",
            "--terms", "fall",
            "--collect", "catalog,enrollment,grading",
        ])

        self.assertEqual(
            args.collections,
            frozenset({"catalog", "enrollment", "grading"}),
        )

    def test_collection_sample_options_can_exclude_cart(self) -> None:
        with patch.dict(
            os.environ,
            {"CART_WINDOWS": "2026-08-04", "ENROLL_WINDOWS": "2026-08-04"},
            clear=False,
        ), patch.object(crawl, "_today_iso", return_value="2026-08-04"):
            options = crawl._sample_options(
                collect_cart=False,
                collect_enrollment=True,
                force=False,
            )

        self.assertFalse(options["collect_cart"])
        self.assertTrue(options["collect_enrolled"])
        self.assertTrue(options["collect_applied"])

    def test_cart_only_and_windowed_flags_are_counts_only_modes(self) -> None:
        args = crawl.parse_args([
            "--years", "2026",
            "--terms", "fall",
            "--counts-only",
            "--cart-only",
            "--windowed",
        ])
        self.assertTrue(args.counts_only)
        self.assertTrue(args.cart_only)
        self.assertTrue(args.windowed)

    def test_windowed_collection_accepts_explicit_cart_component(self) -> None:
        args = crawl.parse_args([
            "--years", "2026",
            "--terms", "fall",
            "--collect", "cart",
            "--windowed",
        ])

        self.assertEqual(args.collections, frozenset({"cart"}))

    def test_windowed_cart_pass_skips_before_minting_a_session(self) -> None:
        with patch.dict(
            os.environ,
            {"CART_WINDOWS": "2099-01-01", "ENROLL_WINDOWS": "2099-01-01"},
            clear=False,
        ), patch.object(crawl, "SnuClient") as client:
            result = crawl.refresh_counts_all(
                None, ["2026"], terms=["fall"], cart_only=True, windowed=True
            )
        self.assertEqual(result["skipped"], "outside collection window")
        client.assert_not_called()

    def test_export_term_selection_accepts_semester_alias(self) -> None:
        self.assertEqual(
            export_json.select_term_codes("fall"),
            ["U000200002U000300001"],
        )

    def test_trend_only_export_leaves_catalog_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "data"
            classes_dir = out / "classes"
            classes_dir.mkdir(parents=True)
            catalog = classes_dir / "2026_fall.json"
            catalog.write_text("catalog", encoding="utf-8")
            (classes_dir / "index.json").write_text(
                '{"terms":[{"year":"2026","term":"fall","file":"2026_fall.json"}]}',
                encoding="utf-8",
            )
            old_out = export_json.OUT
            export_json.OUT = out
            try:
                with db._connect_sqlite(Path(tmp) / "test.db") as conn:
                    db.init_schema(conn)
                    conn.execute(
                        "INSERT INTO terms(term, year, label) VALUES(?,?,?)",
                        ("fall", "2026", "2026 2학기"),
                    )
                    conn.execute(
                        """INSERT INTO classes
                           (term, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,
                            subh_cd, name, quota, applied, cart, enrolled)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("fall", "2026", "fall", "", "C101", "001", "000",
                         "테스트", 30, 12, 7, 4),
                    )
                    conn.commit()
                    db.sample_counts(
                        conn, [("2026", "fall")], ts="2026-08-04T09:00:00",
                        collect_cart=True, collect_enrolled=False,
                    )
                    self.assertEqual(
                        export_json.export_trend_only(
                            conn, years=["2026"], terms=["fall"]
                        ),
                        1,
                    )
            finally:
                export_json.OUT = old_out

            self.assertEqual(catalog.read_text(encoding="utf-8"), "catalog")
            self.assertTrue((out / "trend" / "trend_2026_fall.json").exists())
            index = (classes_dir / "index.json").read_text(encoding="utf-8")
            self.assertIn('"trend":"trend_2026_fall.json"', index)


if __name__ == "__main__":
    unittest.main()
