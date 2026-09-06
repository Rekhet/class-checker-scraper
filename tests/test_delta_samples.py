from __future__ import annotations

import sqlite3
import unittest.mock
import unittest
from pathlib import Path

from scraper import db, export_json


YEAR, TERM = "2026", "T1"


def TS(minutes: int) -> str:
    """A real ISO timestamp: keyframe scheduling does date arithmetic on these."""
    return f"2026-09-01T{9 + minutes // 60:02d}:{minutes % 60:02d}:00"
CLASSES = (("M100", "001"), ("M200", "002"))


def _conn(n: int = 2):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    raw.executemany(
        "INSERT INTO classes (year, term, shtm_fg, deta_shtm_fg, sbjt_cd,"
        " lt_no, subh_cd, name, applied, cart, enrolled, quota, cancel_vacancy)"
        " VALUES (?,?,'U1','U2',?,?,'000','과목',?,?,?,?,?)",
        [(YEAR, TERM, cd, no, 10, None, 10, 30, 0) for cd, no in CLASSES[:n]],
    )
    raw.commit()
    return raw


def _set(conn, sbjt_cd, **cols):
    assign = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE classes SET {assign} WHERE sbjt_cd=?",
                 (*cols.values(), sbjt_cd))
    conn.commit()


def _samples(conn):
    return conn.execute(
        "SELECT sbjt_cd, ts, applied, cart, enrolled, quota, cancel_vacancy "
        "FROM count_samples ORDER BY ts, sbjt_cd").fetchall()


class DeltaSampleTests(unittest.TestCase):
    def test_first_pass_records_every_class(self) -> None:
        conn = _conn()

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0),
                                   collect_cart=False, collect_enrolled=True)

        self.assertEqual(written, 2)
        self.assertEqual(len(_samples(conn)), 2)
        axis = conn.execute("SELECT ts, applied, cart, enrolled "
                            "FROM count_passes").fetchall()
        self.assertEqual([tuple(r) for r in axis], [(TS(0), 1, 0, 1)])

    def test_unchanged_pass_writes_only_the_axis_point(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10),
                                   collect_cart=False)

        self.assertEqual(written, 0)
        self.assertEqual(len(_samples(conn)), 2)          # still just the first pass
        axis = [r[0] for r in conn.execute(
            "SELECT ts FROM count_passes ORDER BY ts").fetchall()]
        self.assertEqual(axis, [TS(0), TS(10)])

    def test_only_the_class_that_moved_is_written(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        _set(conn, "M200", enrolled=11)

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10),
                                   collect_cart=False)

        self.assertEqual(written, 1)
        second = [r for r in _samples(conn) if r["ts"] == TS(10)]
        self.assertEqual([r["sbjt_cd"] for r in second], ["M200"])
        latest = dict(conn.execute(
            "SELECT sbjt_cd, enrolled FROM count_latest").fetchall())
        self.assertEqual(latest, {"M100": 10, "M200": 11})

    def test_uncollected_metric_neither_changes_nor_erases(self) -> None:
        conn = _conn(1)
        _set(conn, "M100", cart=12)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=True,
                         collect_enrolled=False, collect_applied=False)
        _set(conn, "M100", enrolled=11)

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10),
                                   collect_cart=False, collect_enrolled=True)

        self.assertEqual(written, 1)
        row = [r for r in _samples(conn) if r["ts"] == TS(10)][0]
        self.assertIsNone(row["cart"])          # 장바구니 window is closed
        self.assertEqual(row["enrolled"], 11)
        latest = conn.execute(
            "SELECT cart, enrolled FROM count_latest").fetchone()
        self.assertEqual(tuple(latest), (12, 11))   # 장바구니 value survives

    def test_outside_every_window_nothing_is_recorded(self) -> None:
        conn = _conn()

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0),
                                   collect_cart=False, collect_enrolled=False)

        self.assertEqual(written, 0)
        self.assertEqual(_samples(conn), [])
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM count_passes").fetchone()[0], 0)


class TrendForwardFillTests(unittest.TestCase):
    TERM_ROW = {"year": YEAR, "term": TERM, "label": "2026 T1"}

    def test_series_repeat_the_last_known_value(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10), collect_cart=False)
        _set(conn, "M200", enrolled=11)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(20), collect_cart=False)

        trend = export_json.export_trend(conn, self.TERM_ROW)

        self.assertEqual(trend["ts"], [TS(0), TS(10), TS(20)])
        self.assertEqual(trend["updated"], TS(20))
        self.assertEqual(trend["series"]["M100(001)"]["e"], [10, 10, 10])
        self.assertEqual(trend["series"]["M200(002)"]["e"], [10, 10, 11])
        # 장바구니 was never collected: a gap, not a flat line
        self.assertEqual(trend["series"]["M100(001)"]["c"], [None, None, None])

    def test_a_metric_returns_to_a_gap_when_its_window_closes(self) -> None:
        conn = _conn(1)
        _set(conn, "M100", cart=12)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=True,
                         collect_enrolled=False, collect_applied=False)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10), collect_cart=False,
                         collect_enrolled=True)

        trend = export_json.export_trend(conn, self.TERM_ROW)

        self.assertEqual(trend["series"]["M100(001)"]["c"], [12, None])
        self.assertEqual(trend["series"]["M100(001)"]["e"], [None, 10])

    def test_archives_carry_the_baseline_across_window_edges(self) -> None:
        conn = _conn(1)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        for i in range(1, 5):                       # nothing moves afterwards
            db.sample_counts(conn, [(YEAR, TERM)], ts=TS(i * 10),
                             collect_cart=False)

        with unittest.mock.patch.object(export_json, "TREND_WINDOW", 2):
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                complete = export_json.export_trend_archives(
                    conn, self.TERM_ROW, out, window=2)
                import json
                second = json.loads(
                    (out / f"trend_{YEAR}_{TERM}_w001.json").read_text())

        self.assertEqual(complete, 2)
        # window 1 contains no samples at all; its values are forward-filled
        # from the pass that preceded the window
        self.assertEqual(second["ts"], [TS(20), TS(30)])
        self.assertEqual(second["series"]["M100(001)"]["e"], [10, 10])


if __name__ == "__main__":
    import unittest.mock  # noqa: F401  (used above)
    unittest.main()


class TombstoneTests(unittest.TestCase):
    TERM_ROW = {"year": YEAR, "term": TERM, "label": "2026 T1"}

    def test_a_class_leaving_the_roster_closes_its_series(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        conn.execute("DELETE FROM classes WHERE sbjt_cd='M200'")
        conn.commit()

        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(10), collect_cart=False)

        tomb = conn.execute(
            "SELECT applied, cart, enrolled, quota, cancel_vacancy "
            "FROM count_samples WHERE sbjt_cd='M200' AND ts=?", (TS(10),)).fetchone()
        self.assertEqual(tuple(tomb), (None, None, None, None, None))
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM count_latest WHERE sbjt_cd='M200'").fetchone())

        # its history stays (it really was there at T1000) but the series
        # stops instead of being forward-filled forever
        trend = export_json.export_trend(conn, self.TERM_ROW)
        self.assertEqual(trend["series"]["M200(002)"]["e"], [10, None])
        self.assertEqual(trend["series"]["M100(001)"]["e"], [10, 10])

        # a window that starts after the retirement does not mention it at all
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(20), collect_cart=False)
        with unittest.mock.patch.object(export_json, "TREND_WINDOW", 1):
            later = export_json.export_trend(conn, self.TERM_ROW)
        self.assertEqual(later["ts"], [TS(20)])
        self.assertNotIn("M200(002)", later["series"])

    def test_a_tombstone_retires_the_class_when_merged(self) -> None:
        conn = _conn(1)
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        conn.execute(
            "INSERT INTO count_samples (year, term, sbjt_cd, lt_no, ts) "
            "VALUES (?,?,?,?,?)", (YEAR, TERM, "M100", "001", TS(10)))
        conn.commit()

        db.fold_pass_into_latest(conn, TS(10))

        self.assertIsNone(conn.execute(
            "SELECT 1 FROM count_latest WHERE sbjt_cd='M100'").fetchone())


class BackfillTests(unittest.TestCase):
    def test_dense_history_becomes_axis_baseline_and_tombstones(self) -> None:
        conn = _conn(0)
        dense = []
        for i, ts in enumerate((TS(0), TS(10), TS(20))):
            dense.append((YEAR, TERM, "M100", "001", ts, 5 + i, None, 5 + i, 30, 0))
        dense.append((YEAR, TERM, "M900", "009", TS(0), 1, None, 1, 20, 0))
        conn.executemany(
            "INSERT INTO count_samples (year, term, sbjt_cd, lt_no, ts, applied,"
            " cart, enrolled, quota, cancel_vacancy) VALUES (?,?,?,?,?,?,?,?,?,?)",
            dense)
        conn.commit()

        out = db.backfill_delta_tables(db._Conn(conn, "sqlite"))

        self.assertEqual(out["passes"], 3)
        self.assertEqual(out["latest"], 1)      # M100 is still on the roster
        self.assertEqual(out["retired"], 1)     # M900 vanished after T1000
        axis = [r[0] for r in conn.execute(
            "SELECT ts FROM count_passes ORDER BY ts").fetchall()]
        self.assertEqual(axis, [TS(0), TS(10), TS(20)])
        tomb = conn.execute(
            "SELECT ts, quota FROM count_samples WHERE sbjt_cd='M900' "
            "AND quota IS NULL").fetchone()
        self.assertEqual(tuple(tomb), (TS(10), None))
        latest = conn.execute(
            "SELECT sbjt_cd, applied, enrolled FROM count_latest").fetchall()
        self.assertEqual([tuple(r) for r in latest], [("M100", 7, 7)])

    def test_backfill_is_a_no_op_on_a_fresh_database(self) -> None:
        conn = _conn(1)
        self.assertEqual(db.backfill_delta_tables(db._Conn(conn, "sqlite")),
                         {"passes": 0, "latest": 0})


class KeyframeTests(unittest.TestCase):
    """A delta only means anything against the baseline its writer held. If two
    databases' baselines ever diverge, the writer sees 'no change' and never
    emits the value the reader is missing - which silently stranded seven
    classes on 2026-09-04. A periodic full pass heals that."""

    def test_first_pass_of_a_term_is_a_keyframe(self) -> None:
        conn = _conn()

        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)

        full = conn.execute(
            "SELECT full FROM count_passes WHERE ts=?", (TS(0),)).fetchone()[0]
        self.assertEqual(full, 1)

    def test_passes_within_the_interval_stay_deltas(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)

        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(600),
                                   collect_cart=False)   # +10h

        self.assertEqual(written, 0)
        self.assertEqual(conn.execute(
            "SELECT full FROM count_passes WHERE ts=?", (TS(600),)).fetchone()[0], 0)

    def test_every_class_is_restated_once_the_interval_passes(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)

        # 24h later nothing has moved, and the pass re-states both classes
        written = db.sample_counts(conn, [(YEAR, TERM)], ts=TS(24 * 60),
                                   collect_cart=False)

        self.assertEqual(written, 2)
        self.assertEqual(conn.execute(
            "SELECT full FROM count_passes WHERE ts=?",
            (TS(24 * 60),)).fetchone()[0], 1)

    def test_a_keyframe_republishes_a_value_the_reader_is_missing(self) -> None:
        # the 2026-09-04 failure in miniature, which needs TWO databases: the
        # writer's baseline agrees with the live number, so it emits nothing,
        # while the reader sits on a value it can never be told is wrong.
        writer = _conn(1)
        reader = _conn(0)

        def carry(ts):
            rows = writer.execute(
                "SELECT year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled,"
                " quota, cancel_vacancy FROM count_samples WHERE ts=?",
                (ts,)).fetchall()
            reader.executemany(
                "INSERT INTO count_samples (year, term, sbjt_cd, lt_no, ts,"
                " applied, cart, enrolled, quota, cancel_vacancy)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", [tuple(r) for r in rows])
            reader.commit()
            db.fold_pass_into_latest(reader, ts)
            reader.commit()
            return len(rows)

        db.sample_counts(writer, [(YEAR, TERM)], ts=TS(0), collect_cart=False)
        carry(TS(0))
        reader.execute("UPDATE count_latest SET enrolled=9")   # baselines split
        reader.commit()

        db.sample_counts(writer, [(YEAR, TERM)], ts=TS(10), collect_cart=False)
        self.assertEqual(carry(TS(10)), 0)          # writer sees no change...
        self.assertEqual(reader.execute(
            "SELECT enrolled FROM count_latest").fetchone()[0], 9)  # ...reader stays wrong

        db.sample_counts(writer, [(YEAR, TERM)], ts=TS(24 * 60),
                         collect_cart=False)
        self.assertEqual(carry(TS(24 * 60)), 1)     # keyframe re-states it

        self.assertEqual(reader.execute(
            "SELECT enrolled FROM count_latest").fetchone()[0], 10)

    def test_keyframes_can_be_disabled(self) -> None:
        conn = _conn()
        db.sample_counts(conn, [(YEAR, TERM)], ts=TS(0), collect_cart=False)

        self.assertFalse(db.keyframe_due(conn, YEAR, TERM, TS(24 * 60), hours=0))
