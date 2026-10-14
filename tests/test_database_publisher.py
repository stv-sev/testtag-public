"""P1C-Datenbank-Publisher gegen die simulierte Data API (kein echtes Netzwerk)."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import _support
from _support import (AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SECRET_MARKERS, TT_SLUG,
                      FakeDataApi, make_testtag_workbook, testtag_spec)

from testtag_public import database_publisher as dp
from testtag_public import publication_model as pm
from testtag_public import supabase_database as db
from testtag_public.excel_publication import read_program_source

T1 = "2026-10-09T08:00:00Z"
T2 = "2026-10-09T09:00:00Z"
T3 = "2026-10-09T10:00:00Z"
AK5_SCORES = {(1, "Boden", 1): (5, 1.5, 7.5), (2, "Pferd", 1): (4, 1, 4), (1, "Kraft", 1): (None, 1, 8)}
AK9_SCORES = {(1, "Sprung", 1): (3, 1, 3), (1, "Sprung", 2): (4, 1, 4), (3, "Sprung", 1): (5, 1, 5),
              (3, "Sprung", 3): (6, 1, 6)}


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
            changes = {"scores": AK5_SCORES if program == AK5 else AK9_SCORES, **(changes or {})}
            path = make_testtag_workbook(self.dir / f"wb{self.n}.xlsx", program, **changes)
            out.append(read_program_source(path, wait_seconds=0, sleep=lambda _s: None))
        return out

    def publish(self, api: FakeDataApi, sources: list, at: str = T1) -> dp.Outcome:
        return dp.publish(self.config, sources, observed_at=at, transport=api)

    def published(self, api: FakeDataApi, tid: str) -> dict:
        return {k[1:]: r for k, r in api.rows.items() if k[0] == tid}

    def assert_no_personal_data(self, value) -> None:
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for marker in TT_SECRET_MARKERS:
            self.assertNotIn(marker, text)

    def assert_keys_only_in_apikey(self, api: FakeDataApi) -> None:
        for call in api.calls:
            names = {k.lower() for k in call["headers"]}
            self.assertNotIn("authorization", names)
            self.assertNotIn(TEST_SECRET_KEY, json.dumps(call["params"]) + json.dumps(call["body"]))
            self.assertNotIn(TEST_PUBLISHABLE_KEY, json.dumps(call["params"]) + json.dumps(call["body"]))
            self.assertIn(call["headers"]["apikey"], (TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY))
            for value in call["headers"].values():
                self.assertNotIn("Bearer", value)


class FirstPublicationTests(Base):
    def test_first_publication_creates_testtag_and_writes_one_batch(self):
        api = FakeDataApi()
        outcome = self.publish(api, self.sources())
        self.assertEqual(outcome.status, "publiziert")
        self.assertTrue(outcome.testtag_neu)
        (tag,) = api.testtage.values()
        self.assertEqual((tag["slug"], tag["status"], tag["ist_oeffentlich"]), (TT_SLUG, "live", True))
        self.assertEqual(tag["stand_am"], T1.replace("Z", "+00:00"))
        self.assertIsNone(tag["datum"])
        self.assertIsNone(tag["ort"])

        posts = [c for c in api.calls if c["method"] == "POST" and c["table"] == "veroeffentlichung"]
        self.assertEqual(len(posts), 1)
        self.assertIsInstance(posts[0]["body"], list)
        self.assertEqual(len(posts[0]["body"]), 13)  # 1 Zeitplan + 2 Startlisten + 5 AK × (Live + Rangliste)
        self.assertEqual(dict(posts[0]["params"])["on_conflict"], "oeffentliche_testtag_id,bereich,programm,ak")
        self.assertIn("resolution=merge-duplicates", posts[0]["headers"]["Prefer"])
        self.assertEqual(len(self.published(api, tag["id"])), 13)
        self.assertEqual(outcome.schreibanfragen, 4)  # anlegen, Batch, freigeben, stand_am

    def test_order_of_operations_and_key_usage(self):
        api = FakeDataApi()
        self.publish(api, self.sources())
        steps = [(c["method"], c["table"], "secret" if c["headers"]["apikey"] == TEST_SECRET_KEY else "public")
                 for c in api.calls]
        batch = steps.index(("POST", "veroeffentlichung", "secret"))
        stand = max(i for i, s in enumerate(steps) if s == ("PATCH", "testtage", "secret"))
        self.assertLess(steps.index(("POST", "testtage", "secret")), batch)
        public_reads = [i for i, s in enumerate(steps) if s[2] == "public"]
        self.assertTrue(public_reads and all(s[0] == "GET" for s in (steps[i] for i in public_reads)))
        self.assertTrue(any(batch < i < stand for i in public_reads))  # Rücklesen vor stand_am
        self.assertTrue(any(i > stand for i in public_reads))  # abschliessende Prüfung
        self.assertEqual(api.calls[stand]["body"], {"stand_am": T1})
        writes = [s for s in steps if s[0] != "GET"]
        self.assertTrue(all(s[2] == "secret" for s in writes))
        self.assert_keys_only_in_apikey(api)

    def test_validation_error_in_all_programs_aborts_before_network(self):
        api = FakeDataApi()
        sources = self.sources(ak5={"direct": {(1, "Kraft", 1): 8}},  # Kraft vor Ort und extern
                               ak9={"scores": {**AK9_SCORES, (3, "Sprung", 2): (4, 1, 4)}})  # zwei Alternativen
        with self.assertRaises(pm.PublicationError) as ctx:
            self.publish(api, sources)
        self.assertEqual(set(ctx.exception.codes), {"ATHLETIK_BEIDE_QUELLEN", "SPRUNG_DOPPELTE_ALTERNATIVE"})
        self.assertEqual(api.calls, [])

    def test_error_in_one_program_keeps_its_public_state_and_publishes_the_others(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        vorher = copy.deepcopy(self.published(api, tid))
        neu = self.sources(ak5={"direct": {(1, "Kraft", 1): 8}},  # AK5 fehlerhaft: Kraft vor Ort und extern
                           ak9={"scores": {**AK9_SCORES, (1, "Boden", 1): (5, 1, 5)}})  # AK9: neues Resultat
        outcome = self.publish(api, neu, T2)
        self.assertEqual(outcome.status, "publiziert")
        self.assertEqual([(b["programm"], b["codes"]) for b in outcome.blockiert], [(AK5, ["ATHLETIK_BEIDE_QUELLEN"])])
        self.assertTrue(any("Noteneingabe!" in m and "Kraft".lower() in m for m in outcome.blockiert[0]["meldungen"]))
        self.assert_no_personal_data(outcome.blockiert)
        nachher = copy.deepcopy(self.published(api, tid))
        for key, row in vorher.items():
            if key[1] == AK5:
                self.assertEqual(nachher[key], row, key)  # alter Stand bleibt (kein Tombstone, nicht neu geschrieben)
        live9 = [k for k in nachher if k[0] == "resultate_live" and k[1] == AK9]
        self.assertTrue(any(nachher[k]["pruefsumme"] != vorher[k]["pruefsumme"] for k in live9))  # AK9 publiziert
        zeitplan = nachher[("zeitplan", "", "")]["inhalt"]["gruppen"]
        self.assertEqual([g["programm"] for g in zeitplan], [AK5, AK9])  # Block von AK5 bleibt im Zeitplan
        self.assertEqual(zeitplan[0], vorher[("zeitplan", "", "")]["inhalt"]["gruppen"][0])
        self.assertTrue(api.testtage[tid]["ist_oeffentlich"])
        # korrigiert: wieder vollständig, nichts mehr blockiert
        outcome = self.publish(api, self.sources(ak9={"scores": {**AK9_SCORES, (1, "Boden", 1): (5, 1, 5)}}), T3)
        self.assertEqual(outcome.blockiert, [])
        # Sperre geht vor: B6 = FALSCH in der fehlerhaften Mappe verbirgt das Programm
        gesperrt = self.sources(ak5={"direct": {(1, "Kraft", 1): 8}, "public": {"public_active": False}})
        outcome = self.publish(api, gesperrt, "2026-10-09T11:00:00Z")
        self.assertEqual(outcome.blockiert, [])
        live5 = [r for k, r in self.published(api, tid).items() if k[0] == "resultate_live" and k[1] == AK5]
        self.assertTrue(live5 and all(r["inhalt"].get("aktiv") is False for r in live5))  # Tombstones

    def test_existing_testtag_is_reused_without_duplicate(self):
        api = FakeDataApi()
        tid = api.add_testtag(TT_SLUG, titel="Alter Titel", status="vorbereitet", ist_oeffentlich=False)
        api.testtage[tid].update({"datum": "2026-10-09", "ort": "Musterhausen"})
        self.publish(api, self.sources())
        self.assertEqual(list(api.testtage), [tid])
        tag = api.testtage[tid]
        self.assertEqual((tag["titel"], tag["status"], tag["ist_oeffentlich"]), (_support.TT_TITLE, "live", True))
        self.assertEqual((tag["datum"], tag["ort"]), ("2026-10-09", "Musterhausen"))

    def test_archived_testtag_is_not_touched(self):
        api = FakeDataApi()
        api.add_testtag(TT_SLUG, status="archiviert")
        with self.assertRaises(dp.PublisherError) as ctx:
            self.publish(api, self.sources())
        self.assertEqual(ctx.exception.code, dp.PublisherCode.TESTTAG_ARCHIVIERT)
        self.assertEqual(api.writes(), [])

    def test_nothing_released_and_no_testtag_writes_nothing(self):
        api = FakeDataApi()
        outcome = self.publish(api, self.sources(ak5={"public": {"public_active": False}},
                                                 ak9={"public": {"public_active": False}}))
        self.assertEqual(outcome.status, "nichts_zu_publizieren")
        self.assertEqual(api.writes(), [])
        self.assertEqual(api.testtage, {})


class IdempotencyAndLiveTests(Base):
    def test_repeated_run_is_idempotent_and_sends_no_write(self):
        api = FakeDataApi()
        sources = self.sources()
        self.publish(api, sources, T1)
        snapshot = (json.dumps(sorted(map(list, api.rows.items())), sort_keys=True), json.dumps(api.testtage, sort_keys=True))
        before = len(api.calls)
        outcome = self.publish(api, sources, T2)
        self.assertEqual(outcome.status, "unveraendert")
        self.assertEqual(outcome.schreibanfragen, 0)
        self.assertEqual([c for c in api.calls[before:] if c["method"] != "GET"], [])
        self.assertEqual((json.dumps(sorted(map(list, api.rows.items())), sort_keys=True), json.dumps(api.testtage, sort_keys=True)), snapshot)
        self.assertTrue(any(c["headers"]["apikey"] == TEST_PUBLISHABLE_KEY for c in api.calls[before:]))

    def test_previous_live_state_is_used(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        changed = {**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5), (4, "Boden", 1): (3, 1, 3)}  # Korrektur + neu
        before = len(api.calls)
        outcome = self.publish(api, self.sources(ak5={"scores": changed}), T2)
        self.assertEqual(outcome.status, "publiziert")
        (batch,) = [c for c in api.calls[before:] if c["method"] == "POST"]
        written = {(r["bereich"], r["programm"], r["ak"]) for r in batch["body"]}
        self.assertEqual(written, {("resultate_live", AK5, "AK 7"), ("resultate_rangliste", AK5, "AK 7")})
        live = self.published(api, tid)[("resultate_live", AK5, "AK 7")]
        stamps = [(r["code"], r["T"] if "T" in r else r["punkte"], r["geaendert_am"])
                  for r in live["inhalt"]["gruppen"][0]["resultate"]]
        by_code = {}
        for code, value, at in stamps:
            by_code.setdefault(code, []).append((value, at))
        self.assertEqual(sorted(by_code["2-1"]), [(5, T2)])  # korrigiert
        self.assertIn((3, T2), by_code["1-1"])  # neu
        self.assertIn((7.5, T1), by_code["1-1"])  # unverändert
        self.assertEqual(by_code["9-1"], [(8, T1)])
        self.assertEqual([at for _c, _v, at in stamps][:2], [T2, T2])  # neueste zuerst
        untouched = self.published(api, tid)[("startliste", AK5, "")]
        self.assertEqual(untouched["stand_am"], T1.replace("Z", "+00:00"))
        self.assertEqual(api.testtage[tid]["stand_am"], T2.replace("Z", "+00:00"))

    def test_newly_released_rotation_counts_as_new(self):
        api = FakeDataApi()
        self.publish(api, self.sources(ak5={"public": {"published_rotation_max": 0}}), T1)
        tid = next(iter(api.testtage))
        self.assertEqual(self.published(api, tid)[("resultate_live", AK5, "AK 7")]["inhalt"]["gruppen"][0]["resultate"], [])
        self.publish(api, self.sources(), T2)
        results = self.published(api, tid)[("resultate_live", AK5, "AK 7")]["inhalt"]["gruppen"][0]["resultate"]
        self.assertEqual({r["geaendert_am"] for r in results}, {T2})
        self.assertEqual(len(results), 3)

    def test_incompatible_schema_version_aborts_before_writing(self):
        api = FakeDataApi()
        tid = api.add_testtag(TT_SLUG)
        api.add_row(tid, "resultate_live", AK5, "AK 7", {"schema_version": "alt-0", "gruppen": []})
        with self.assertRaises(dp.PublisherError) as ctx:
            self.publish(api, self.sources())
        self.assertEqual(ctx.exception.code, dp.PublisherCode.SCHEMA_INKOMPATIBEL)
        self.assertEqual(api.writes(), [])


class FailureTests(Base):
    def test_batch_failure_gives_no_success_and_no_stand_am(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        api.fail_batch = 500
        before = len(api.calls)
        with self.assertRaises(dp.PublisherError) as ctx:
            self.publish(api, self.sources(ak5={"scores": {**AK5_SCORES, (2, "Pferd", 1): (6, 1, 6)}}), T2)
        message = str(ctx.exception)
        self.assertEqual(ctx.exception.code, dp.PublisherCode.DB_SCHREIBEN)
        self.assertIn("NICHT vollständig publiziert", message)
        self.assertIn("HTTP 500", message)
        self.assert_no_personal_data(message)
        self.assertNotIn("Failing row", message)
        self.assertEqual([c for c in api.calls[before:] if c["method"] == "PATCH"], [])
        self.assertEqual([c for c in api.calls[before:] if c["method"] == "POST"].__len__(), 1)  # kein Einzelversuch
        self.assertEqual(api.testtage[tid]["stand_am"], T1.replace("Z", "+00:00"))

    def test_batch_failure_on_new_testtag_reports_incomplete_initial_state(self):
        api = FakeDataApi(fail_batch=503)
        with self.assertRaises(dp.PublisherError) as ctx:
            self.publish(api, self.sources())
        self.assertIn("neu angelegt, ist aber nicht öffentlich", str(ctx.exception))
        (tag,) = api.testtage.values()
        self.assertEqual((tag["status"], tag["ist_oeffentlich"]), ("vorbereitet", False))

    def test_incomplete_batch_response_is_detected(self):
        api = FakeDataApi(batch_drop=True)
        with self.assertRaises(dp.PublisherError) as ctx:
            self.publish(api, self.sources())
        self.assertEqual(ctx.exception.code, dp.PublisherCode.BATCH_UNVOLLSTAENDIG)

    def test_public_readback_detects_content_and_checksum_deviation(self):
        def change_content(rows):
            for row in rows:
                if row["bereich"] == "zeitplan":
                    row["inhalt"]["gruppen"] = []
            return rows

        def change_checksum(rows):
            for row in rows:
                if row["bereich"] == "startliste":
                    row["pruefsumme"] = "0" * 64
            return rows

        for tamper in (change_content, change_checksum, lambda rows: rows[:-1]):
            with self.subTest(tamper=tamper):
                api = FakeDataApi(tamper_public=tamper)
                with self.assertRaises(dp.PublisherError) as ctx:
                    self.publish(api, self.sources())
                self.assertEqual(ctx.exception.code, dp.PublisherCode.RUECKLESEN_ABWEICHUNG)
                stand_patches = [c for c in api.calls if c["method"] == "PATCH" and "stand_am" in c["body"]]
                self.assertEqual(stand_patches, [])

    def test_transport_errors_are_redacted(self):
        def broken(method, url, headers, body, timeout):
            raise db.TransportError(f"kaputt {TEST_SECRET_KEY} Synthaler")

        with self.assertRaises(dp.PublisherError) as ctx:
            dp.publish(self.config, self.sources(), observed_at=T1, transport=broken)
        self.assertNotIn(TEST_SECRET_KEY, str(ctx.exception))


class TombstoneTests(Base):
    def test_locked_program_becomes_tombstones_and_probe_stays(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        probe = {"schema_version": "probe-1", "event": {"x": 1}}
        api.add_row(tid, "probe", AK5, "", probe, stand_am="2026-09-01T00:00:00Z")
        probe_before = json.dumps(api.rows[(tid, "probe", AK5, "")], sort_keys=True)

        outcome = self.publish(api, self.sources(ak9={"public": {"public_active": False}}), T2)
        self.assertEqual(outcome.status, "publiziert")
        rows = self.published(api, tid)
        ak9_rows = {k: r for k, r in rows.items() if k[1] == AK9}
        self.assertEqual(len(ak9_rows), 7)  # Startliste + 3 AK × 2
        for row in ak9_rows.values():
            self.assertEqual(row["inhalt"], {"schema_version": pm.SCHEMA_VERSION, "art": row["bereich"],
                                             "aktiv": False, "gruppen": []})
            self.assertEqual(row["pruefsumme"], pm.content_checksum(row["inhalt"]))
        self.assert_no_personal_data([r["inhalt"] for r in ak9_rows.values()])
        self.assertEqual([g["programm"] for g in rows[("zeitplan", "", "")]["inhalt"]["gruppen"]], [AK5])
        self.assertEqual(json.dumps(api.rows[(tid, "probe", AK5, "")], sort_keys=True), probe_before)
        self.assertTrue(api.testtage[tid]["ist_oeffentlich"])
        self.assertEqual(outcome.tombstones, 7)
        # Wiederholung: Tombstones bleiben unverändert, keine Schreibanfrage
        again = self.publish(api, self.sources(ak9={"public": {"public_active": False}}), T3)
        self.assertEqual((again.status, again.schreibanfragen), ("unveraendert", 0))

    def test_removed_ak_becomes_tombstone(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        people = [p for p in testtag_spec(AK5)["participants"] if p[5] != "AK 6"]
        self.publish(api, self.sources(ak5={"participants": people}), T2)
        rows = self.published(api, tid)
        for bereich in ("resultate_live", "resultate_rangliste"):
            self.assertTrue(dp.is_tombstone(rows[(bereich, AK5, "AK 6")]["inhalt"]))
            self.assertFalse(dp.is_tombstone(rows[(bereich, AK5, "AK 7")]["inhalt"]))

    def test_full_lock_clears_personal_data_then_hides_testtag(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        before = len(api.calls)
        locked = self.sources(ak5={"public": {"public_active": False}}, ak9={"public": {"public_active": False}})
        outcome = self.publish(api, locked, T2)
        self.assertEqual(outcome.status, "publiziert")
        self.assertFalse(outcome.oeffentlich)
        rows = self.published(api, tid)
        self.assertEqual(len(rows), 13)
        self.assertTrue(all(dp.is_tombstone(r["inhalt"]) for r in rows.values()))
        self.assert_no_personal_data([r["inhalt"] for r in rows.values()])
        self.assertFalse(api.testtage[tid]["ist_oeffentlich"])
        calls = api.calls[before:]
        batch = next(i for i, c in enumerate(calls) if c["method"] == "POST")
        hide = next(i for i, c in enumerate(calls) if c["method"] == "PATCH" and c["body"].get("ist_oeffentlich") is False)
        self.assertLess(batch, hide)
        status, body = api("GET", db.table_url(self.config, "veroeffentlichung", [("select", "inhalt")]),
                           {"apikey": TEST_PUBLISHABLE_KEY}, None, 5)
        self.assertEqual(json.loads(body), [])
        again = self.publish(api, locked, T3)
        self.assertEqual((again.status, again.schreibanfragen), ("unveraendert", 0))


class CodeTests(Base):
    def test_sprung_codes_stay_5_n_and_ak9_display_is_neutral(self):
        api = FakeDataApi()
        self.publish(api, self.sources(), T1)
        tid = next(iter(api.testtage))
        rows = self.published(api, tid)
        ak9 = rows[("resultate_live", AK9, "AK 9")]["inhalt"]["gruppen"][0]["resultate"]
        ak10 = rows[("resultate_live", AK9, "AK 10")]["inhalt"]["gruppen"][0]["resultate"]
        self.assertEqual(sorted((r["code"], r["anzeige"]) for r in ak9), [("sprung-1", "Sprung 1"), ("sprung-2", "Sprung 2")])
        self.assertEqual(sorted(r["code"] for r in ak10), ["5-1", "5-3"])
        self.assertNotIn("Tsukahara", json.dumps(ak9, ensure_ascii=False))

    def test_rotation_position_does_not_change_apparatus_or_line_codes(self):
        spec = testtag_spec(AK9, scores={(3, "Sprung", 1): (5, 1, 5)})
        reordered = testtag_spec(AK9, scores={(3, "Sprung", 1): (5, 1, 5)},
                                 stations=[("Sprung", 5), ("Boden", 1), ("Pferd", 2), ("Ringe", 3), ("Barren", 6),
                                           ("Trampolin", 7), ("Reck", 8)])
        codes = []
        for s in (spec, reordered):
            self.n += 1
            path = make_testtag_workbook(self.dir / f"r{self.n}.xlsx", spec=s)
            state = pm.build_state([read_program_source(path, wait_seconds=0, sleep=lambda _s: None)], observed_at=T1)
            codes.append([(r["geraet"], r["code"]) for r in state["einzelwertungen"]])
        self.assertEqual(codes[0], [("Sprung", "5-1")])
        self.assertEqual(codes[0], codes[1])


if __name__ == "__main__":
    unittest.main()
