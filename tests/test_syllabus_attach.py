from __future__ import annotations

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

import catalog_snapshot as cs  # noqa: E402
import db  # noqa: E402
import syllabus_attach as sa  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cc103ajax"
FALL = "U000200002U000300001"
WINTER = "U000200002U000300002"
SPRING = "U000200001U000300001"
T0 = "2026-10-17T04:40:00+09:00"


def _fix(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _remote() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    sa.init_remote(conn)
    conn.execute(db.CATALOG_SNAPSHOTS_DDL)
    conn.executescript("""CREATE TABLE classes (year TEXT, term TEXT, sbjt_cd TEXT,
                          lt_no TEXT, subh_cd TEXT);""")
    return conn


def _add_classes(remote, year: str, term: str, n: int) -> list[tuple]:
    keys = [(f"S{i:03d}", "001", "000") for i in range(n)]
    remote.executemany("INSERT INTO classes VALUES (?,?,?,?,?)",
                       [(year, term, *k) for k in keys])
    remote.commit()
    return keys


KO = [["강의계획서(초급독일어1).hwp (123.5KB)", "1d8e1d3e-a4c4-4bef-a179-ef07ec4d305a", 0]]
EN = [["NOE_2026_YJ_Syllabus-GoGE.pdf (77.66KB)", "6a548e73-9a5c-4ed8-87ae-748caafe62aa", 0]]


class ParseTests(unittest.TestCase):
    def test_five_real_responses(self) -> None:
        self.assertEqual(sa.parse_attach(_fix("none")), {})
        self.assertEqual(sa.parse_attach(_fix("ko_only")), {"ko": KO})
        self.assertEqual(sa.parse_attach(_fix("en_only")), {"en": EN})
        self.assertEqual(sa.parse_attach(_fix("both")), {
            "ko": [["강의계획서(조사료학특강_국문,2026-02).hwp (120.5KB)",
                    "2d446767-426b-4a88-a60c-04f9b4f2fc14", 0]],
            "en": [["강의계획서(조사료학특강_영문,2026-02).hwp (99.5KB)",
                    "52d05b7f-e2d4-444a-9897-a44128895230", 0]]})

    def test_entry_with_an_attach_number_but_no_name_is_skipped(self) -> None:
        # empty_name.json is real: korAttachNo set, korFileNm null (sugang's own
        # page hides it; its download URL is a 404).
        raw = _fix("empty_name")["LISTTAB03_LIST_ATTACH"][0]
        self.assertTrue(raw["korAttachNo"] and not raw["korFileNm"])
        self.assertEqual(sa.parse_attach(_fix("empty_name")), {})

    def test_null_detail_number_defaults_to_zero_and_blank_name_is_skipped(self) -> None:
        resp = {"LISTTAB03_LIST_ATTACH": [
            {"korFileNm": "a.hwp (1KB)", "korAttachNo": "n1", "korAttachDetaNo": None},
            {"korFileNm": "  ", "korAttachNo": "n2", "korAttachDetaNo": 1},
            {"engFileNm": "b.pdf (1KB)", "engAttachNo": "n3", "engAttachDetaNo": 2}]}
        self.assertEqual(sa.parse_attach(resp), {"ko": [["a.hwp (1KB)", "n1", 0]],
                                                 "en": [["b.pdf (1KB)", "n3", 2]]})

    def test_missing_or_null_list_is_no_attachment(self) -> None:
        self.assertEqual(sa.parse_attach({}), {})
        self.assertEqual(sa.parse_attach({"LISTTAB03_LIST_ATTACH": None}), {})

    def test_key_pads_a_blank_subh(self) -> None:
        self.assertEqual(sa.key("A", "001", ""), "A|001|000")
        self.assertEqual(sa.key("A", "001", None), "A|001|000")
        self.assertEqual(sa.key("A", "001", "002"), "A|001|002")


class SnapshotTests(unittest.TestCase):
    def _snap(self, attach, missing=(), *, at="2026-10-17T04:40:00+09:00") -> dict:
        return sa.build_snapshot("2026", FALL, attach, list(missing), collected_at=at)

    def test_snapshot_shape_and_payload(self) -> None:
        snap = self._snap({"B|001|000": {}, "A|001|000": {"ko": KO}}, ["C|001|000"])
        self.assertEqual((snap["classes"], snap["missing"]), (2, 1))
        data = json.loads(zlib.decompress(snap["payload"]))
        self.assertEqual(data, {"year": "2026", "term": FALL, "collected_at": T0,
                                "classes": {"A|001|000": {"ko": KO}, "B|001|000": {}},
                                "missing": ["C|001|000"]})
        self.assertEqual(list(data["classes"]), sorted(data["classes"]))

    def test_digest_ignores_collected_at_and_order_but_not_names(self) -> None:
        a = self._snap({"A|001|000": {"ko": KO}, "B|001|000": {}}, ["Z|001|000", "Y|001|000"])
        b = self._snap({"B|001|000": {}, "A|001|000": {"ko": KO}}, ["Y|001|000", "Z|001|000"],
                       at="2026-10-18T04:40:00+09:00")
        self.assertEqual(a["digest"], b["digest"])
        renamed = [["다른 이름.hwp (1KB)", KO[0][1], 0]]
        c = self._snap({"A|001|000": {"ko": renamed}, "B|001|000": {}},
                       ["Z|001|000", "Y|001|000"])
        self.assertNotEqual(a["digest"], c["digest"])

    def test_push_new_changed_unchanged(self) -> None:
        remote = _remote()
        snap = self._snap({"A|001|000": {"ko": KO}})
        self.assertEqual(sa.push(remote, snap, now="2026-10-17T04:41:00+09:00"), "new")
        again = self._snap({"A|001|000": {"ko": KO}}, at="2026-10-18T04:40:00+09:00")
        self.assertEqual(sa.push(remote, again, now="2026-10-18T04:41:00+09:00"), "unchanged")
        row = remote.execute("SELECT collected_at, checked_at, classes, missing"
                             " FROM syllabus_attach_snapshots").fetchone()
        self.assertEqual(row, (T0, "2026-10-18T04:41:00+09:00", 1, 0))
        changed = self._snap({"A|001|000": {"ko": KO}, "B|001|000": {}},
                             at="2026-10-19T04:40:00+09:00")
        self.assertEqual(sa.push(remote, changed, now="2026-10-19T04:41:00+09:00"), "changed")
        row = remote.execute("SELECT digest, collected_at, classes FROM"
                             " syllabus_attach_snapshots").fetchone()
        self.assertEqual(row, (changed["digest"], "2026-10-19T04:40:00+09:00", 2))

    def test_now_is_seoul_iso(self) -> None:
        self.assertRegex(sa._now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+09:00$")


class GuardTests(unittest.TestCase):
    def _snap(self, classes: int, missing: int) -> dict:
        attach = {f"A{i:03d}|001|000": {} for i in range(classes)}
        miss = [f"M{i:03d}|001|000" for i in range(missing)]
        return sa.build_snapshot("2026", FALL, attach, miss, collected_at=T0)

    def test_failure_rate(self) -> None:
        remote = _remote()
        sa.check_snapshot(remote, self._snap(98, 2), total=100)
        with self.assertRaisesRegex(sa.SnapshotError, r"2026/\S+: 3 of 100 classes failed"):
            sa.check_snapshot(remote, self._snap(97, 3), total=100)

    def test_shrink_against_previous_row(self) -> None:
        remote = _remote()
        sa.push(remote, self._snap(100, 0), now=T0)
        with self.assertRaisesRegex(sa.SnapshotError, "previous"):
            sa.check_snapshot(remote, self._snap(89, 0), total=89)
        sa.check_snapshot(remote, self._snap(90, 0), total=90)

    def test_previous_counts_classes_plus_missing(self) -> None:
        remote = _remote()
        sa.push(remote, self._snap(98, 2), now=T0)   # previous total 100
        with self.assertRaises(sa.SnapshotError):
            sa.check_snapshot(remote, self._snap(88, 1), total=89)


class FakeHttp:
    """post(url, data, headers) -> parsed JSON; keys in `fail` raise."""

    def __init__(self, responses=None, fail=(), on_post=None):
        self.responses = responses or {}
        self.fail, self.on_post = set(fail), on_post
        self.calls: list[dict] = []

    def post(self, url, data, headers):
        self.calls.append({"url": url, "data": dict(data), "headers": dict(headers)})
        if self.on_post:
            self.on_post()
        k = (data["sbjtCd"], data["ltNo"], data["sbjtSubhCd"])
        if k in self.fail:
            raise OSError("boom")
        return self.responses.get(k, _fix("none"))


class FetchTests(unittest.TestCase):
    def test_request_shape_attach_and_missing(self) -> None:
        keys = [("A", "001", "000"), ("B", "002", "000"), ("C", "001", "000")]
        http = FakeHttp({("A", "001", "000"): _fix("ko_only")}, fail={("C", "001", "000")})
        sleeps: list[float] = []
        attach, missing = sa.fetch_term(http, "2026", FALL, keys, sleep=sleeps.append)
        self.assertEqual(attach, {"A|001|000": {"ko": KO}, "B|002|000": {}})
        self.assertEqual(missing, ["C|001|000"])
        first = http.calls[0]
        self.assertEqual(first["url"], "https://sugang.snu.ac.kr/sugang/cc/cc103ajax.action")
        self.assertEqual(first["data"], {"openSchyy": "2026", "openShtmFg": "U000200002",
                                         "openDetaShtmFg": "U000300001", "sbjtCd": "A",
                                         "ltNo": "001", "sbjtSubhCd": "000"})
        self.assertEqual(first["headers"], {"Referer": "https://sugang.snu.ac.kr/"})
        # C failed twice (one retry after 1 s); 4 requests succeeded/failed in order
        self.assertEqual(len(http.calls), 4)
        self.assertIn(1, sleeps)
        self.assertTrue(all(s in (0.1, 1) for s in sleeps))

    def test_a_response_without_the_attach_list_is_a_failure(self) -> None:
        http = FakeHttp({("A", "001", "000"): {"error": "x"}})
        attach, missing = sa.fetch_term(http, "2026", FALL, [("A", "001", "000")],
                                        sleep=lambda s: None)
        self.assertEqual((attach, missing), ({}, ["A|001|000"]))

    def test_retry_recovers(self) -> None:
        class Flaky(FakeHttp):
            def post(self, url, data, headers):
                self.calls.append(1)
                if len(self.calls) == 1:
                    raise OSError("blip")
                return _fix("both")
        http = Flaky()
        attach, missing = sa.fetch_term(http, "2026", FALL, [("A", "001", "000")],
                                        sleep=lambda s: None)
        self.assertEqual(missing, [])
        self.assertEqual(set(attach["A|001|000"]), {"ko", "en"})


class HttpTests(unittest.TestCase):
    def test_one_session_is_reused_and_cookies_are_not_sent_back(self) -> None:
        sessions = []

        class FakeResp:
            def raise_for_status(self): pass
            def json(self): return {"LISTTAB03_LIST_ATTACH": []}

        class FakeJar:
            cleared = 0
            def clear(self): FakeJar.cleared += 1

        class FakeSession:
            def __init__(self):
                sessions.append(self)
                self.cookies, self.calls = FakeJar(), []
            def post(self, url, data=None, headers=None, timeout=None):
                self.calls.append((url, data, headers, timeout))
                return FakeResp()

        with patch.object(sa.requests, "Session", FakeSession):
            http = sa.Http()
            a = http.post(sa.URL, {"x": "1"}, sa.HEADERS)
            http.post(sa.URL, {"x": "2"}, sa.HEADERS)
        self.assertEqual(a, {"LISTTAB03_LIST_ATTACH": []})
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].calls[0], (sa.URL, {"x": "1"}, sa.HEADERS, 30))
        self.assertEqual(FakeJar.cleared, 2)


class RosterTests(unittest.TestCase):
    def test_target_terms(self) -> None:
        env = {"COUNT_YEAR": "2026", "COUNT_SEM": "fall",
               "UPCOMING_TERMS": "2026:winter 2027:spring 2026:fall"}
        self.assertEqual(sa.target_terms(env), [("2026", FALL), ("2026", WINTER),
                                                ("2027", SPRING)])
        self.assertEqual(sa.target_terms({"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}),
                         [("2026", FALL)])
        with self.assertRaises(ValueError):
            sa.target_terms({"COUNT_YEAR": "2026", "COUNT_SEM": "autumn"})

    def test_past_terms_oldest_first_from_2020_without_targets(self) -> None:
        remote = _remote()
        for y, t in [("2019", FALL), ("2021", SPRING), ("2020", FALL), ("2026", FALL)]:
            _add_classes(remote, y, t, 1)
        self.assertEqual(sa.past_terms(remote, [("2026", FALL)]),
                         [("2020", FALL), ("2021", SPRING)])

    def test_roster_prefers_cloud_classes_then_catalog_snapshot(self) -> None:
        remote = _remote()
        remote.executemany("INSERT INTO classes VALUES (?,?,?,?,?)", [
            ("2026", FALL, "B", "001", "000"), ("2026", FALL, "A", "001", ""),
            ("2026", FALL, "A", "001", ""), ("2025", FALL, "Z", "001", "000")])
        self.assertEqual(sa.roster(remote, "2026", FALL),
                         [("A", "001", "000"), ("B", "001", "000")])
        payload = zlib.compress(json.dumps({"classes": [
            {"sbjt_cd": "W2", "lt_no": "001", "subh_cd": "000"},
            {"sbjt_cd": "W1", "lt_no": "002", "subh_cd": ""}]}).encode())
        remote.execute("INSERT INTO catalog_snapshots VALUES"
                       "('2026',?, 'l','d',2,'t','t',?)", (WINTER, payload))
        self.assertEqual(sa.roster(remote, "2026", WINTER),
                         [("W1", "002", "000"), ("W2", "001", "000")])
        self.assertEqual(sa.roster(remote, "2030", WINTER), [])


class CollectTests(unittest.TestCase):
    def _setup(self, n: int):
        remote = _remote()
        keys = _add_classes(remote, "2026", FALL, n)
        return remote, keys

    def _collect(self, terms, remote, http, **kw):
        opened = {"n": 0, "open": 0, "max_open_during_post": 0, "sleeps": []}

        def factory():
            opened["n"] += 1
            return _Tracked(remote, opened)
        http.on_post = lambda: opened.__setitem__(
            "max_open_during_post", max(opened["max_open_during_post"], opened["open"]))
        with patch.object(sa.time, "sleep", opened["sleeps"].append):
            rc = sa.collect(terms, http=http, remote_factory=factory, **kw)
        return rc, opened

    def test_one_failure_in_fifty_is_pushed(self) -> None:
        remote, keys = self._setup(50)
        rc, _ = self._collect([("2026", FALL)], remote, FakeHttp(fail={keys[7]}))
        self.assertEqual(rc, 0)
        row = remote.execute("SELECT classes, missing FROM syllabus_attach_snapshots"
                             ).fetchone()
        self.assertEqual(row, (49, 1))

    def test_two_failures_in_fifty_are_not_pushed(self) -> None:
        remote, keys = self._setup(50)
        rc, _ = self._collect([("2026", FALL)], remote, FakeHttp(fail={keys[1], keys[2]}))
        self.assertEqual(rc, 1)
        self.assertEqual(remote.execute(
            "SELECT COUNT(*) FROM syllabus_attach_snapshots").fetchone()[0], 0)

    def test_remote_is_never_open_during_fetches(self) -> None:
        remote, _ = self._setup(5)
        rc, opened = self._collect([("2026", FALL)], remote, FakeHttp())
        self.assertEqual(rc, 0)
        self.assertEqual(opened["max_open_during_post"], 0)
        self.assertGreaterEqual(opened["n"], 2)   # roster read, then push

    def test_dry_run_prints_and_writes_nothing(self) -> None:
        remote, keys = self._setup(100)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc, _ = self._collect([("2026", FALL)], remote, FakeHttp(fail={keys[0]}),
                                  dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn(f"2026/{FALL}: 99 classes, roster 100, missing 1", buf.getvalue())
        self.assertEqual(remote.execute(
            "SELECT COUNT(*) FROM syllabus_attach_snapshots").fetchone()[0], 0)

    def test_skip_existing_unless_force(self) -> None:
        remote, _ = self._setup(5)
        _add_classes(remote, "2025", FALL, 5)
        self._collect([("2025", FALL)], remote, FakeHttp())
        http = FakeHttp()
        rc, _ = self._collect([("2025", FALL), ("2026", FALL)], remote, http,
                              skip_existing=True)
        self.assertEqual(rc, 0)
        self.assertEqual({c["data"]["openSchyy"] for c in http.calls}, {"2026"})
        http = FakeHttp()
        self._collect([("2025", FALL)], remote, http, skip_existing=True, force=True)
        self.assertEqual(len(http.calls), 5)

    def test_each_term_is_stored_as_it_completes(self) -> None:
        remote = _remote()
        for y in ("2021", "2022", "2023"):
            _add_classes(remote, y, FALL, 3)

        class Crash(FakeHttp):
            def post(self, url, data, headers):
                if data["openSchyy"] == "2023":
                    raise KeyboardInterrupt   # not an Exception: aborts the run
                return super().post(url, data, headers)
        with self.assertRaises(KeyboardInterrupt):
            self._collect([(y, FALL) for y in ("2021", "2022", "2023")], remote, Crash())
        self.assertEqual([r[0] for r in remote.execute(
            "SELECT year FROM syllabus_attach_snapshots ORDER BY year")], ["2021", "2022"])

    def test_over_two_percent_aborts_early_waits_and_restarts_once(self) -> None:
        remote, keys = self._setup(100)
        http = FakeHttp(fail=set(keys))
        rc, opened = self._collect([("2026", FALL)], remote, http)
        self.assertEqual(rc, 1)
        # 3 failed classes end a pass (2 requests each): 2 passes, never 100 classes
        self.assertEqual(len(http.calls), 12)
        self.assertEqual(opened["sleeps"].count(60), 1)
        self.assertEqual(remote.execute(
            "SELECT COUNT(*) FROM syllabus_attach_snapshots").fetchone()[0], 0)

    def test_restart_after_a_bad_first_pass_recovers(self) -> None:
        remote, keys = self._setup(100)

        class FirstPassBad(FakeHttp):
            def post(self, url, data, headers):
                if len(self.calls) < 6:   # first pass: 3 classes x (try + retry)
                    self.calls.append(1)
                    raise OSError("blocked")
                return super().post(url, data, headers)
        rc, opened = self._collect([("2026", FALL)], remote, FirstPassBad())
        self.assertEqual(rc, 0)
        self.assertEqual(remote.execute("SELECT classes, missing FROM"
                                        " syllabus_attach_snapshots").fetchone(), (100, 0))
        self.assertIn(60, opened["sleeps"])

    def test_a_failed_term_does_not_stop_later_terms(self) -> None:
        remote = _remote()
        bad = _add_classes(remote, "2021", FALL, 100)
        _add_classes(remote, "2022", FALL, 3)

        class BadYear(FakeHttp):
            def post(self, url, data, headers):
                if data["openSchyy"] == "2021":
                    raise OSError("boom")
                return super().post(url, data, headers)
        rc, _ = self._collect([("2021", FALL), ("2022", FALL)], remote, BadYear())
        self.assertEqual(rc, 1)
        self.assertEqual([r[0] for r in remote.execute(
            "SELECT year FROM syllabus_attach_snapshots")], ["2022"])
        self.assertTrue(bad)

    def test_tolerated_failures_carry_the_previous_entry(self) -> None:
        remote, keys = self._setup(100)
        prev = sa.build_snapshot("2026", FALL, {sa.key(*keys[7]): {"ko": KO}}, [],
                                 collected_at=T0)
        sa.push(remote, prev, now=T0)
        rc, _ = self._collect([("2026", FALL)], remote,
                              FakeHttp(fail={keys[7], keys[8]}))
        self.assertEqual(rc, 0)
        data = json.loads(zlib.decompress(remote.execute(
            "SELECT payload FROM syllabus_attach_snapshots").fetchone()[0]))
        self.assertEqual(data["classes"][sa.key(*keys[7])], {"ko": KO})   # carried
        self.assertNotIn(sa.key(*keys[8]), data["classes"])               # no previous
        self.assertEqual(data["missing"], [sa.key(*keys[8])])

    def test_a_carried_class_keeps_an_unchanged_term_unchanged(self) -> None:
        import io
        from contextlib import redirect_stdout
        remote, keys = self._setup(100)
        first = FakeHttp({keys[7]: _fix("ko_only")})
        self._collect([("2026", FALL)], remote, first)
        before = remote.execute("SELECT digest, collected_at FROM"
                                " syllabus_attach_snapshots").fetchone()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc, _ = self._collect([("2026", FALL)], remote,
                                  FakeHttp({keys[7]: _fix("ko_only")}, fail={keys[7]}))
        self.assertEqual(rc, 0)
        self.assertIn("unchanged", buf.getvalue())
        self.assertEqual(remote.execute("SELECT digest, collected_at FROM"
                                        " syllabus_attach_snapshots").fetchone(), before)

    def test_force_bypasses_the_shrink_guard_only(self) -> None:
        remote, keys = self._setup(50)
        big = sa.build_snapshot("2026", FALL, {f"X{i:03d}|001|000": {} for i in range(100)},
                                [], collected_at=T0)
        sa.push(remote, big, now=T0)
        rc, _ = self._collect([("2026", FALL)], remote, FakeHttp())
        self.assertEqual(rc, 1)
        rc, _ = self._collect([("2026", FALL)], remote, FakeHttp(), force=True)
        self.assertEqual(rc, 0)
        self.assertEqual(remote.execute(
            "SELECT classes FROM syllabus_attach_snapshots").fetchone()[0], 50)

    def test_empty_roster_is_an_error(self) -> None:
        remote = _remote()
        rc, _ = self._collect([("2026", FALL)], remote, FakeHttp())
        self.assertEqual(rc, 1)


class _Tracked:
    """sqlite connection whose close() is counted; stands in for a libsql one."""

    def __init__(self, conn, counters):
        self._c, self._k = conn, counters
        counters["open"] += 1
        self._closed = False

    def close(self):
        if not self._closed:
            self._closed = True
            self._k["open"] -= 1

    def __getattr__(self, name):
        return getattr(self._c, name)


class PullTests(unittest.TestCase):
    def _local(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        return conn

    def _snap(self, term: str, name: str = "a.hwp (1KB)") -> dict:
        return sa.build_snapshot("2026", term, {"A|001|000": {"ko": [[name, "n", 0]]}}, [],
                                 collected_at=T0)

    def test_changed_digest_is_copied_equal_is_skipped(self) -> None:
        remote, local = _remote(), self._local()
        sa.push(remote, self._snap(FALL), now=T0)
        out = sa.pull(remote, local, now=T0, targets=[("2026", FALL)])
        self.assertEqual(out, {"terms": 1, "applied": [("2026", FALL)], "stale": [],
                               "uncollected": []})
        row = local.execute("SELECT digest, classes, missing, checked_at, payload FROM"
                            " syllabus_attach_snapshots").fetchone()
        self.assertEqual(row[:4], (self._snap(FALL)["digest"], 1, 0, T0))
        self.assertEqual(row[4], self._snap(FALL)["payload"])
        again = sa.pull(remote, local, now=T0, targets=[("2026", FALL)])
        self.assertEqual(again["applied"], [])
        sa.push(remote, self._snap(FALL, "b.hwp (1KB)"), now=T0)
        self.assertEqual(sa.pull(remote, local, now=T0, targets=[])["applied"],
                         [("2026", FALL)])

    def test_stale_covers_only_targets(self) -> None:
        remote, local = _remote(), self._local()
        sa.push(remote, self._snap(FALL), now="2026-10-10T04:40:00+09:00")
        sa.push(remote, self._snap(WINTER), now="2026-10-10T04:40:00+09:00")
        out = sa.pull(remote, local, now="2026-10-17T04:40:00+09:00",
                      targets=[("2026", WINTER)])
        self.assertEqual(out["stale"], [("2026", WINTER)])
        self.assertEqual(len(out["applied"]), 2)   # past term still copied
        fresh = sa.pull(remote, local, now="2026-10-10T05:00:00+09:00",
                        targets=[("2026", WINTER)])
        self.assertEqual(fresh["stale"], [])

    def test_a_target_term_with_no_cloud_row_is_stale(self) -> None:
        remote, local = _remote(), self._local()
        sa.push(remote, self._snap(FALL), now=T0)
        out = sa.pull(remote, local, now=T0, targets=[("2026", FALL), ("2026", WINTER)])
        self.assertEqual(out["uncollected"], [("2026", WINTER)])   # its own list
        self.assertEqual(out["stale"], [])

    def test_exactly_48_hours_is_not_stale(self) -> None:
        remote, local = _remote(), self._local()
        sa.push(remote, self._snap(FALL), now="2026-10-15T04:40:00+09:00")
        out = sa.pull(remote, local, now="2026-10-17T04:40:00+09:00",
                      targets=[("2026", FALL)])
        self.assertEqual(out["stale"], [])


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.local = _KeepOpen(_Wrap(sqlite3.connect(":memory:")))   # persists across runs

    def _run(self, argv, remote, **env):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "local.db"
            sqlite3.connect(dest).close()
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            self.addCleanup(lambda: None)
            with redirect_stderr(buf), patch.object(sa, "open_local", return_value=self.local), \
                 patch.object(sa, "_remote", return_value=_KeepOpen(remote)), \
                 patch.dict("os.environ", {"COUNT_YEAR": "2026", "COUNT_SEM": "fall",
                                           "UPCOMING_TERMS": "", **env}):
                try:
                    return sa.main(argv + ["--dest", str(dest)] if argv[0] == "pull" else argv)
                finally:
                    self.err = buf.getvalue()

    def test_exit_codes(self) -> None:
        remote = _remote()
        self.assertEqual(self._run(["pull"], remote), 3)   # target term never collected
        self.assertIn("no syllabus snapshot collected yet", self.err)
        self.assertNotIn("not refreshed", self.err)
        snap = sa.build_snapshot("2026", FALL, {"A|001|000": {}}, [], collected_at=T0)
        sa.push(remote, snap, now=sa._now())
        self.assertEqual(self._run(["pull"], remote), 4)
        self.assertEqual(self._run(["pull"], remote), 0)
        sa.push(remote, snap, now="2020-01-01T00:00:00+09:00")
        self.assertEqual(self._run(["pull"], remote), 3)
        self.assertIn("not refreshed for over 48 h", self.err)
        self.assertNotIn("no syllabus snapshot", self.err)

    def test_dest_must_be_a_local_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "local file"):
            sa.main(["pull", "--dest", "libsql://example.turso.io"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "not found"):
                sa.main(["pull", "--dest", str(Path(tmp) / "missing.db")])

    def test_contradictory_or_empty_options_are_rejected(self) -> None:
        remote = _remote()
        for argv in (["collect", "--terms", ""], ["collect", "--terms", "  "],
                     ["collect", "--past", "--terms", "2026:winter"],
                     ["collect", "--force", "--dry-run"],
                     ["pull", "--max-age-hours", "0"]):
            with self.subTest(argv=argv), patch.object(sa, "_remote", return_value=remote), \
                 patch.dict("os.environ", {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}):
                with self.assertRaises(SystemExit) as cm:
                    sa.main(argv)
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(remote.execute(
            "SELECT COUNT(*) FROM syllabus_attach_snapshots").fetchone()[0], 0)

    def test_terms_naming_only_the_counted_term_is_an_error(self) -> None:
        with patch.object(sa, "_remote", return_value=_KeepOpen(_remote())), \
             patch.dict("os.environ", {"COUNT_YEAR": "2026", "COUNT_SEM": "fall"}):
            self.assertEqual(sa.main(["collect", "--terms", "2026:fall"]), 2)

    def test_only_config_errors_map_to_exit_2_in_pull(self) -> None:
        remote = _remote()
        with patch.object(sa, "pull", side_effect=ValueError("bad date")):
            with self.assertRaisesRegex(ValueError, "bad date"):
                self._run(["pull"], remote)

    def test_init_remote_creates_the_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        with patch.object(sa, "_remote", return_value=_KeepOpen(conn)):
            self.assertEqual(sa.main(["init-remote"]), 0)
        conn.execute("SELECT * FROM syllabus_attach_snapshots")

    def test_the_schema_has_the_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(syllabus_attach_snapshots)")]
        self.assertEqual(cols, ["year", "term", "digest", "classes", "missing",
                                "collected_at", "checked_at", "payload"])


class _KeepOpen:
    """Remote stand-in whose close() leaves the in-memory database usable."""

    def __init__(self, conn):
        self._c = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._c, name)


class _Wrap:
    """Local stand-in: a sqlite connection with db.init_schema's tables."""

    def __init__(self, conn):
        self._c = conn
        conn.executescript(db.SCHEMA)

    def __getattr__(self, name):
        return getattr(self._c, name)


if __name__ == "__main__":
    unittest.main()
