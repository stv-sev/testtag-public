"""P1B-Prüflauf: nur anonyme Kennzahlen, keine Dateien, kein Netzwerk."""
from __future__ import annotations

import hashlib
import io
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import _support
from _support import AK5, AK9, TT_SECRET_MARKERS, make_testtag_workbook

from testtag_public import publication_check

FIXED = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)


class PublicationCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def run_check(self, *paths: Path):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [arg for p in paths for arg in ("--workbook", str(p))] + ["--stability-seconds", "0"]
        code = publication_check.run(argv, sleep=lambda _s: None, now=lambda: FIXED, stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_anonymous(self, text: str, keys=()):
        for marker in [*TT_SECRET_MARKERS, str(self.dir), "2016", "2018", "7.5", *keys]:
            self.assertNotIn(marker, text)

    def test_success_prints_only_anonymous_counts(self):
        ak5 = make_testtag_workbook(self.dir / "ak5.xlsx", AK5, scores={(1, "Boden", 1): (5, 1.5, 7.5)})
        ak9 = make_testtag_workbook(self.dir / "ak9.xlsx", AK9, direct={(1, "Kraft", 1): 3})
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ak5, ak9)}
        code, out, err = self.run_check(ak5, ak9)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Event-Slug:        {_support.TT_SLUG}", out)
        self.assertIn("Programm:          AK5-AK9+", out)
        self.assertIn("Programm:          AK9-AK12", out)
        self.assertIn("Teilnehmer:      4 (AK 6: 1, AK 7: 3)", out)
        self.assertIn("Sichtbar:        Technik 1, Beweglichkeit 0, Kraft 0", out)
        self.assertIn("Sichtbar:        Technik 0, Beweglichkeit 0, Kraft 1", out)
        self.assertIn("Zeitplanzeilen:  2", out)
        self.assertIn("Startliste:      4 Einträge", out)
        self.assertIn("Publikationszeilen: 13", out)
        self.assertIn("Validierung:       OK", out)
        from testtag_public import excel_publication
        state = excel_publication.load_publication_state([ak5, ak9], observed_at=FIXED, wait_seconds=0,
                                                         sleep=lambda _s: None)
        keys = {r["teilnehmer"] for r in state["einzelwertungen"]}
        keys |= {k[:16] for k in keys}
        self.assert_anonymous(out + err, keys)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["ak5.xlsx", "ak9.xlsx"])
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ak5, ak9)})

    def test_validation_error_has_code_and_no_personal_data(self):
        ak5 = make_testtag_workbook(self.dir / "ak5.xlsx", AK5, direct={(2, "Kraft", 1): 4},
                                    scores={(2, "Kraft", 1): (None, 1, 4)})
        code, out, err = self.run_check(ak5)
        self.assertEqual(code, 4)
        self.assertIn("FEHLER (ATHLETIK_BEIDE_QUELLEN)", err)
        self.assertNotIn("Validierung:       OK", out)
        self.assert_anonymous(out + err)

    def test_missing_workbook_is_a_source_error_without_path(self):
        code, _out, err = self.run_check(self.dir / "fehlt.xlsx")
        self.assertEqual(code, 3)
        self.assertNotIn(str(self.dir), err)

    def test_unexpected_network_access_fails_in_tests(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.org", 443))
        with socket.socket() as sock, self.assertRaises(AssertionError):
            sock.connect(("127.0.0.1", 9))


if __name__ == "__main__":
    unittest.main()
