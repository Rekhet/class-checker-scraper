from __future__ import annotations

import sqlite3
import unittest

from scraper import db
from scraper.cloud_collect import bootstrap_local, push_samples


def _remote_seeded() -> sqlite3.Connection:
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db.init_schema(db._Conn(raw, "sqlite"))
    raw.execute(
        "INSERT INTO terms (term, year, label) VALUES ('T1','2026','2026 2학기')"
    )
    raw.executemany(
        "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
        " lt_no, subh_cd, name, applied, quota)"
        " VALUES ('2026','U1','U2','T1',?,?,'000','c',?,30)",
        [(f"M{i:05d}", "001", i) for i in range(5)],
    )
    raw.execute(
        "INSERT INTO classes (year, shtm_fg, deta_shtm_fg, term, sbjt_cd,"
        " lt_no, subh_cd, name)"
        " VALUES ('2025','U1','U2','T1','OLD','001','000','old')"
    )
    raw.commit()
    return raw


def _local_empty() -> sqlite3.Connection:
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    return raw


class BootstrapLocalTests(unittest.TestCase):
    def test_copies_terms_and_scoped_classes(self) -> None:
        remote, local = _remote_seeded(), _local_empty()

        out = bootstrap_local(remote, local, year="2026", term="T1")

        self.assertEqual(out["classes"], 5)
        self.assertEqual(
            local.execute("SELECT COUNT(*) FROM terms").fetchone()[0], 1
        )
        self.assertEqual(
            local.execute(
                "SELECT COUNT(*) FROM classes WHERE year='2026'"
            ).fetchone()[0],
            5,
        )
        # out-of-scope class must not be copied
        self.assertEqual(
            local.execute(
                "SELECT COUNT(*) FROM classes WHERE year='2025'"
            ).fetchone()[0],
            0,
        )
        # copied rows keep their live-count columns
        row = local.execute(
            "SELECT applied, quota FROM classes WHERE sbjt_cd='M00003'"
        ).fetchone()
        self.assertEqual((row["applied"], row["quota"]), (3, 30))


class PushSamplesTests(unittest.TestCase):
    def test_pushes_all_samples_in_batched_statements(self) -> None:
        local = _local_empty()
        db.init_schema(db._Conn(local, "sqlite"))
        local.executemany(
            "INSERT INTO count_samples"
            " (year, term, sbjt_cd, lt_no, ts, applied, cart, enrolled, quota)"
            " VALUES ('2026','T1',?,?,'2026-09-01T09:00:00',?,NULL,NULL,30)",
            [(f"M{i:05d}", "001", i) for i in range(250)],
        )
        local.commit()

        raw_remote = _local_empty()
        db.init_schema(db._Conn(raw_remote, "sqlite"))
        statements: list[str] = []

        class _CountingRemote:
            def execute(self, sql, params=()):
                if sql.lstrip().upper().startswith("INSERT"):
                    statements.append(sql)
                return raw_remote.execute(sql, params)

            def commit(self):
                raw_remote.commit()

        remote = _CountingRemote()

        out = push_samples(local, remote)

        self.assertEqual(out["pushed"], 250)
        self.assertEqual(
            raw_remote.execute(
                "SELECT COUNT(*) FROM count_samples"
            ).fetchone()[0],
            250,
        )
        self.assertLessEqual(len(statements), 5)


if __name__ == "__main__":
    unittest.main()
