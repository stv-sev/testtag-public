"""quelle.py: öffentlicher Lesezugriff, Auflösung, Prüfung, Tombstones und Auswahlhilfen (kein Netzwerk)."""
from __future__ import annotations

import socket
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import _support
from _support import (AK5, AK9, ARCHIV_ID, LIVE_ID, PRIVAT_ID, PUB_SECRET_MARKERS, PUB_T1, PUB_T2,
                      TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SLUG, FakeOeffentlich, synthetic_publication)

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import quelle as q  # noqa: E402

SECRETS = {"supabase_url": "https://example-ref.supabase.co", "supabase_publishable_key": TEST_PUBLISHABLE_KEY}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data = synthetic_publication(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.api = FakeOeffentlich(self.data["testtage"], self.data["rows"], self.data["snapshots"])
        self.zugang = q.lade_zugang(SECRETS)

    def stand(self, kennung=TT_SLUG):
        testtag = q.finde_testtag(self.zugang, kennung, transport=self.api)
        return q.lade_stand(self.zugang, testtag, transport=self.api)

    def assert_neutral(self, text: str):
        for marker in PUB_SECRET_MARKERS:
            self.assertNotIn(marker, text)
        self.assertNotIn("example-ref", text)


class ZugangTests(Base):
    def test_valid_access_and_timeout(self):
        zugang = q.lade_zugang({**SECRETS, "zeitueberschreitung": 2.5})
        self.assertEqual((zugang.url, zugang.zeitueberschreitung), ("https://example-ref.supabase.co", 2.5))
        self.assertNotIn(TEST_PUBLISHABLE_KEY, repr(zugang) + str(zugang))
        q.lade_testtage(zugang, transport=self.api)
        self.assertEqual(self.api.calls[-1]["zeit"], 2.5)
        self.assertEqual(q.lade_zugang({**SECRETS, "zeitueberschreitung": 999}).zeitueberschreitung,
                         q.ZEITUEBERSCHREITUNG)

    def test_secret_key_names_and_values_are_rejected(self):
        for extra in ({"supabase_secret_key": TEST_SECRET_KEY}, {"SUPABASE_SERVICE_ROLE_KEY": "x"},
                      {"service_role": "x"}):
            with self.subTest(extra=list(extra)):
                with self.assertRaises(q.QuellenFehler) as ctx:
                    q.lade_zugang({**SECRETS, **extra})
                self.assertEqual(ctx.exception.code, "SCHREIBSCHLUESSEL")
        with self.assertRaises(q.QuellenFehler) as ctx:
            q.lade_zugang({**SECRETS, "supabase_publishable_key": TEST_SECRET_KEY})
        self.assertNotIn(TEST_SECRET_KEY, str(ctx.exception))
        self.assertNotIn("sb_secret", str(ctx.exception))

    def test_https_and_missing_configuration(self):
        for url in ("http://example-ref.supabase.co", "https://example-ref.supabase.co/rest", "https://u:p@x.co"):
            with self.assertRaises(q.QuellenFehler):
                q.lade_zugang({**SECRETS, "supabase_url": url})
        with self.assertRaises(q.NichtEingerichtet):
            q.lade_zugang({})
        with self.assertRaises(q.NichtEingerichtet):
            q.lade_zugang({"supabase_url": "https://x.supabase.co"})

    def test_only_apikey_header_no_authorization_or_bearer(self):
        self.stand()
        q.lade_testtage(self.zugang, transport=self.api)
        for call in self.api.calls:
            self.assertEqual(call["kopf"], {"apikey": TEST_PUBLISHABLE_KEY, "Accept": "application/json"})
            self.assertNotIn(TEST_PUBLISHABLE_KEY, call["url"])
            self.assertTrue(call["url"].startswith("https://example-ref.supabase.co/rest/v1/"))


class TesttagTests(Base):
    def test_list_contains_only_public_sorted_live_prepared_archive(self):
        testtage = q.lade_testtage(self.zugang, transport=self.api)
        self.assertEqual([t.status for t in testtage], ["live", "vorbereitet", "archiviert"])
        self.assertNotIn(PRIVAT_ID, [t.id for t in testtage])

    def test_sorting_within_status_newest_first(self):
        a = q.Testtag("a" * 8 + "-0000-4000-8000-" + "0" * 12, "alt", "Alt", "archiviert", "2024-01-01T00:00:00+00:00")
        b = q.Testtag("b" * 8 + "-0000-4000-8000-" + "0" * 12, "neu", "Neu", "archiviert", "2025-01-01T00:00:00+00:00")
        c = q.Testtag("c" * 8 + "-0000-4000-8000-" + "0" * 12, "live", "Live", "live", "2020-01-01T00:00:00+00:00")
        self.assertEqual([t.slug for t in q.sortiere_testtage([a, b, c])], ["live", "neu", "alt"])

    def test_slug_resolves_to_uuid_and_uuid_stays_compatible(self):
        by_slug = q.finde_testtag(self.zugang, TT_SLUG, transport=self.api)
        self.assertEqual(by_slug.id, LIVE_ID)
        by_uuid = q.finde_testtag(self.zugang, LIVE_ID.upper(), transport=self.api)
        self.assertEqual(by_uuid, by_slug)
        self.assertIn(("id", f"eq.{LIVE_ID}"), urllib.parse.parse_qsl(urllib.parse.urlsplit(self.api.calls[-1]["url"]).query))

    def test_unknown_or_private_slug_returns_none(self):
        self.assertIsNone(q.finde_testtag(self.zugang, "gibt-es-nicht", transport=self.api))
        self.assertIsNone(q.finde_testtag(self.zugang, "synth-geheim", transport=self.api))
        self.assertIsNone(q.finde_testtag(self.zugang, PRIVAT_ID, transport=self.api))

    def test_invalid_identifiers_never_reach_the_api(self):
        for kennung in ("", "a b", "x&select=*", "-start", "ä", "a" * 101, "eq.1,or(id.neq.0)"):
            with self.subTest(kennung=kennung), self.assertRaises(q.QuellenFehler) as ctx:
                q.finde_testtag(self.zugang, kennung, transport=self.api)
            self.assertEqual(ctx.exception.code, "KENNUNG")
        self.assertEqual(self.api.calls, [])

    def test_url_encoding_of_parameters(self):
        q.finde_testtag(self.zugang, "a.b_c-d", transport=self.api)
        url = self.api.calls[-1]["url"]
        self.assertIn("slug=eq.a.b_c-d", url)
        self.assertIn("select=id,slug,titel,status,stand_am", url)
        self.assertEqual(q.oeffentliche_url("https://app.example.ch/", TT_SLUG), f"https://app.example.ch/?t={TT_SLUG}")


class StandTests(Base):
    def test_tombstones_probe_and_unknown_areas_are_filtered(self):
        stand = self.stand()
        bereiche = {(d.bereich, d.programm, d.ak) for d in stand.saetze}
        self.assertNotIn(("resultate_live", AK5, "AK 5"), bereiche)  # Tombstone
        self.assertFalse(any(d.bereich == "probe" for d in stand.saetze))
        self.assertEqual(q.altersklassen(stand, q.LIVE, AK5), ["AK 6", "AK 7"])
        self.assertEqual(q.programme(stand, q.RANGLISTE), [AK5, AK9])
        self.assertNotIn("PROBEINHALT", repr(stand))

    def test_unknown_schema_version_is_rejected(self):
        def alt(rows):
            rows[0]["inhalt"]["schema_version"] = "publikation-0"
            return rows
        self.api.manipulieren = alt
        with self.assertRaises(q.QuellenFehler) as ctx:
            self.stand()
        self.assertEqual(ctx.exception.code, "SCHEMA")

    def test_wrong_checksum_is_rejected(self):
        def falsch(rows):
            for row in rows:
                if row["bereich"] == "resultate_rangliste":
                    row["inhalt"]["gruppen"][0]["eintraege"][0]["punkte"] = 99
                    break
            return rows
        self.api.manipulieren = falsch
        with self.assertRaises(q.QuellenFehler) as ctx:
            self.stand()
        self.assertEqual(ctx.exception.code, "PRUEFSUMME")

    def test_invalid_json_and_duplicate_keys_are_rejected(self):
        self.api.manipulieren = lambda rows: [dict(rows[0], inhalt="{kaputt"), *rows[1:]]
        with self.assertRaises(q.QuellenFehler) as ctx:
            self.stand()
        self.assertEqual(ctx.exception.code, "JSON")
        self.api.manipulieren = lambda rows: rows + [rows[0]]
        with self.assertRaises(q.QuellenFehler) as ctx:
            self.stand()
        self.assertEqual(ctx.exception.code, "DOPPELT")

    def test_checksum_uses_the_shared_p1b_function(self):
        from testtag_public import publication_model as pm
        self.assertIs(q.content_checksum, pm.content_checksum)
        self.assertIs(q.sort_live, pm.sort_live)

    def test_network_and_http_errors_contain_no_payload(self):
        testtag = q.finde_testtag(self.zugang, TT_SLUG, transport=self.api)
        self.api.status = 503
        with self.assertRaises(q.QuellenFehler) as ctx:
            q.lade_stand(self.zugang, testtag, transport=self.api)
        self.assertEqual(ctx.exception.code, "HTTP_503")
        self.assert_neutral(str(ctx.exception))
        self.api.status = None
        self.api.ausnahme = RuntimeError(f"Synthaler {TEST_PUBLISHABLE_KEY} https://example-ref.supabase.co")
        with self.assertRaises(q.QuellenFehler) as ctx:
            q.lade_stand(self.zugang, testtag, transport=self.api)
        self.assert_neutral(str(ctx.exception))

    def test_default_transport_respects_timeout_and_is_neutral(self):
        captured = {}

        def urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            raise socket.timeout("zu langsam")

        with mock.patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(q.QuellenFehler) as ctx:
                q.lade_testtage(q.lade_zugang({**SECRETS, "zeitueberschreitung": 1.5}))
        self.assertEqual(ctx.exception.code, "ZEITUEBERSCHREITUNG")
        self.assertEqual(captured["timeout"], 1.5)
        self.assertNotIn("Authorization", captured["headers"])
        with self.assertRaises(q.QuellenFehler):
            q.https_transport("http://example.org", {}, 1)

    def test_tests_cannot_reach_the_network(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("example-ref.supabase.co", 443))


class ArchivTests(Base):
    def archiv(self):
        testtag = q.finde_testtag(self.zugang, ARCHIV_ID, transport=self.api)
        self.assertEqual(testtag.status, "archiviert")
        return testtag

    def test_archived_testtag_reads_only_the_snapshot(self):
        testtag = self.archiv()
        self.api.calls.clear()
        self.api.rows = []  # veränderliche Zeilen dürfen keine Rolle spielen
        stand = q.lade_stand(self.zugang, testtag, transport=self.api)
        tables = [urllib.parse.urlsplit(c["url"]).path.rsplit("/", 1)[-1] for c in self.api.calls]
        self.assertEqual(tables, ["archiv_snapshots"])
        expected = next(g["eintraege"] for r in self.data["rows"] if r["oeffentliche_testtag_id"] == ARCHIV_ID
                        and r["bereich"] == "resultate_rangliste" and r["ak"] == "AK 7"
                        for g in r["inhalt"]["gruppen"] if g["liste"] == "gesamt")
        self.assertEqual(q.rangliste(stand, AK5, "AK 7", "gesamt"), expected)
        self.assertNotIn(("resultate_live", AK5, "AK 5"), {(d.bereich, d.programm, d.ak) for d in stand.saetze})
        self.assertFalse(any(d.bereich == "probe" for d in stand.saetze))
        self.assertEqual(q.programme(stand, q.STARTLISTE), [AK5, AK9])

    def test_missing_or_damaged_snapshot_gives_no_fallback(self):
        testtag = self.archiv()

        def change_value(snaps):
            snaps[0]["inhalt"]["veroeffentlichung"][0]["inhalt"]["gruppen"] = []
            return snaps

        def add_tombstone(snaps):
            from testtag_public import archive as ar
            from testtag_public import database_publisher as dp
            t = dp.tombstone_inhalt("resultate_live")
            snaps[0]["inhalt"]["veroeffentlichung"].append({"bereich": "resultate_live", "programm": AK5, "ak": "AK 99",
                                                            "pruefsumme": dp.content_checksum(t), "stand_am": PUB_T1,
                                                            "inhalt": t})
            snaps[0]["pruefsumme"] = ar.archive_checksum(snaps[0]["inhalt"])
            return snaps

        cases = {"ARCHIV_FEHLT": lambda snaps: [], "ARCHIV_PRUEFSUMME": change_value,
                 "ARCHIV_SCHEMA": lambda snaps: [dict(snaps[0], schema_version="archiv-0")],
                 "ARCHIV_TOMBSTONE": add_tombstone, "ARCHIV_DOPPELT": lambda snaps: snaps + snaps,
                 "ARCHIV_TESTTAG": lambda snaps: [dict(snaps[0], oeffentliche_testtag_id=LIVE_ID)]}
        for code, change in cases.items():
            with self.subTest(code=code):
                self.api.calls.clear()
                self.api.manipulieren_archiv = change
                with self.assertRaises(q.QuellenFehler) as ctx:
                    q.lade_stand(self.zugang, testtag, transport=self.api)
                self.assertEqual(ctx.exception.code, code)
                self.assert_neutral(str(ctx.exception))
                self.assertFalse(any("/veroeffentlichung?" in c["url"] for c in self.api.calls))


class AuswahlTests(Base):
    def test_live_is_sorted_newest_first_with_stable_secondary_order(self):
        resultate = q.live_resultate(self.stand())
        stamps = [r["geaendert_am"] for r in resultate]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual(stamps[0], PUB_T2)
        self.assertIn(PUB_T1, stamps)
        same = [r["ergebnis_id"] for r in resultate if r["geaendert_am"] == PUB_T1]
        self.assertEqual(same, sorted(same))

    def test_filters_by_program_and_ak(self):
        stand = self.stand()
        self.assertTrue(all(r["programm"] == AK9 for r in q.live_resultate(stand, AK9)))
        self.assertTrue(all(r["ak"] == "AK 9" for r in q.live_resultate(stand, AK9, "AK 9")))
        self.assertEqual(q.altersklassen(stand, q.RANGLISTE, AK9), ["AK 9", "AK 10", "AK 11"])

    def test_rangliste_startliste_zeitplan_follow_publisher(self):
        stand = self.stand()
        gesamt = q.rangliste(stand, AK5, "AK 7", "gesamt")
        self.assertEqual([e["rang"] for e in gesamt], [1, 2, 2, 4])
        listen = q.startlisten(stand)
        self.assertEqual([p for p, _g in listen], [AK5, AK9])
        self.assertEqual([g["startgeraet"] for g in listen[0][1]], ["Boden", "Pferd", "Beweglichkeit", "Reck"])
        self.assertEqual([b["programm"] for b in q.zeitplan(stand)], [AK5, AK9])

    def test_time_in_europe_zurich_with_fallback(self):
        self.assertEqual(q.zeit_text("2026-10-09T08:00:00Z"), "10:00:00")  # Sommerzeit
        self.assertEqual(q.zeit_text("2026-12-09T08:00:00+00:00", mit_datum=True), "09.12.2026 09:00")
        with mock.patch.dict(sys.modules, {"zoneinfo": None}):
            self.assertEqual(q.nach_zuerich(datetime(2026, 10, 9, 8, tzinfo=timezone.utc)).hour, 10)
            self.assertEqual(q.nach_zuerich(datetime(2026, 10, 25, 1, tzinfo=timezone.utc)).hour, 2)
            self.assertEqual(q.nach_zuerich(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)).hour, 1)
            self.assertEqual(q.nach_zuerich(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)).hour, 3)


if __name__ == "__main__":
    unittest.main()
