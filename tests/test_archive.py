"""P1F: unveränderliche Archivierung gegen die simulierte Data API (kein echtes Netzwerk)."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import _support
from _support import (AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SECRET_MARKERS, TT_SLUG,
                      FakeDataApi, make_testtag_workbook)

from testtag_public import archive as ar
from testtag_public import database_publisher as dp
from testtag_public import publisher
from testtag_public import supabase_database as db
from testtag_public.excel_publication import read_program_source

T1, T2, T3 = "2026-10-09T08:00:00Z", "2026-10-09T09:00:00Z", "2026-10-09T10:00:00Z"
AK5_SCORES = {(1, "Boden", 1): (5, 1.5, 7.5), (2, "Pferd", 1): (4, 1, 4), (1, "Kraft", 1): (None, 1, 8)}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.config = db.load_config(TEST_ENV)
        self.n = 0

    def sources(self, ak5: dict | None = None, ak9: dict | None = None) -> list:
        out = []
        for program, changes in ((AK5, ak5), (AK9, ak9)):
            self.n += 1
            changes = {"scores": AK5_SCORES if program == AK5 else {}, **(changes or {})}
            path = make_testtag_workbook(self.dir / f"wb{self.n}.xlsx", program, **changes)
            out.append(read_program_source(path, wait_seconds=0, sleep=lambda _s: None))
        return out

    def published_api(self, ak9_active=True) -> tuple[FakeDataApi, str]:
        api = FakeDataApi()
        dp.publish(self.config, self.sources(ak9={"public": {"public_active": ak9_active}}), observed_at=T1,
                   transport=api)
        return api, next(iter(api.testtage))

    def writes(self, api: FakeDataApi, since: int = 0) -> list:
        return [c for c in api.calls[since:] if c["method"] != "GET"]


class ArchiveFlowTests(Base):
    def test_successful_archive_inserts_once_then_sets_status(self):
        api, tid = self.published_api()
        dp.publish(self.config, self.sources(), observed_at=T1, transport=api)
        api.add_row(tid, "probe", AK5, "", {"schema_version": "probe-1", "x": 1})
        before = len(api.calls)
        outcome = ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        self.assertEqual((outcome.status, outcome.snapshot_neu), ("archiviert", True))
        self.assertEqual(api.testtage[tid]["status"], "archiviert")
        steps = [(c["method"], c["table"]) for c in self.writes(api, before)]
        self.assertEqual(steps, [("POST", "archiv_snapshots"), ("PATCH", "testtage")])  # Snapshot vor Status
        self.assertEqual(outcome.publikation.status, "unveraendert")
        snap = api.snapshots[tid]
        self.assertEqual(snap["schema_version"], ar.ARCHIV_SCHEMA_VERSION)
        self.assertEqual(snap["pruefsumme"], ar.archive_checksum(snap["inhalt"]))
        entries = ar.verify_archive(snap["inhalt"], tid)
        self.assertEqual(len(entries), 13)
        self.assertFalse(any(e["bereich"] == "probe" for e in entries))
        self.assertEqual(snap["inhalt"]["testtag"]["slug"], TT_SLUG)
        for call in api.calls[before:]:
            self.assertNotIn("authorization", {k.lower() for k in call["headers"]})
            if call["method"] != "GET":
                self.assertEqual(call["headers"]["apikey"], TEST_SECRET_KEY)
        self.assertTrue(any(c["table"] == "archiv_snapshots" and c["headers"]["apikey"] == TEST_PUBLISHABLE_KEY
                            for c in api.calls[before:]))  # öffentliches Rücklesen

    def test_pending_changes_are_published_before_archiving(self):
        api, tid = self.published_api()
        changed = {**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)}
        outcome = ar.archive(self.config, self.sources(ak5={"scores": changed}), observed_at=T2, transport=api)
        self.assertEqual(outcome.publikation.status, "publiziert")
        live = next(e for e in api.snapshots[tid]["inhalt"]["veroeffentlichung"]
                    if e["bereich"] == "resultate_live" and e["ak"] == "AK 7")
        self.assertIn(5, [r.get("T") for r in live["inhalt"]["gruppen"][0]["resultate"]])

    def test_tombstones_are_not_archived(self):
        api, tid = self.published_api()
        dp.publish(self.config, self.sources(ak9={"public": {"public_active": False}}), observed_at=T2, transport=api)
        ar.archive(self.config, self.sources(ak9={"public": {"public_active": False}}), observed_at=T3, transport=api)
        entries = api.snapshots[tid]["inhalt"]["veroeffentlichung"]
        self.assertTrue(entries)
        self.assertFalse(any(dp.is_tombstone(e["inhalt"]) or e["programm"] == AK9 for e in entries))

    def test_identical_repeat_is_idempotent_without_writes(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        snapshot = json.dumps(api.snapshots, sort_keys=True)
        before = len(api.calls)
        outcome = ar.archive(self.config, self.sources(), observed_at=T3, transport=api)
        self.assertEqual((outcome.status, outcome.schreibanfragen), ("bereits_archiviert", 0))
        self.assertEqual(self.writes(api, before), [])
        self.assertEqual(json.dumps(api.snapshots, sort_keys=True), snapshot)

    def test_divergent_existing_snapshot_is_rejected(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        api.testtage[tid]["status"] = "live"  # Snapshot vorhanden, Status nicht abgeschlossen …
        api.rows[(tid, "zeitplan", "", "")]["inhalt"] = copy.deepcopy(api.rows[(tid, "zeitplan", "", "")]["inhalt"])
        api.rows[(tid, "zeitplan", "", "")]["inhalt"]["gruppen"] = []  # … und abweichender Publikationsstand
        api.rows[(tid, "zeitplan", "", "")]["pruefsumme"] = dp.content_checksum(
            api.rows[(tid, "zeitplan", "", "")]["inhalt"])
        snapshot = json.dumps(api.snapshots, sort_keys=True)
        before = len(api.calls)
        with self.assertRaises(dp.PublisherError) as ctx:
            ar.archive(self.config, self.sources(), observed_at=T3, transport=api)
        self.assertEqual(ctx.exception.code, ar.ArchivCode.ARCHIV_ABWEICHUNG)
        self.assertEqual(self.writes(api, before), [])
        self.assertEqual(json.dumps(api.snapshots, sort_keys=True), snapshot)
        self.assertEqual(api.testtage[tid]["status"], "live")

    def test_resume_after_abort_between_snapshot_and_status(self):
        api, tid = self.published_api()
        api.fail_archive_status = 503
        with self.assertRaises(dp.PublisherError):
            ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        self.assertIn(tid, api.snapshots)
        self.assertEqual(api.testtage[tid]["status"], "live")
        snapshot = json.dumps(api.snapshots, sort_keys=True)
        api.fail_archive_status = None
        before = len(api.calls)
        outcome = ar.archive(self.config, self.sources(), observed_at=T3, transport=api)
        self.assertEqual((outcome.status, outcome.snapshot_neu), ("fortgesetzt", False))
        self.assertIsNone(outcome.publikation)  # bei bestehendem Snapshot keine neue Publikation
        self.assertEqual([(c["method"], c["table"]) for c in self.writes(api, before)], [("PATCH", "testtage")])
        self.assertEqual(api.testtage[tid]["status"], "archiviert")
        self.assertEqual(json.dumps(api.snapshots, sort_keys=True), snapshot)

    def test_no_publication_after_archiving(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        rows = json.dumps(sorted(map(list, api.rows.items())), sort_keys=True)
        before = len(api.calls)
        with self.assertRaises(dp.PublisherError) as ctx:
            dp.publish(self.config, self.sources(ak5={"scores": {**AK5_SCORES, (2, "Pferd", 1): (6, 1, 6)}}),
                       observed_at=T3, transport=api)
        self.assertEqual(ctx.exception.code, dp.PublisherCode.TESTTAG_ARCHIVIERT)
        self.assertEqual(self.writes(api, before), [])
        self.assertEqual(json.dumps(sorted(map(list, api.rows.items())), sort_keys=True), rows)

    def test_snapshot_is_never_updated_or_deleted(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        ar.archive(self.config, self.sources(), observed_at=T3, transport=api)
        self.assertFalse(any(c["table"] == "archiv_snapshots" and c["method"] in ("PATCH", "DELETE")
                             for c in api.calls))
        self.assertFalse(any(c["method"] == "DELETE" for c in api.calls))
        status, _ = api("PATCH", db.table_url(self.config, "archiv_snapshots", [("oeffentliche_testtag_id", f"eq.{tid}")]),
                        {"apikey": TEST_SECRET_KEY}, b'{"pruefsumme":"x"}', 5)
        self.assertEqual(status, 403)  # auch die simulierten Rechte erlauben kein UPDATE

    def test_empty_or_unknown_testtag_is_not_archived(self):
        api = FakeDataApi()
        locked = self.sources(ak5={"public": {"public_active": False}}, ak9={"public": {"public_active": False}})
        with self.assertRaises(dp.PublisherError) as ctx:
            ar.archive(self.config, locked, observed_at=T1, transport=api)
        self.assertEqual(ctx.exception.code, ar.ArchivCode.ARCHIV_LEER)
        self.assertEqual(api.calls, [])
        with self.assertRaises(dp.PublisherError) as ctx:
            ar.archive(self.config, self.sources(), observed_at=T1, transport=api)
        self.assertEqual(ctx.exception.code, ar.ArchivCode.ARCHIV_TESTTAG_FEHLT)
        self.assertEqual(self.writes(api), [])

    def test_hide_makes_archived_testtag_private(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        was_public, _http, writes = ar.hide(self.config, TT_SLUG, transport=api)
        self.assertEqual((was_public, writes), (True, 1))
        self.assertFalse(api.testtage[tid]["ist_oeffentlich"])
        self.assertEqual(api.testtage[tid]["status"], "archiviert")
        self.assertEqual(ar.hide(self.config, TT_SLUG, transport=api)[2], 0)


class ArchiveFormatTests(Base):
    def test_verify_archive_rejects_tampering(self):
        api, tid = self.published_api()
        ar.archive(self.config, self.sources(), observed_at=T2, transport=api)
        good = api.snapshots[tid]

        def broken(change):
            row = copy.deepcopy(good)
            change(row)
            return row

        def add_probe(r):
            r["inhalt"]["veroeffentlichung"].append({"bereich": "probe", "programm": AK5, "ak": "x", "pruefsumme": "0" * 64,
                                                     "stand_am": T1, "inhalt": {"schema_version": "probe-1"}})

        def add_tombstone(r):
            t = dp.tombstone_inhalt("resultate_live")
            r["inhalt"]["veroeffentlichung"].append({"bereich": "resultate_live", "programm": AK5, "ak": "AK 99",
                                                     "pruefsumme": dp.content_checksum(t), "stand_am": T1, "inhalt": t})

        def duplicate(r):
            r["inhalt"]["veroeffentlichung"].append(copy.deepcopy(r["inhalt"]["veroeffentlichung"][0]))

        def value(r):
            r["inhalt"]["veroeffentlichung"][0]["inhalt"]["gruppen"] = []

        cases = {"PRUEFSUMME": [value, lambda r: r.update(pruefsumme="0" * 64)],
                 "BEREICH": [add_probe], "TOMBSTONE": [add_tombstone], "DOPPELT": [duplicate],
                 "SCHEMA": [lambda r: r.update(schema_version="archiv-0")],
                 "TESTTAG": [lambda r: r.update(oeffentliche_testtag_id="0" * 8 + "-0000-4000-8000-" + "0" * 12)]}
        for code, changes in cases.items():
            for change in changes:
                with self.subTest(code=code):
                    row = broken(change)
                    if code not in ("PRUEFSUMME", "SCHEMA", "TESTTAG"):
                        row["pruefsumme"] = ar.archive_checksum(row["inhalt"])  # gültige Hülle, ungültiger Inhalt
                    with self.assertRaises(ar.ArchiveFormatError) as ctx:
                        ar.verify_snapshot_row(row, tid)
                    self.assertEqual(ctx.exception.code, code)
        self.assertEqual(ar.verify_snapshot_row(good, tid), good["inhalt"])


class ArchiveCliTests(Base):
    def setUp(self):
        super().setUp()
        self.ak5 = make_testtag_workbook(self.dir / "ak5.xlsx", AK5, scores=AK5_SCORES)
        self.ak9 = make_testtag_workbook(self.dir / "ak9.xlsx", AK9)

    def run_cli(self, *extra, api):
        out, err = io.StringIO(), io.StringIO()
        code = publisher.run(["--workbook-ak5", str(self.ak5), "--workbook-ak9", str(self.ak9), "--stability-seconds",
                              "0", *extra], env=TEST_ENV, transport=api, sleep=lambda _s: None,
                             now=lambda: datetime(2026, 10, 9, 12, tzinfo=timezone.utc), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_archive_requires_both_confirmations(self):
        api = FakeDataApi()
        self.run_cli("--publish", "--confirm-slug", TT_SLUG, api=api)
        before = len(api.calls)
        for extra in (("--archive",), ("--archive", "--confirm-slug", TT_SLUG), ("--archive", "--confirm-archive"),
                      ("--confirm-archive", "--confirm-slug", TT_SLUG), ("--confirm-archive",),
                      ("--archive", "--confirm-archive", "--confirm-slug", "anderer-slug"),
                      ("--archive", "--publish", "--confirm-archive", "--confirm-slug", TT_SLUG)):
            with self.subTest(extra=extra):
                code, out, err = self.run_cli(*extra, api=api)
                self.assertEqual(code, 2, out + err)
        self.assertEqual(self.writes(api, before), [])
        self.assertEqual(api.snapshots, {})

    def test_archive_cli_success_repeat_and_safe_output(self):
        api = FakeDataApi()
        self.run_cli("--publish", "--confirm-slug", TT_SLUG, api=api)
        code, out, err = self.run_cli("--archive", "--confirm-archive", "--confirm-slug", TT_SLUG, api=api)
        self.assertEqual(code, 0, err)
        self.assertIn("archiviert – Snapshot neu eingefügt und geprüft", out)
        code, out2, err = self.run_cli("--archive", "--confirm-archive", "--confirm-slug", TT_SLUG, api=api)
        self.assertEqual(code, 0, err)
        self.assertIn("bereits archiviert – identischer Snapshot, nichts geschrieben", out2)
        self.assertIn("Schreibanfragen:     0", out2)
        code, out3, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG, api=api)
        self.assertEqual(code, 4)
        self.assertIn("TESTTAG_ARCHIVIERT", err)
        for text in (out, out2, out3, err):
            for marker in (*TT_SECRET_MARKERS, TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY, str(self.dir)):
                self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
