from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper import db
from scraper.export_json import export_trend_archives


def _conn(passes: int, classes: int = 2):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    rows = []
    for p in range(passes):
        ts = f"2026-08-04T{p // 60:02d}:{p % 60:02d}:00"
        for c in range(classes):
            rows.append(("2026", "T1", f"M{c:03d}", "001", ts, p, None, p, 30))
    raw.executemany(
        "INSERT INTO count_samples (year, term, sbjt_cd, lt_no, ts,"
        " applied, cart, enrolled, quota) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    raw.commit()
    return raw


TERM = {"year": "2026", "term": "T1"}


class TrendArchiveTests(unittest.TestCase):
    def test_completed_chunks_are_written_and_indexed(self) -> None:
        # 500 passes at window 240 -> chunks w000 (0..239) and w001 (240..479)
        # are complete; the trailing 20 passes belong to the live window only.
        conn = _conn(500)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            n = export_trend_archives(conn, TERM, out, window=240)

            self.assertEqual(n, 2)
            w0 = json.loads((out / "trend_2026_T1_w000.json").read_text())
            w1 = json.loads((out / "trend_2026_T1_w001.json").read_text())
            self.assertEqual(len(w0["ts"]), 240)
            self.assertEqual(len(w1["ts"]), 240)
            self.assertEqual(w0["ts"][0], "2026-08-04T00:00:00")
            self.assertEqual(w1["ts"][0], w0["ts"][-1].replace("03:59", "04:00"))
            self.assertFalse((out / "trend_2026_T1_w002.json").exists())
            # per-class aligned arrays present
            self.assertIn("M000(001)", w0["series"])
            self.assertEqual(len(w0["series"]["M000(001)"]["a"]), 240)

    def test_existing_chunk_files_are_not_rewritten(self) -> None:
        conn = _conn(500)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_trend_archives(conn, TERM, out, window=240)
            marker = out / "trend_2026_T1_w000.json"
            marker.write_text("{\"frozen\": true}")

            n = export_trend_archives(conn, TERM, out, window=240)

            self.assertEqual(n, 2)   # still reports both chunks
            self.assertEqual(marker.read_text(), "{\"frozen\": true}")

    def test_too_few_passes_yield_no_chunks(self) -> None:
        conn = _conn(100)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(export_trend_archives(conn, TERM, out, window=240), 0)
            self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
