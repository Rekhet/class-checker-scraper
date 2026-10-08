from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import catalog_snapshot as cs  # noqa: E402
import db  # noqa: E402

WINTER = "U000200002U000300002"


def _rec(code: str, *, name: str = "강의", slots=None) -> dict:
    return {"year": "2026", "shtm_fg": WINTER[:10], "deta_shtm_fg": WINTER[10:],
            "sbjt_cd": code, "lt_no": "001", "subh_cd": "000", "name": name,
            "professor": "교수", "college": "인문대학", "department": "국문과",
            "classification": ["학사", "교양"], "grade": "1", "credits": 3,
            "quota": 30, "quota_returning": None, "applied": 0, "enrolled": None,
            "cart": 0, "room": "", "language": "한국어", "status": "설강",
            "slots": slots or []}


def _snap(recs, *, methods=None, switchable=()) -> dict:
    return cs.build_snapshot("2026", WINTER, "2026 겨울학기", recs,
                             methods if methods is not None
                             else {(r["sbjt_cd"], "001", "000"): "A~F" for r in recs},
                             set(switchable))


def _remote() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    cs.init_remote(conn)
    return conn


class SnapshotTests(unittest.TestCase):
    def test_digest_ignores_record_and_key_order(self) -> None:
        a, b = _rec("A1"), _rec("B2")
        self.assertEqual(_snap([a, b])["digest"], _snap([b, a])["digest"])
        self.assertNotEqual(_snap([a, b])["digest"],
                            _snap([a, _rec("B2", name="다른 강의")])["digest"])

    def test_push_inserts_then_only_touches_checked_at_when_unchanged(self) -> None:
        remote = _remote()
        snap = _snap([_rec("A1")])
        self.assertEqual(cs.push(remote, snap, now="2026-10-08T05:20:00"), "new")
        self.assertEqual(cs.push(remote, snap, now="2026-10-08T11:20:00"), "unchanged")
        row = remote.execute("SELECT fetched_at, checked_at, classes FROM catalog_snapshots"
                             ).fetchone()
        self.assertEqual(row, ("2026-10-08T05:20:00", "2026-10-08T11:20:00", 1))

        changed = _snap([_rec("A1"), _rec("B2")])
        self.assertEqual(cs.push(remote, changed, now="2026-10-08T17:20:00"), "changed")
        row = remote.execute("SELECT fetched_at, classes, digest FROM catalog_snapshots"
                             ).fetchone()
        self.assertEqual(row, ("2026-10-08T17:20:00", 2, changed["digest"]))

    def test_guard_refuses_an_empty_or_shrunken_term(self) -> None:
        remote = _remote()
        cs.push(remote, _snap([_rec(f"C{i}") for i in range(10)]), now="t0")
        with self.assertRaisesRegex(cs.SnapshotError, "no classes"):
            cs.check_snapshot(remote, _snap([]))
        with self.assertRaisesRegex(cs.SnapshotError, "below"):
            cs.check_snapshot(remote, _snap([_rec(f"C{i}") for i in range(8)]))
        cs.check_snapshot(remote, _snap([_rec(f"C{i}") for i in range(9)]))

    def test_guard_refuses_a_term_with_no_grading_tags(self) -> None:
        with self.assertRaisesRegex(cs.SnapshotError, "평가방식"):
            cs.check_snapshot(_remote(), _snap([_rec("A1")], methods={}))

    def test_upcoming_terms_skip_the_collected_term_and_reject_bad_entries(self) -> None:
        self.assertEqual(
            cs.parse_terms("2026:winter 2027:spring 2026:fall",
                           count_year="2026", count_sem="fall"),
            [("2026", WINTER), ("2027", "U000200001U000300001")])
        with self.assertRaisesRegex(ValueError, "2027:autumn"):
            cs.parse_terms("2027:autumn", count_year="", count_sem="")


class LocalConnectionTests(unittest.TestCase):
    def test_the_local_catalog_is_never_the_cloud_database(self) -> None:
        # `pull` runs with the cloud credentials in its environment; db.connect()
        # would follow TURSO_DATABASE_URL to the cloud, and init_schema there
        # rebuilt the cloud class_slots once (2026-10-08).
        with self.assertRaisesRegex(SystemExit, "local file"):
            cs.open_local("libsql://example.turso.io")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "not found"):
                cs.open_local(str(Path(tmp) / "missing.db"))

    def test_pull_cli_opens_the_dest_file_not_the_environment_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "local.db"
            sqlite3.connect(dest).close()
            opened = []
            with patch.object(cs, "open_local",
                              side_effect=lambda p: opened.append(p) or (_ for _ in ()).throw(
                                  SystemExit("stop"))), \
                 patch.object(cs, "_remote", return_value=_remote()), \
                 patch.dict("os.environ", {"TURSO_DATABASE_URL": "libsql://cloud",
                                           "DB_BACKEND": "turso"}):
                with self.assertRaises(SystemExit):
                    cs.main(["pull", "--dest", str(dest)])
            self.assertEqual(opened, [str(dest)])


class PullTests(unittest.TestCase):
    def _local(self, tmp: str):
        conn = db._connect_sqlite(Path(tmp) / "local.db")
        db.init_schema(conn)
        return conn

    def _pull(self, local, remote, **kwargs):
        with patch.object(cs.changelog, "write_run_log", return_value=None):
            return cs.pull(remote, local, **kwargs)

    def test_pull_rebuilds_a_changed_term_with_slots_and_grading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = self._local(tmp), _remote()
            slot = {"day_index": 0, "period": 1, "start_time": "09:00",
                    "end_time": "10:15", "room": "3-213"}
            cs.push(remote, _snap([_rec("A1", slots=[slot]), _rec("B2")],
                                  methods={("A1", "001", "000"): "A~F",
                                           ("B2", "001", "000"): "S/U"},
                                  switchable={("B2", "001", "000")}),
                    now="2026-10-08T05:20:00")

            out = self._pull(local, remote, now="2026-10-08T06:00:00")

            self.assertEqual(out["applied"], [("2026", WINTER)])
            rows = local.execute(
                "SELECT sbjt_cd, grading, grading_switch FROM classes ORDER BY sbjt_cd"
            ).fetchall()
            self.assertEqual([tuple(r) for r in rows],
                             [("A1", "A~F", "N"), ("B2", "S/U", "Y")])
            self.assertEqual(local.execute(
                "SELECT room FROM class_slots").fetchone()[0], "3-213")
            self.assertEqual(local.execute(
                "SELECT label FROM terms WHERE term=?", (WINTER,)).fetchone()[0],
                "2026 겨울학기")

            again = self._pull(local, remote, now="2026-10-08T12:00:00")
            self.assertEqual(again["applied"], [])

    def test_pull_reports_a_stale_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = self._local(tmp), _remote()
            cs.push(remote, _snap([_rec("A1")]), now="2026-10-06T05:20:00")

            out = self._pull(local, remote, now="2026-10-08T06:00:00", max_age_hours=30)

            self.assertEqual(out["applied"], [("2026", WINTER)])   # still applied
            self.assertEqual(out["stale"], [("2026", WINTER)])

    def test_pull_ignores_cloud_terms_that_are_no_longer_upcoming(self) -> None:
        # After a rollover the counted term's old snapshot stays in the cloud;
        # it must neither overwrite the crawled catalog nor raise a stale alert.
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = self._local(tmp), _remote()
            cs.push(remote, _snap([_rec("A1")]), now="2026-10-01T05:20:00")

            out = self._pull(local, remote, now="2026-10-08T06:00:00",
                             terms=[("2027", "U000200001U000300001")])

            self.assertEqual((out["applied"], out["stale"]), ([], []))
            self.assertEqual(local.execute("SELECT COUNT(*) FROM classes").fetchone()[0], 0)

    def test_pull_leaves_terms_that_are_not_in_the_cloud_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = self._local(tmp), _remote()
            db.upsert_term(local, "U000200002U000300001", "2026", "2026 2학기")
            db.upsert_class(local, {**_rec("K1"), "shtm_fg": "U000200002",
                                    "deta_shtm_fg": "U000300001"})
            local.commit()
            cs.push(remote, _snap([_rec("A1")]), now="2026-10-08T05:20:00")

            self._pull(local, remote, now="2026-10-08T06:00:00")

            self.assertEqual(local.execute(
                "SELECT COUNT(*) FROM classes WHERE sbjt_cd='K1'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
