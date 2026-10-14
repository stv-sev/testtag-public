"""P1C-Kommandozeile: Probelauf ohne Netzwerk, Bestätigung, Konfiguration, sichere Ausgabe."""
from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import _support
from _support import (AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SECRET_MARKERS, TT_SLUG,
                      FakeDataApi, make_testtag_workbook, testtag_spec)

from testtag_public import publisher
from testtag_public.excel_publication import load_publication_state

FIXED = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
SCORES = {(1, "Boden", 1): (5, 1.5, 7.5), (1, "Kraft", 1): (None, 1, 8)}


def no_transport(*_args, **_kwargs):
    raise AssertionError("Netzwerk/Transport darf im Probelauf nicht verwendet werden")


class PublisherCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.ak5 = make_testtag_workbook(self.dir / "ak5.xlsx", AK5, scores=SCORES)
        self.ak9 = make_testtag_workbook(self.dir / "ak9.xlsx", AK9, direct={(3, "Kraft", 2): 4})

    def run_cli(self, *extra, env=None, transport=no_transport, ak5=None, ak9=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--workbook-ak5", str(ak5 or self.ak5), "--workbook-ak9", str(ak9 or self.ak9),
                "--stability-seconds", "0", *extra]
        code = publisher.run(argv, env={} if env is None else env, transport=transport, sleep=lambda _s: None,
                             now=lambda: FIXED, stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def sensitive(self) -> list[str]:
        state = load_publication_state([self.ak5, self.ak9], observed_at=FIXED, wait_seconds=0, sleep=lambda _s: None)
        keys = {r["teilnehmer"] for r in state["einzelwertungen"]}
        keys |= {p["teilnehmer"] for groups in state["startlisten"].values() for g in groups for p in g["teilnehmer"]}
        return [*TT_SECRET_MARKERS, *keys, *(k[:16] for k in keys), str(self.dir), TEST_SECRET_KEY,
                TEST_PUBLISHABLE_KEY, "7.5", "Bearer"]

    def assert_safe(self, text: str) -> None:
        for marker in self.sensitive():
            self.assertNotIn(marker, text)

    def subdivisionen(self) -> list[Path]:
        people = testtag_spec(AK5)["participants"]
        return [make_testtag_workbook(self.dir / "ak5-a.xlsx", AK5, public={"subdivision": "AK5–AK6"},
                                      participants=[x for x in people if x[5] == "AK 6"]),
                make_testtag_workbook(self.dir / "ak5-b.xlsx", AK5, public={"subdivision": "AK7"},
                                      participants=[x for x in people if x[5] == "AK 7"]),
                self.ak9]

    def run_argv(self, argv, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = publisher.run([*argv, "--stability-seconds", "0"], env={} if env is None else env,
                             transport=no_transport, sleep=lambda _s: None, now=lambda: FIXED, stdout=stdout,
                             stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_any_number_of_workbooks_for_subdivisions(self):
        mappen = self.subdivisionen()
        for quelle, argv, env in (
                ("--workbook", [x for m in mappen for x in ("--workbook", str(m))], {}),
                ("TESTTAG_WORKBOOKS", [], {"TESTTAG_WORKBOOKS": ";".join(str(m) for m in mappen)})):
            with self.subTest(quelle=quelle):
                code, out, err = self.run_argv(argv, env)
                self.assertEqual(code, 0, err)
                self.assertIn("Programm:            AK5–AK6 (Freigabe ja, Rotation 8/8)", out)
                self.assertIn("Programm:            AK7 (Freigabe ja, Rotation 8/8)", out)
                self.assertIn("Programm:            AK9-AK12", out)
                self.assertIn("Status:              Probelauf – nichts geschrieben", out)
        code, _out, err = self.run_argv(["--workbook", str(mappen[0]), "--workbook-ak9", str(self.ak9)])
        self.assertEqual(code, 2)
        self.assertIn("--workbook nicht zusammen mit", err)
        env = {"TESTTAG_WORKBOOKS": str(mappen[0]), "TESTTAG_WORKBOOK_AK9_AK12": str(self.ak9)}
        code, _out, err = self.run_argv([], env)
        self.assertEqual(code, 5)
        self.assertIn("nicht gemeinsam", err)
        self.assert_safe(err)

    def test_default_is_a_dry_run_without_network(self):
        with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Netzwerk")):
            code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertIn(f"Event-Slug:          {TT_SLUG}", out)
        self.assertIn("Programm:            AK5-AK9+ (Freigabe ja, Rotation 8/8)", out)
        self.assertIn("Sichtbar:          Technik 1, Beweglichkeit 0, Kraft 1", out)
        self.assertIn("Ranglistengruppen: 2", out)
        self.assertIn("Publikationszeilen:  13", out)
        self.assertIn("Status:              Probelauf – nichts geschrieben", out)
        self.assertRegex(out, r"zeitplan\s+-\s+-\s+[0-9a-f]{12}\n")
        self.assertNotRegex(out, r"[0-9a-f]{13,}")  # nur gekürzte Prüfsummen
        self.assert_safe(out + err)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["ak5.xlsx", "ak9.xlsx"])

    def test_dry_run_does_not_load_database_flow(self):
        with mock.patch("testtag_public.database_publisher.publish", side_effect=AssertionError("publish")):
            code, _out, err = self.run_cli()
        self.assertEqual(code, 0, err)

    def test_publish_requires_exact_slug_confirmation(self):
        for extra in (("--publish",), ("--publish", "--confirm-slug", "falscher-slug"),
                      ("--publish", "--confirm-slug", TT_SLUG.upper()), ("--confirm-slug", TT_SLUG)):
            with self.subTest(extra=extra):
                code, out, err = self.run_cli(*extra, env=TEST_ENV)
                self.assertEqual(code, 2)
                self.assertNotIn("publiziert", out)
                self.assert_safe(out + err)

    def test_missing_environment_is_reported_before_reading(self):
        with mock.patch("testtag_public.publisher.read_program_source", side_effect=AssertionError("nicht lesen")):
            code, _out, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG,
                                           env={"SUPABASE_URL": "https://x.supabase.co"})
        self.assertEqual(code, 5)
        self.assertIn("SUPABASE_SECRET_KEY", err)
        self.assertIn("SUPABASE_PUBLISHABLE_KEY", err)

    def test_error_in_one_program_blocks_only_that_program(self):
        bad = make_testtag_workbook(self.dir / "bad.xlsx", AK5, scores=SCORES, direct={(1, "Kraft", 1): 8})
        code, out, err = self.run_cli(ak5=bad)  # Probelauf: Signal mit Exit 4, Ort der Meldung
        self.assertEqual(code, 4)
        self.assertIn("Blockiert:           AK5-AK9+ (ATHLETIK_BEIDE_QUELLEN) – alter öffentlicher Stand bleibt", err)
        self.assertIn("Noteneingabe!", err)
        self.assertIn("Programm:            AK9-AK12", out)
        self.assertIn("ein Programm ist blockiert", out)
        api = FakeDataApi()
        code, out, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG, env=TEST_ENV, transport=api, ak5=bad)
        self.assertEqual(code, 0, err)
        self.assertIn("(teilweise – blockiert: AK5-AK9+)", out)
        self.assertIn("ATHLETIK_BEIDE_QUELLEN", err)
        self.assertTrue(any(c["table"] == "veroeffentlichung" and c["method"] == "POST" for c in api.calls))
        self.assert_safe(out + err)

    def test_wrong_program_in_option_is_rejected(self):
        code, _out, err = self.run_cli(ak5=self.ak9, ak9=self.ak5)
        self.assertEqual(code, 4)
        self.assertIn("PROGRAMM_ZUORDNUNG", err)

    def test_publish_success_and_idempotent_repeat(self):
        api = FakeDataApi()
        code, out, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG, env=TEST_ENV, transport=api)
        self.assertEqual(code, 0, err)
        self.assertIn("Status:              publiziert und öffentlich geprüft", out)
        self.assertIn("Zeilen:              13 verwaltet, 13 geschrieben (davon 0 Tombstones), 0 unverändert", out)
        self.assertIn("HTTP 201  Batch-Upsert", out)
        self.assertIn("Testtag öffentlich:  ja (neu angelegt)", out)
        self.assert_safe(out + err)
        for call in api.calls:
            self.assertNotIn("authorization", {k.lower() for k in call["headers"]})
        code, out, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG, env=TEST_ENV, transport=api)
        self.assertEqual(code, 0, err)
        self.assertIn("Status:              unverändert – nichts geschrieben", out)
        self.assertIn("Schreibanfragen:     0", out)

    def test_database_failure_output_is_sanitized(self):
        api = FakeDataApi(fail_batch=500)
        code, out, err = self.run_cli("--publish", "--confirm-slug", TT_SLUG, env=TEST_ENV, transport=api)
        self.assertEqual(code, 6)
        self.assertIn("[DB_SCHREIBEN]", err)
        self.assertIn("NICHT vollständig publiziert", err)
        self.assertNotIn("publiziert und öffentlich geprüft", out)
        self.assert_safe(out + err)

    def test_unexpected_exception_reveals_only_the_type(self):
        boom = RuntimeError(f"Synthaler TV Testdorf {TEST_SECRET_KEY} {self.dir}")
        with mock.patch("testtag_public.publisher.build_state", side_effect=boom):
            code, out, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", err)
        self.assert_safe(out + err)

    def test_safe_report_filters_personal_data_keys_and_paths(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        report = publisher.SafeReport(stdout, stderr, (TEST_SECRET_KEY,))
        state = load_publication_state([self.ak5, self.ak9], observed_at=FIXED, wait_seconds=0, sleep=lambda _s: None)
        report.protect_state(state)
        key = state["einzelwertungen"][0]["teilnehmer"]
        report.out(f"Synthaler Lea {key} {key[:16]} {TEST_SECRET_KEY} C:\\Users\\x\\a.xlsx /c/Users/x/b.xlsx")
        text = stdout.getvalue()
        self.assert_safe(text)
        self.assertNotIn("C:\\Users", text)
        self.assertIn("<entfernt>", text)


if __name__ == "__main__":
    unittest.main()
