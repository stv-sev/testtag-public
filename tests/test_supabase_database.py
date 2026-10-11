from __future__ import annotations

import base64
import hashlib
import json
import unittest
import urllib.parse
from datetime import datetime, timezone

import _support
from _support import TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, FakeSupabase, jsonb_reorder

from testtag_public import supabase_database as db

STAND = datetime(2026, 10, 11, 8, 30, 0, tzinfo=timezone.utc)
TESTTAG_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
INHALT = {"schema_version": "probe-1", "event": {"event_id": "e", "program": "AK5-AK9+", "public_active": True,
                                                 "published_rotation_max": 7},
          "noteneingabe": {"formula_cells": 4, "cached_empty_sample_addresses": ["E3", "F9"]}}


def legacy_jwt(role: str) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{part({'alg': 'HS256'})}.{part({'role': role})}.c2ln"


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, value = response
        return status, value if isinstance(value, bytes) else json.dumps(value).encode()


def query(url: str) -> list[tuple[str, str]]:
    return urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)


class ConfigTests(unittest.TestCase):
    def test_missing_configuration_names_all_variables(self):
        with self.assertRaises(db.DatabaseConfigError) as ctx:
            db.load_config({})
        for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_PUBLISHABLE_KEY"):
            self.assertIn(name, str(ctx.exception))
        self.assertNotIn("BUCKET", str(ctx.exception))
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_valid_configuration_hides_keys(self):
        config = db.load_config(TEST_ENV)
        for text in (repr(config), str(config)):
            self.assertNotIn(TEST_SECRET_KEY, text)
            self.assertNotIn(TEST_PUBLISHABLE_KEY, text)

    def test_secret_key_must_start_with_sb_secret(self):
        for bad in (TEST_PUBLISHABLE_KEY, legacy_jwt("service_role"), "geheim123", "sb_secret_"):
            with self.subTest(bad=bad[:14]):
                with self.assertRaises(db.DatabaseConfigError) as ctx:
                    db.load_config(dict(TEST_ENV, SUPABASE_SECRET_KEY=bad))
                self.assertIn("sb_secret_", str(ctx.exception))
                self.assertNotIn(bad, str(ctx.exception).replace("sb_secret_)", ""))

    def test_publishable_key_must_start_with_sb_publishable(self):
        for bad in (TEST_SECRET_KEY, legacy_jwt("anon"), "oeffentlich123"):
            with self.subTest(bad=bad[:14]):
                with self.assertRaises(db.DatabaseConfigError) as ctx:
                    db.load_config(dict(TEST_ENV, SUPABASE_PUBLISHABLE_KEY=bad))
                self.assertIn("sb_publishable_", str(ctx.exception))
                self.assertNotIn(bad, str(ctx.exception))

    def test_url_must_be_https_without_credentials(self):
        for bad in ("http://example-ref.supabase.co", "https://user:pw@example-ref.supabase.co",
                    "https://example-ref.supabase.co/rest/v1"):
            with self.subTest(bad=bad):
                with self.assertRaises(db.DatabaseConfigError):
                    db.load_config(dict(TEST_ENV, SUPABASE_URL=bad))


class CanonicalJsonTests(unittest.TestCase):
    def test_canonical_sha256_is_stable_and_order_independent(self):
        expected = hashlib.sha256(json.dumps(INHALT, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(db.canonical_sha256(INHALT), expected)
        self.assertEqual(db.canonical_sha256(jsonb_reorder(INHALT)), expected)
        self.assertEqual(db.canonical_sha256(json.loads(json.dumps(INHALT))), expected)

    def test_canonical_json_form(self):
        self.assertEqual(db.canonical_json({"b": 1, "a": "ä"}), '{"a":"ä","b":1}'.encode("utf-8"))
        with self.assertRaises(ValueError):
            db.canonical_json({"x": float("nan")})

    def test_semantic_comparison(self):
        self.assertTrue(db.json_equal(INHALT, jsonb_reorder(INHALT)))
        self.assertFalse(db.json_equal({"a": True}, {"a": 1}))
        self.assertFalse(db.json_equal({"a": 1}, {"a": 1.0}))
        self.assertFalse(db.json_equal({"a": [1, 2]}, {"a": [2, 1]}))
        self.assertFalse(db.json_equal({"a": 1}, {"a": 1, "b": None}))


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.config = db.load_config(TEST_ENV)

    def assert_only_apikey(self, headers, key):
        self.assertEqual(headers["apikey"], key)
        self.assertNotIn("authorization", {k.lower() for k in headers})
        for value in headers.values():
            self.assertNotIn("Bearer", value)

    def test_upsert_testtag(self):
        transport = Recorder((201, [{"id": TESTTAG_ID.upper()}]))
        result = db.upsert_testtag(self.config, slug="aargau-2026-ak5-ak9plus", titel="Titel", ist_oeffentlich=True,
                                   stand_am=STAND, transport=transport)
        self.assertEqual(result, TESTTAG_ID)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://example-ref.supabase.co/rest/v1/testtage?on_conflict=slug&select=id")
        self.assert_only_apikey(call["headers"], TEST_SECRET_KEY)
        self.assertIn("resolution=merge-duplicates", call["headers"]["Prefer"])
        body = json.loads(call["body"])
        self.assertEqual(body, {"slug": "aargau-2026-ak5-ak9plus", "titel": "Titel", "ist_oeffentlich": True,
                                "stand_am": "2026-10-11T08:30:00Z"})
        self.assertNotIn("status", body)

    def test_upsert_testtag_requires_single_uuid(self):
        for response in ((201, []), (201, [{"id": "kein-uuid"}]), (201, [{"id": TESTTAG_ID}, {"id": TESTTAG_ID}])):
            with self.subTest(response=response):
                with self.assertRaises(db.DatabaseWriteError):
                    db.upsert_testtag(self.config, slug="e", titel="T", ist_oeffentlich=True, stand_am=STAND,
                                      transport=Recorder(response))

    def test_upsert_probe_uses_exact_conflict_columns(self):
        transport = Recorder((201, b""))
        row = db.probe_row(TESTTAG_ID, "AK5-AK9+", INHALT, STAND)
        db.upsert_probe(self.config, row, transport)
        call = transport.calls[0]
        self.assertEqual(query(call["url"]), [("on_conflict", "oeffentliche_testtag_id,bereich,programm,ak")])
        self.assertIn("on_conflict=oeffentliche_testtag_id,bereich,programm,ak", call["url"])
        self.assert_only_apikey(call["headers"], TEST_SECRET_KEY)
        self.assertIn("resolution=merge-duplicates", call["headers"]["Prefer"])
        body = json.loads(call["body"])
        self.assertEqual(body["bereich"], "probe")
        self.assertEqual(body["programm"], "AK5-AK9+")
        self.assertEqual(body["ak"], "")
        self.assertEqual(body["pruefsumme"], db.canonical_sha256(INHALT))
        self.assertEqual(body["stand_am"], "2026-10-11T08:30:00Z")
        self.assertEqual(body["inhalt"], INHALT)

    def test_read_uses_publishable_key_and_full_primary_key(self):
        transport = Recorder((200, [{"x": 1}]))
        db.read_probe_public(self.config, TESTTAG_ID, "AK5-AK9+", transport)
        call = transport.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assert_only_apikey(call["headers"], TEST_PUBLISHABLE_KEY)
        self.assertIn("programm=eq.AK5-AK9%2B", call["url"])
        self.assertTrue(call["url"].endswith("&ak=eq."))
        self.assertEqual(dict(query(call["url"])), {
            "select": "oeffentliche_testtag_id,bereich,programm,ak,pruefsumme,stand_am,inhalt",
            "oeffentliche_testtag_id": f"eq.{TESTTAG_ID}", "bereich": "eq.probe",
            "programm": "eq.AK5-AK9+", "ak": "eq.",
        })

    def test_parameters_are_encoded(self):
        programm = "A&B=C ?#é+/"
        transport = Recorder((200, [{}]))
        db.read_probe_public(self.config, TESTTAG_ID, programm, transport)
        url = transport.calls[0]["url"]
        self.assertEqual(dict(query(url))["programm"], f"eq.{programm}")
        raw_query = urllib.parse.urlsplit(url).query
        for char in "&=?# +/é":
            self.assertNotIn(char, raw_query.split("programm=")[1].split("&")[0])
        self.assertEqual(db.table_url(self.config, "a b", []), "https://example-ref.supabase.co/rest/v1/a%20b")

    def test_read_requires_exactly_one_row(self):
        for rows in ([], [{}, {}], {"x": 1}):
            with self.subTest(rows=rows):
                with self.assertRaises(db.DatabaseReadError):
                    db.read_probe_public(self.config, TESTTAG_ID, "P", Recorder((200, rows)))

    def test_read_rejects_invalid_uuid(self):
        with self.assertRaises(Exception):
            db.read_probe_public(self.config, "1 or 1=1", "P", Recorder((200, [{}])))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.expected = db.probe_row(TESTTAG_ID, "AK5-AK9+", INHALT, STAND)

    def returned(self, **changes):
        row = dict(self.expected, inhalt=jsonb_reorder(INHALT), stand_am="2026-10-11T08:30:00+00:00")
        row.update(changes)
        return row

    def test_jsonb_roundtrip_accepted(self):
        self.assertEqual(db.verify_probe(self.returned(), self.expected), db.canonical_sha256(INHALT))
        as_text = self.returned(inhalt=json.dumps(jsonb_reorder(INHALT)))
        self.assertEqual(db.verify_probe(as_text, self.expected), db.canonical_sha256(INHALT))

    def test_mismatches_rejected(self):
        changed = json.loads(json.dumps(INHALT))
        changed["noteneingabe"]["formula_cells"] = 5
        cases = {
            "inhalt": self.returned(inhalt=changed),
            "pruefsumme": self.returned(pruefsumme="0" * 64),
            "stand_am": self.returned(stand_am="2026-10-11T08:31:00+00:00"),
            "programm": self.returned(programm="AK9-AK12"),
            "oeffentliche_testtag_id": self.returned(oeffentliche_testtag_id="11111111-1111-1111-1111-111111111111"),
        }
        for name, row in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(db.DatabaseReadError) as ctx:
                    db.verify_probe(row, self.expected)
                self.assertIn(name, str(ctx.exception))


class WriteRejectionTests(unittest.TestCase):
    def setUp(self):
        self.config = db.load_config(TEST_ENV)
        self.denied = (401, {"code": "42501", "message": "permission denied for table veroeffentlichung"})

    def test_rejection_confirmed(self):
        transport = Recorder(self.denied, self.denied, self.denied)
        lines = db.assert_publishable_write_rejected(self.config, TESTTAG_ID, "AK5-AK9+", "a" * 64, transport)
        self.assertEqual(len(lines), 3)
        for call in transport.calls:
            self.assertEqual(call["headers"]["apikey"], TEST_PUBLISHABLE_KEY)
            self.assertNotIn("Authorization", call["headers"])
        methods = [c["method"] for c in transport.calls]
        self.assertEqual(methods, ["POST", "POST", "PATCH"])
        insert = json.loads(transport.calls[0]["body"])
        self.assertNotEqual(insert["oeffentliche_testtag_id"], TESTTAG_ID)   # fremde UUID: FK würde scheitern
        self.assertIn(" ", json.loads(transport.calls[1]["body"])["slug"])   # Slug verletzt CHECK-Regel
        patch = transport.calls[2]
        self.assertEqual(dict(query(patch["url"])), {"oeffentliche_testtag_id": f"eq.{TESTTAG_ID}",
                                                     "bereich": "eq.probe", "programm": "eq.AK5-AK9+", "ak": "eq."})
        self.assertEqual(json.loads(patch["body"]), {"pruefsumme": "a" * 64})

    def test_accepted_write_is_security_failure(self):
        for status in (200, 201, 204):
            with self.subTest(status=status):
                with self.assertRaises(db.SecurityCheckError) as ctx:
                    db.assert_publishable_write_rejected(self.config, TESTTAG_ID, "P", "a" * 64,
                                                         Recorder((status, b"")))
                self.assertIn("SICHERHEIT", str(ctx.exception))
                self.assertEqual(ctx.exception.exit_code, 9)

    def test_ambiguous_rejection_is_not_accepted(self):
        for response in ((409, {"code": "23503"}), (401, {"message": "Invalid API key"}), (500, b"x"),
                         (403, {"code": "PGRST301"})):
            with self.subTest(response=response):
                with self.assertRaises(db.SecurityCheckError) as ctx:
                    db.assert_publishable_write_rejected(self.config, TESTTAG_ID, "P", "a" * 64, Recorder(response))
                self.assertIn("nicht eindeutig", str(ctx.exception))


class SecretLeakTests(unittest.TestCase):
    def setUp(self):
        self.config = db.load_config(TEST_ENV)

    def test_server_echo_of_keys_redacted(self):
        echo = (500, f"{TEST_SECRET_KEY} {TEST_PUBLISHABLE_KEY}".encode())
        calls = [
            lambda t: db.upsert_testtag(self.config, slug="e", titel="T", ist_oeffentlich=True, stand_am=STAND,
                                        transport=t),
            lambda t: db.upsert_probe(self.config, db.probe_row(TESTTAG_ID, "P", INHALT, STAND), t),
            lambda t: db.read_probe_public(self.config, TESTTAG_ID, "P", t),
        ]
        for call in calls:
            with self.assertRaises(db.ProbeError) as ctx:
                call(Recorder(echo))
            self.assertNotIn(TEST_SECRET_KEY, str(ctx.exception))
            self.assertNotIn(TEST_PUBLISHABLE_KEY, str(ctx.exception))

    def test_connection_error_redacted(self):
        error = db.TransportError(f"URLError: {TEST_SECRET_KEY}")
        with self.assertRaises(db.DatabaseWriteError) as ctx:
            db.upsert_probe(self.config, db.probe_row(TESTTAG_ID, "P", INHALT, STAND), Recorder(error))
        self.assertNotIn(TEST_SECRET_KEY, str(ctx.exception))

    def test_unexpected_transport_exception_hides_details(self):
        with self.assertRaises(db.DatabaseReadError) as ctx:
            db.read_probe_public(self.config, TESTTAG_ID, "P", Recorder(RuntimeError(TEST_PUBLISHABLE_KEY)))
        self.assertNotIn(TEST_PUBLISHABLE_KEY, str(ctx.exception))

    def test_redact_patterns(self):
        text = db.redact("a sb_secret_xyz b sb_publishable_abc c")
        self.assertEqual(text, "a <redacted> b <redacted> c")


class NetworkGuardTests(unittest.TestCase):
    def test_default_transport_cannot_reach_network_in_tests(self):
        config = db.load_config(TEST_ENV)
        with self.assertRaises(db.ProbeError):
            db.read_probe_public(config, TESTTAG_ID, "P")   # ohne Transport -> urllib -> gesperrt

    def test_fake_supabase_end_to_end(self):
        config = db.load_config(TEST_ENV)
        fake = FakeSupabase()
        testtag_id = db.upsert_testtag(config, slug="e", titel="T", ist_oeffentlich=True, stand_am=STAND, transport=fake)
        row = db.probe_row(testtag_id, "AK5-AK9+", INHALT, STAND)
        db.upsert_probe(config, row, fake)
        db.verify_probe(db.read_probe_public(config, testtag_id, "AK5-AK9+", fake), row)
        db.assert_publishable_write_rejected(config, testtag_id, "AK5-AK9+", row["pruefsumme"], fake)
        for call in fake.calls:
            self.assertNotIn("Authorization", call["headers"])


if __name__ == "__main__":
    unittest.main()
