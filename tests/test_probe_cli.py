from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import _support
from _support import (SECRET_MARKERS, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, FakeSupabase,
                      make_workbook)

from testtag_public import probe, supabase_database as db

FIXED = datetime(2026, 10, 11, 8, 30, 0, tzinfo=timezone.utc)
KEYS = (TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.workbook = make_workbook(self.dir / "261009-Test-AK5-AK9plus.xlsx")
        self.output = self.dir / "out" / "probe.json"

    def run_cli(self, *extra, env=None, transport=None, workbook=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = probe.run(
            ["--workbook", str(workbook or self.workbook), "--output", str(self.output), "--stability-seconds", "0",
             *extra],
            env=TEST_ENV if env is None else env, transport=transport, sleep=lambda _s: None, now=lambda: FIXED,
            stdout=stdout, stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_no_keys(self, *texts):
        for text in texts:
            for key in KEYS:
                self.assertNotIn(key, text)

    def test_local_mode_writes_json_without_network(self):
        code, out, err = self.run_cli(env={})
        self.assertEqual(code, 0, err)
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(data["event"]["program"], "AK5-AK9+")
        self.assertIn("OK (nur lokal)", out)
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["probe.json"])

    def test_old_upload_switch_removed(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                self.run_cli("--upload")

    def test_publish_probe_success(self):
        fake = FakeSupabase()
        before = hashlib.sha256(self.workbook.read_bytes()).hexdigest()
        code, out, err = self.run_cli("--publish-probe", transport=fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Schreibschutz bestätigt", out)
        self.assertEqual(out.count("abgelehnt (HTTP 401, 42501)"), 3)
        self.assertEqual(hashlib.sha256(self.workbook.read_bytes()).hexdigest(), before)
        self.assertTrue(self.output.exists())

        (testtag,) = fake.testtage.values()
        self.assertEqual(testtag["slug"], "synth-2026-ak5-ak9plus")
        self.assertIs(testtag["ist_oeffentlich"], True)
        ((pk, row),) = fake.veroeffentlichung.items()
        self.assertEqual(pk, (testtag["id"], "probe", "AK5-AK9+", ""))
        self.assertEqual(row["inhalt"], json.loads(self.output.read_text(encoding="utf-8")))
        self.assertEqual(row["pruefsumme"], db.canonical_sha256(row["inhalt"]))
        self.assertEqual(row["stand_am"], "2026-10-11T08:30:00Z")

        # Rollen: Schreiben nur mit Secret Key, Rücklesen und Schreibtests nur mit Publishable Key, nie Bearer.
        roles = [(c["method"], c["url"].split("/rest/v1/")[1].split("?")[0], c["headers"]["apikey"]) for c in fake.calls]
        self.assertEqual(roles[:3], [("POST", "testtage", TEST_SECRET_KEY),
                                     ("POST", "veroeffentlichung", TEST_SECRET_KEY),
                                     ("GET", "veroeffentlichung", TEST_PUBLISHABLE_KEY)])
        self.assertTrue(all(key == TEST_PUBLISHABLE_KEY for _m, _t, key in roles[3:]))
        for call in fake.calls:
            self.assertNotIn("authorization", {k.lower() for k in call["headers"]})
        self.assert_no_keys(out, err, self.output.read_text(encoding="utf-8"))

    def test_probe_content_has_no_personal_data(self):
        fake = FakeSupabase()
        self.assertEqual(self.run_cli("--publish-probe", transport=fake)[0], 0)
        sent = json.dumps([c["body"] for c in fake.calls], ensure_ascii=False)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, sent, marker)
        self.assertNotIn(str(self.dir), sent)
        self.assertNotIn(str(self.dir).replace("\\", "\\\\"), sent)

    def test_publishable_key_can_write_is_detected(self):
        code, out, err = self.run_cli("--publish-probe", transport=FakeSupabase(secure=False))
        self.assertEqual(code, 9)
        self.assertIn("SICHERHEIT", err)
        self.assertFalse(self.output.exists())

    def test_readback_mismatch_fails(self):
        code, _out, err = self.run_cli("--publish-probe", transport=FakeSupabase(tamper_read=True))
        self.assertEqual(code, 7)
        self.assertIn("inhalt", err)
        self.assertFalse(self.output.exists())

    def test_readback_empty_fails(self):
        code, _out, err = self.run_cli("--publish-probe", transport=FakeSupabase(read_rows=[]))
        self.assertEqual(code, 7)
        self.assertIn("0 statt genau 1", err)

    def test_public_active_false_aborts_before_writing(self):
        workbook = make_workbook(self.dir / "inaktiv.xlsx", public={"public_active": False})
        fake = FakeSupabase()
        code, _out, err = self.run_cli("--publish-probe", transport=fake, workbook=workbook)
        self.assertEqual(code, 4)
        self.assertIn("public_active", err)
        self.assertEqual(fake.calls, [])

    def test_write_error_keeps_existing_valid_output_and_hides_keys(self):
        self.assertEqual(self.run_cli(env={})[0], 0)
        before = self.output.read_bytes()
        code, out, err = self.run_cli("--publish-probe", transport=FakeSupabase(write_status=500))
        self.assertEqual(code, 6)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["probe.json"])
        self.assert_no_keys(out, err)
        self.assertIn("<redacted>", err)

    def test_missing_config_reported_before_reading(self):
        with mock.patch.object(probe.excel_probe, "probe_workbook", side_effect=AssertionError("nicht lesen")):
            code, _out, err = self.run_cli("--publish-probe", env={"SUPABASE_URL": "https://x.supabase.co"})
        self.assertEqual(code, 5)
        self.assertIn("SUPABASE_SECRET_KEY", err)
        self.assertIn("SUPABASE_PUBLISHABLE_KEY", err)
        self.assertFalse(self.output.exists())

    def test_wrong_key_types_rejected_without_leak(self):
        swapped = dict(TEST_ENV, SUPABASE_SECRET_KEY=TEST_PUBLISHABLE_KEY, SUPABASE_PUBLISHABLE_KEY=TEST_SECRET_KEY)
        code, out, err = self.run_cli("--publish-probe", env=swapped, transport=FakeSupabase())
        self.assertEqual(code, 5)
        self.assert_no_keys(out, err)

    def test_invalid_workbook_does_not_overwrite_output(self):
        self.assertEqual(self.run_cli(env={})[0], 0)
        before = self.output.read_bytes()
        self.workbook.write_bytes(b"kaputt")
        code, _out, _err = self.run_cli(env={})
        self.assertEqual(code, 3)
        self.assertEqual(self.output.read_bytes(), before)

    def test_unexpected_error_hides_details(self):
        with mock.patch.object(probe.excel_probe, "probe_workbook", side_effect=RuntimeError(TEST_SECRET_KEY)):
            code, out, err = self.run_cli(env={})
        self.assertEqual(code, 1)
        self.assert_no_keys(out, err)

    def test_output_must_not_be_excel(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = probe.run(["--workbook", str(self.workbook), "--output", str(self.dir / "x.xlsx")],
                         env={}, stdout=stdout, stderr=stderr)
        self.assertEqual(code, 8)


if __name__ == "__main__":
    unittest.main()
