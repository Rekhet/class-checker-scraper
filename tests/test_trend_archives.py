from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper import db
from scraper.export_json import (decode_trend, export_trend,
                                 export_trend_archives, live_bounds)


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


def _read(path: Path) -> dict:
    return decode_trend(json.loads(path.read_text()))


class TrendArchiveTests(unittest.TestCase):
    def test_completed_chunks_are_written_and_indexed(self) -> None:
        # 500 passes at window 240 -> chunks w000 (0..239) and w001 (240..479)
        # are complete; the trailing 20 passes belong to the live window only.
        conn = _conn(500)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            n = export_trend_archives(conn, TERM, out, window=240)

            self.assertEqual(n, 2)
            w0 = _read(out / "trend_2026_T1_w000.json")
            w1 = _read(out / "trend_2026_T1_w001.json")
            self.assertEqual(len(w0["ts"]), 240)
            self.assertEqual(len(w1["ts"]), 240)
            self.assertEqual(w0["ts"][0], "2026-08-04T00:00:00")
            self.assertEqual(w1["ts"][0], w0["ts"][-1].replace("03:59", "04:00"))
            self.assertFalse((out / "trend_2026_T1_w002.json").exists())
            # per-class aligned arrays present
            self.assertIn("M000(001)", w0["series"])
            self.assertEqual(w0["series"]["M000(001)"]["a"], list(range(240)))

    def test_existing_current_format_chunks_are_not_rewritten(self) -> None:
        conn = _conn(500)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_trend_archives(conn, TERM, out, window=240)
            marker = out / "trend_2026_T1_w000.json"
            marker.write_text('{"v":2,"frozen":true}')

            n = export_trend_archives(conn, TERM, out, window=240)

            self.assertEqual(n, 2)   # still reports both chunks
            self.assertEqual(marker.read_text(), '{"v":2,"frozen":true}')

    def test_old_dense_chunks_are_rewritten_once(self) -> None:
        conn = _conn(500)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old = out / "trend_2026_T1_w000.json"
            old.write_text('{"updated":"x","ts":[],"series":{}}')

            export_trend_archives(conn, TERM, out, window=240)

            self.assertEqual(len(_read(old)["ts"]), 240)

    def test_too_few_passes_yield_no_chunks(self) -> None:
        conn = _conn(100)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(export_trend_archives(conn, TERM, out, window=240), 0)
            self.assertEqual(list(out.iterdir()), [])


class LiveWindowTests(unittest.TestCase):
    def test_live_window_starts_at_the_newest_complete_chunk(self) -> None:
        self.assertEqual(live_bounds(100, 240), (0, 100))
        self.assertEqual(live_bounds(240, 240), (0, 240))
        self.assertEqual(live_bounds(500, 240), (240, 500))
        self.assertEqual(live_bounds(720, 240), (480, 720))

    def test_live_file_never_overlaps_a_navigable_archive(self) -> None:
        conn = _conn(500)
        from unittest.mock import patch
        import scraper.export_json as export_json

        with patch.object(export_json, "TREND_WINDOW", 240), \
             patch.object(export_json, "live_bounds",
                          lambda total: live_bounds(total, 240)):
            live = decode_trend(export_trend(conn, TERM))
        # archives 0..0 are paged to; w001 (240..479) rides in the live file
        self.assertEqual(live["ts"][0], "2026-08-04T04:00:00")
        self.assertEqual(len(live["ts"]), 260)


class EncodingTests(unittest.TestCase):
    def test_round_trip_and_size(self) -> None:
        conn = _conn(240, classes=50)
        payload = export_trend(conn, TERM)
        # every value moves every pass here, the worst case for the encoding
        dense = decode_trend(payload)
        self.assertEqual(dense["series"]["M007(001)"]["e"], list(range(240)))
        self.assertEqual(dense["series"]["M007(001)"]["q"], [30] * 240)
        # a constant quota is one scalar, and cart (never collected) is absent
        self.assertEqual(payload["series"]["M007(001)"]["q"], 30)
        self.assertNotIn("c", payload["series"]["M007(001)"])
        self.assertNotIn("c", payload["m"])

    def test_times_are_absolute_epoch_seconds(self) -> None:
        payload = export_trend(_conn(2), TERM)
        # 2026-08-04T00:00:00 in Asia/Seoul is 2026-08-03T15:00:00Z
        self.assertEqual(payload["t"][0], 1785769200)
        self.assertEqual(payload["t"][1] - payload["t"][0], 60)


if __name__ == "__main__":
    unittest.main()
