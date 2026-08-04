from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import audit_cart_counts  # noqa: E402
import db  # noqa: E402


class CartAuditTests(unittest.TestCase):
    def _catalog_row(self, *, row_id: int, subh_cd: str, name: str,
                     cart: int | None) -> dict:
        return {
            "id": row_id,
            "year": "2026",
            "term": "U000200002U000300001",
            "sbjt_cd": "C101",
            "lt_no": "001",
            "subh_cd": subh_cd,
            "name": name,
            "professor": "교수",
            "department": "학과",
            "cart": cart,
        }

    def test_summary_finds_only_stored_null_cart_rows(self) -> None:
        rows = [
            self._catalog_row(row_id=1, subh_cd="000", name="미수집", cart=None),
            self._catalog_row(row_id=2, subh_cd="001", name="수집", cart=0),
        ]
        report = audit_cart_counts.summarize_null_rows(rows[:1])
        self.assertEqual(report["stored_null"], 1)
        self.assertEqual(report["stored_null_by_subh_cd"], {"000": 1})

    def test_find_null_rows_filters_by_term_alias(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with db._connect_sqlite(Path(tmp) / "audit.db") as conn:
                db.init_schema(conn)
                conn.execute(
                    """INSERT INTO classes
                       (term, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,
                        subh_cd, name, cart)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("U000200002U000300001", "2026", "U000200002",
                     "U000300001", "C101", "001", "000", "미수집", None),
                )
                conn.execute(
                    """INSERT INTO classes
                       (term, year, shtm_fg, deta_shtm_fg, sbjt_cd, lt_no,
                        subh_cd, name, cart)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("U000200001U000300001", "2026", "U000200001",
                     "U000300001", "C102", "001", "000", "다른 학기", None),
                )
                conn.commit()

                rows = audit_cart_counts.find_null_cart_rows(
                    conn, year="2026", term="fall"
                )

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["sbjt_cd"], "C101")

    def test_live_nonnegative_value_is_reported_for_stored_null(self) -> None:
        catalog = [self._catalog_row(row_id=1, subh_cd="000", name="미수집", cart=None)]
        live = [{
            "year": "2026", "shtm_fg": "U000200002",
            "deta_shtm_fg": "U000300001", "sbjt_cd": "C101", "lt_no": "001",
            "subh_cd": "027", "name": "미수집", "professor": "교수",
            "department": "학과", "cart": 58,
        }]
        report = audit_cart_counts.analyze_live_records(catalog, catalog, live)
        self.assertEqual(report["live_nonnegative_for_stored_null"], 1)
        self.assertEqual(report["recoverable_examples"][0]["live_cart"], 58)
        self.assertEqual(report["recoverable_examples"][0]["match"], "code+lecture")

    def test_live_ambiguity_is_reported_without_guessing(self) -> None:
        catalog = [
            self._catalog_row(row_id=1, subh_cd="000", name="첫 과목", cart=None),
            self._catalog_row(row_id=2, subh_cd="001", name="둘째 과목", cart=None),
        ]
        live = [{
            "year": "2026", "shtm_fg": "U000200002",
            "deta_shtm_fg": "U000300001", "sbjt_cd": "C101", "lt_no": "001",
            "subh_cd": "027", "name": "확인 불가", "professor": "교수",
            "department": "학과", "cart": 58,
        }]
        report = audit_cart_counts.analyze_live_records(catalog, catalog, live)
        self.assertEqual(report["ambiguous"], 2)
        self.assertEqual(report["live_nonnegative_for_stored_null"], 0)


if __name__ == "__main__":
    unittest.main()
