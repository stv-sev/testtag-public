"""P1G: Schreibschutz auf Transportebene (SlugScope) – Schreiben nur für den bestätigten Testtag."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support
from _support import AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SLUG, FakeDataApi, make_testtag_workbook

from testtag_public import archive as ar
from testtag_public import database_publisher as dp
from testtag_public import publisher
from testtag_public import supabase_database as db
from testtag_public.excel_publication import read_program_source
from testtag_public.write_scope import SlugScope

FREMD = "aargau-produktiv-2026"
SCORES = {(1, "Boden", 1): (5, 1.5, 7.5)}


class SlugScopeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.config = db.load_config(TEST_ENV)
        self.api = FakeDataApi()
        self.fremd_id = self.api.add_testtag(FREMD, status="vorbereitet")
        self.api.add_row(self.fremd_id, "probe", AK5, "", {"schema_version": "probe-1"})

    def sources(self, **ak5):
        paths = [make_testtag_workbook(self.dir / f"{p[:3]}.xlsx", p, scores=SCORES if p == AK5 else {}, **(ak5 if p == AK5 else {}))
                 for p in (AK5, AK9)]
        return [read_program_source(p, wait_seconds=0, sleep=lambda _s: None) for p in paths]

    def before_fremd(self):
        return json.dumps([self.api.testtage[self.fremd_id], sorted(map(list, ((k, v) for k, v in self.api.rows.items()
                                                                              if k[0] == self.fremd_id)))],
                          sort_keys=True)

    def call(self, scope, method, table, params, body):
        return scope(method, db.table_url(self.config, table, params), db._headers(TEST_SECRET_KEY, body=body is not None),
                     None if body is None else json.dumps(body).encode(), 5)

    def test_normal_flows_work_through_the_scope(self):
        scope = SlugScope(self.api, TT_SLUG)
        outcome = dp.publish(self.config, self.sources(), observed_at="2026-10-09T08:00:00Z", transport=scope)
        self.assertEqual(outcome.status, "publiziert")
        ar.archive(self.config, self.sources(), observed_at="2026-10-09T09:00:00Z", transport=scope)
        ar.hide(self.config, TT_SLUG, transport=scope)
        self.assertEqual(scope.blocked, 0)
        tid = next(i for i, t in self.api.testtage.items() if t["slug"] == TT_SLUG)
        self.assertEqual((self.api.testtage[tid]["status"], self.api.testtage[tid]["ist_oeffentlich"]),
                         ("archiviert", False))

    def test_foreign_testtag_writes_are_never_sent(self):
        scope = SlugScope(self.api, TT_SLUG)
        dp.publish(self.config, self.sources(), observed_at="2026-10-09T08:00:00Z", transport=scope)
        snapshot = self.before_fremd()
        sent = len(self.api.calls)
        row = {"oeffentliche_testtag_id": self.fremd_id, "bereich": "zeitplan", "programm": "", "ak": "",
               "pruefsumme": "0" * 64, "stand_am": "2026-10-09T08:00:00Z", "inhalt": {}}
        attempts = [
            ("POST", "testtage", [], {"slug": FREMD, "titel": "x"}),
            ("PATCH", "testtage", [("id", f"eq.{self.fremd_id}")], {"status": "live"}),
            ("PATCH", "testtage", [("slug", f"eq.{FREMD}")], {"status": "live"}),
            ("PATCH", "testtage", [("id", "neq.0")], {"ist_oeffentlich": False}),
            ("POST", "veroeffentlichung", [("on_conflict", "oeffentliche_testtag_id,bereich,programm,ak")], [row]),
            ("POST", "archiv_snapshots", [], {**row, "schema_version": "archiv-1"}),
            ("PATCH", "veroeffentlichung", [("oeffentliche_testtag_id", f"eq.{self.fremd_id}")], {"inhalt": {}}),
            ("DELETE", "veroeffentlichung", [("oeffentliche_testtag_id", f"eq.{self.fremd_id}")], None),
            ("DELETE", "testtage", [("id", f"eq.{self.fremd_id}")], None),
            ("PATCH", "archiv_snapshots", [], {"pruefsumme": "x"}),
        ]
        for method, table, params, body in attempts:
            with self.subTest(method=method, table=table), self.assertRaises(db.TransportError) as ctx:
                self.call(scope, method, table, params, body)
            self.assertIn("Schreibschutz", str(ctx.exception))
        self.assertEqual(len(self.api.calls), sent)  # keine dieser Anfragen wurde gesendet
        self.assertEqual(scope.blocked, len(attempts))
        self.assertEqual(self.before_fremd(), snapshot)

    def test_mixed_batch_and_authorization_header_are_rejected(self):
        scope = SlugScope(self.api, TT_SLUG)
        dp.publish(self.config, self.sources(), observed_at="2026-10-09T08:00:00Z", transport=scope)
        tid = next(iter(scope.ids))
        rows = [{"oeffentliche_testtag_id": i, "bereich": "zeitplan", "programm": "", "ak": "", "pruefsumme": "0" * 64,
                 "stand_am": "2026-10-09T08:00:00Z", "inhalt": {}} for i in (tid, self.fremd_id)]
        with self.assertRaises(db.TransportError):
            self.call(scope, "POST", "veroeffentlichung", [], rows)
        headers = {**db._headers(TEST_SECRET_KEY), "Authorization": "Bearer x"}
        with self.assertRaises(db.TransportError):
            scope("GET", db.table_url(self.config, "testtage", []), headers, None, 5)

    def test_publisher_cli_cannot_touch_other_testtag(self):
        """Selbst wenn der Datenbankablauf fehlerhaft einen fremden Testtag adressierte: nichts wird gesendet."""
        ak5 = make_testtag_workbook(self.dir / "a.xlsx", AK5, scores=SCORES)
        ak9 = make_testtag_workbook(self.dir / "b.xlsx", AK9)
        original = dp.patch_testtag

        def falsch(config, testtag_id, fields, transport, http, action):
            return original(config, self.fremd_id, fields, transport, http, action)  # simulierter Fehler
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dp, "patch_testtag", falsch):
            code = publisher.run(["--workbook-ak5", str(ak5), "--workbook-ak9", str(ak9), "--stability-seconds", "0",
                                  "--publish", "--confirm-slug", TT_SLUG], env=TEST_ENV, transport=self.api,
                                 stdout=out, stderr=err)
        self.assertEqual(code, 6)
        self.assertIn("Schreibschutz", err.getvalue())
        self.assertEqual(self.api.testtage[self.fremd_id]["status"], "vorbereitet")
        self.assertFalse(any(c["method"] == "PATCH" and ("id", f"eq.{self.fremd_id}") in c["params"]
                             for c in self.api.calls))
        self.assertNotIn(TEST_SECRET_KEY, out.getvalue() + err.getvalue())
        self.assertNotIn(TEST_PUBLISHABLE_KEY, out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
