"""P1B Ende-zu-Ende mit synthetischen Arbeitsmappen (Struktur wie produktiv, nur erfundene Personen)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import _support
from _support import AK5, AK9, TT_SECRET_MARKERS, make_testtag_workbook, testtag_spec

from testtag_public import excel_publication as ep
from testtag_public import publication_model as pm
from testtag_public.publication_model import Code

T1 = "2026-10-09T08:00:00Z"
T2 = "2026-10-09T09:00:00Z"
EXTRA_AK7 = (5, "Ersonnen", "Mia", 2018, "TV Testdorf", "AK 7", 7, "Reck")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.count = 0

    def book(self, program: str = AK5, **changes) -> Path:
        self.count += 1
        return make_testtag_workbook(self.dir / f"wb{self.count}.xlsx", program, **changes)

    def state(self, *paths: Path, observed_at: str = T1, previous=None) -> dict:
        return ep.load_publication_state(paths, observed_at=observed_at, previous=previous, wait_seconds=0,
                                         sleep=lambda _s: None)

    def error(self, code: str, *paths: Path) -> pm.PublicationError:
        with self.assertRaises(pm.PublicationError) as ctx:
            self.state(*paths)
        self.assertIn(code, ctx.exception.codes, str(ctx.exception))
        self.assert_anonymous(str(ctx.exception))
        return ctx.exception

    def assert_anonymous(self, text: str) -> None:
        for marker in TT_SECRET_MARKERS:
            self.assertNotIn(marker, text)
        self.assertNotIn(str(self.dir), text)
        self.assertNotIn(self.dir.name, text)

    @staticmethod
    def results(state: dict, programm: str = AK5, bereich: str | None = None) -> list[dict]:
        return [r for r in state["einzelwertungen"]
                if r["programm"] == programm and (bereich is None or r["bereich"] == bereich)]

    @staticmethod
    def ranking(state: dict, programm: str, ak: str, liste: str) -> list[dict]:
        group = next(g for g in state["ranglisten"][programm] if g["ak"] == ak)
        return group[liste]


class TesttagTests(Base):
    def test_two_programs_form_one_testtag(self):
        state = self.state(self.book(AK9), self.book(AK5))
        self.assertEqual(state["schema_version"], pm.SCHEMA_VERSION)
        self.assertEqual(state["event"], {"slug": _support.TT_SLUG, "titel": _support.TT_TITLE})
        self.assertEqual([p["programm"] for p in state["programme"]], [AK5, AK9])
        self.assertTrue(all(len(p["quelle"]["sha256"]) == 64 for p in state["programme"]))
        rows = pm.publication_rows(state)
        kinds = [(r["bereich"], r["programm"], r["ak"]) for r in rows]
        self.assertEqual(kinds.count(("zeitplan", "", "")), 1)
        self.assertIn(("startliste", AK5, ""), kinds)
        self.assertIn(("startliste", AK9, ""), kinds)
        for program, aks in ((AK5, ["AK 6", "AK 7"]), (AK9, ["AK 9", "AK 10", "AK 11"])):
            for ak in aks:
                self.assertIn(("resultate_live", program, ak), kinds)
                self.assertIn(("resultate_rangliste", program, ak), kinds)
        for row in rows:
            self.assertEqual(row["inhalt"]["schema_version"], pm.SCHEMA_VERSION)
            self.assertEqual(row["inhalt"]["art"], row["bereich"])
            self.assertIn("gruppen", row["inhalt"])
            self.assertEqual(row["pruefsumme"], pm.content_checksum(row["inhalt"]))
            self.assertNotIn("id", row)

    def test_differing_slug_or_title_is_rejected(self):
        for field in ("event_id", "event_title"):
            with self.subTest(field=field):
                other = self.book(AK9, public={field: "abweichend-2026"})
                self.error(Code.EVENT_WIDERSPRUCH, self.book(AK5), other)

    def test_duplicate_or_unknown_program_is_rejected(self):
        self.error(Code.PROGRAMM_DOPPELT, self.book(AK5), self.book(AK5))
        self.error(Code.PROGRAMM_UNBEKANNT, self.book(AK5, public={"program": "AK1-AK2"}))

    def test_inactive_program_produces_no_public_content(self):
        state = self.state(self.book(AK5), self.book(AK9, public={"public_active": False},
                                                     direct={(1, "Kraft", 1): 5}))
        self.assertEqual([p["aktiv"] for p in state["programme"]], [True, False])
        self.assertEqual([z["programm"] for z in state["zeitplan"]], [AK5])
        self.assertNotIn(AK9, state["startlisten"])
        self.assertNotIn(AK9, state["ranglisten"])
        self.assertEqual(self.results(state, AK9), [])
        self.assertFalse(any(r["programm"] == AK9 for r in pm.publication_rows(state)))

    def test_public_active_and_rotation_limit_are_validated(self):
        for program, override in ((AK5, {"public_active": None}), (AK5, {"public_active": "ja"}),
                                  (AK5, {"published_rotation_max": 9}), (AK9, {"published_rotation_max": 8}),
                                  (AK5, {"published_rotation_max": None}), (AK5, {"published_rotation_max": 2.5})):
            with self.subTest(program=program, override=override):
                self.error(Code.PUBLIC_UNGUELTIG, self.book(program, public=override))


class RotationTests(Base):
    SCORES = {(1, "Boden", 1): (5, 1.5, 7.5), (1, "Pferd", 1): (2, 1, 2), (1, "Ringe", 1): (3, 1, 3)}

    def test_rotation_limit_zero_partial_and_full(self):
        for limit, expected in ((0, set()), (2, {"1-1", "2-1"}), (8, {"1-1", "2-1", "3-1"})):
            with self.subTest(limit=limit):
                state = self.state(self.book(scores=self.SCORES, public={"published_rotation_max": limit}))
                self.assertEqual({r["code"] for r in self.results(state, bereich="technik")}, expected)
                self.assertTrue(all(r["rotation"] <= limit for r in self.results(state)))

    def test_later_rotations_do_not_influence_live_list_or_ranking(self):
        base = self.state(self.book(scores=self.SCORES, public={"published_rotation_max": 2}))
        later = {**self.SCORES, (1, "Reck", 1): (9, 1, 9), (2, "Boden", 1): (9, 1, 9)}  # Rotation 8
        more = self.state(self.book(scores=later, public={"published_rotation_max": 2}))
        self.assertEqual(base["einzelwertungen"], more["einzelwertungen"])
        self.assertEqual(base["ranglisten"], more["ranglisten"])
        self.assertEqual([r["pruefsumme"] for r in pm.publication_rows(base)],
                         [r["pruefsumme"] for r in pm.publication_rows(more)])
        self.assertEqual(self.ranking(base, AK5, "AK 7", "technik")[0]["punkte"], 9.5)

    def test_onsite_athletics_respects_rotation_limit(self):
        scores = {(1, "Kraft", 1): (None, 1, 8)}  # Kraft 1 an Station Ringe: Rotation 3 für Startgerät Boden
        hidden = self.state(self.book(scores=scores, public={"published_rotation_max": 2}))
        self.assertEqual(self.results(hidden, bereich="kraft"), [])
        shown = self.state(self.book(scores=scores, public={"published_rotation_max": 3}))
        self.assertEqual([(r["rotation"], r["punkte"]) for r in self.results(shown, bereich="kraft")], [(3, 8)])

    def test_external_athletics_is_visible_without_rotation(self):
        state = self.state(self.book(AK9, public={"published_rotation_max": 0},
                                     direct={(1, "Kraft", 14): 6.5, (2, "Beweglichkeit", 10): 3}))
        visible = sorted((r["bereich"], r["code"], r["rotation"], r["punkte"]) for r in self.results(state, AK9))
        self.assertEqual(visible, [("beweglichkeit", "4-10", None, 3), ("kraft", "9-14", None, 6.5)])
        ak5 = self.state(self.book(public={"published_rotation_max": 0, "Kraftmodus": "extern"},
                                   direct={(1, "Kraft", 2): 4}))
        self.assertEqual([(r["code"], r["rotation"]) for r in self.results(ak5)], [("9-2", None)])

    def test_invalid_rotation_of_a_result_is_rejected(self):
        for value in (0, 9, None, "x"):
            with self.subTest(value=value):
                self.error(Code.ROTATION_UNGUELTIG, self.book(scores={(1, "Boden", 1): (5, 1, 5)},
                                                              rotation={(1, "Boden", 1): value}))


class AthleticsModeTests(Base):
    def test_all_four_mode_combinations(self):
        for kraft in ("vor_ort", "extern"):
            for bew in ("vor_ort", "extern"):
                with self.subTest(kraft=kraft, bew=bew):
                    scores, direct = {}, {}
                    if kraft == "vor_ort":
                        scores[(1, "Kraft", 5)] = (None, 1, 4)  # Station Pferd, Rotation 2
                    else:
                        direct[(1, "Kraft", 5)] = 4
                    if bew == "vor_ort":
                        scores[(1, "Beweglichkeit", 7)] = (3, 1, 3)  # Rotation 4
                    else:
                        direct[(1, "Beweglichkeit", 7)] = 3
                    state = self.state(self.book(scores=scores, direct=direct,
                                                 public={"Kraftmodus": kraft, "Beweglichkeitsmodus": bew}))
                    got = {r["bereich"]: (r["erfassung"], r["rotation"], r["punkte"]) for r in self.results(state)}
                    self.assertEqual(got["kraft"], (kraft, 2 if kraft == "vor_ort" else None, 4))
                    self.assertEqual(got["beweglichkeit"], (bew, 4 if bew == "vor_ort" else None, 3))
                    top = self.ranking(state, AK5, "AK 7", "athletik")[0]
                    self.assertEqual((top["kraft"], top["beweglichkeit"], top["athletik"]), (4, 3, 7))

    def test_empty_or_invalid_mode_is_rejected(self):
        for value in (None, "", "vorort", "Extern", "beides"):
            for field in ("Kraftmodus", "Beweglichkeitsmodus"):
                with self.subTest(field=field, value=value):
                    self.error(Code.ATHLETIKMODUS_UNGUELTIG, self.book(public={field: value}))

    def test_ak9_accepts_only_extern(self):
        self.state(self.book(AK9))
        for field in ("Kraftmodus", "Beweglichkeitsmodus"):
            for value in ("vor_ort", None):
                with self.subTest(field=field, value=value):
                    self.error(Code.ATHLETIKMODUS_UNGUELTIG, self.book(AK9, public={field: value}))

    def test_values_in_both_sources_are_a_conflict(self):
        exc = self.error(Code.ATHLETIK_BEIDE_QUELLEN, self.book(scores={(1, "Kraft", 1): (None, 1, 8)},
                                                                direct={(1, "Kraft", 1): 8}))
        self.assertIn("kraft", str(exc))
        self.assertNotIn("beweglichkeit", str(exc))

    def test_value_only_in_inactive_source_is_a_conflict_per_area(self):
        exc = self.error(Code.ATHLETIK_INAKTIVE_QUELLE,
                         self.book(public={"Beweglichkeitsmodus": "extern"}, scores={(2, "Beweglichkeit", 1): (2, 1, 2)}))
        self.assertIn("beweglichkeit", str(exc))
        self.assertNotIn("kraft", str(exc))
        self.error(Code.ATHLETIK_INAKTIVE_QUELLE, self.book(direct={(2, "Kraft", 3): 0}))  # Null zählt

    def test_prefilled_tal_alone_is_not_a_result(self):
        # Vor-Ort-Blöcke enthalten TAL = 1 für alle Personen; extern gewählt → kein Konflikt.
        state = self.state(self.book(public={"Kraftmodus": "extern", "Beweglichkeitsmodus": "extern"},
                                     direct={(1, "Kraft", 1): 5},
                                     scores={(2, "Beweglichkeit", 2): (None, 1, None)}))
        self.assertEqual([(r["bereich"], r["punkte"]) for r in self.results(state)], [("kraft", 5)])

    def test_zero_is_a_result_and_missing_stays_missing(self):
        state = self.state(self.book(direct={}, scores={(1, "Kraft", 5): (None, 1, 0), (1, "Boden", 1): (0, 1, 0)}))
        got = {r["bereich"]: r for r in self.results(state)}
        self.assertEqual(got["kraft"]["punkte"], 0)
        self.assertEqual((got["technik"]["S"], got["technik"]["T"]), (0, 0))
        others = [r for r in self.results(state) if r["teilnehmer"] != got["kraft"]["teilnehmer"]]
        self.assertEqual(others, [])

    def test_ak9_direct_fields_must_be_complete(self):
        self.error(Code.STRUKTUR_UNGUELTIG, self.book(AK9, kraft_names=_support.AK9_KRAFT[:13]))
        self.error(Code.STRUKTUR_UNGUELTIG, self.book(AK5, bew_names=_support.AK5_BEW[:6]))


class TechnikTests(Base):
    def test_s_is_sks_and_t_is_end_without_tal_or_internal_fields(self):
        state = self.state(self.book(scores={(1, "Boden", 1): (5, 1.5, 7.5)}))
        (res,) = self.results(state)
        self.assertEqual((res["S"], res["T"], res["code"], res["anzeige"], res["rotation"]),
                         (5, 7.5, "1-1", "Synth Boden vorwärts", 1))
        self.assertEqual(res["status"], pm.STATUS_GEWERTET)
        text = json.dumps(pm.publication_rows(state), ensure_ascii=False)
        for forbidden in ('"TAL"', '"tal"', "Noteneingabe!", "excel_id", "zelle", "id_athlete", "1.5,"):
            self.assertNotIn(forbidden, text)

    def test_end_must_equal_sks_times_tal(self):
        self.error(Code.ENDWERT_INKONSISTENT, self.book(scores={(1, "Boden", 1): (5, 1.5, 7)}))
        self.error(Code.ENDWERT_INKONSISTENT, self.book(scores={(1, "Boden", 1): (5, None, 5)}))

    def test_sks_without_end_is_not_published(self):
        state = self.state(self.book(scores={(1, "Boden", 1): (5, None, None)}))
        self.assertEqual(self.results(state), [])
        self.assertEqual(state["diagnose"][AK5]["technik"]["unvollstaendig"], 1)

    def test_text_in_value_cell_is_rejected(self):
        self.error(Code.WERT_UNGUELTIG, self.book(scores={(1, "Boden", 1): ("fünf", 1, None)}))

    def test_result_in_line_not_intended_for_ak_is_a_conflict(self):
        # Boden Linie 2 ist für AK 6 nicht vorgesehen (akMinMax 0/0); Person 3 ist AK 6.
        self.state(self.book())  # leer: kein Konflikt
        self.error(Code.LINIE_NICHT_VORGESEHEN, self.book(scores={(3, "Boden", 2): (2, 1, 2)}))
        state = self.state(self.book(scores={(1, "Boden", 2): (2, 1, 2)}))  # AK 7: vorgesehen
        self.assertEqual([r["code"] for r in self.results(state)], ["1-2"])

    def test_result_in_line_without_definition_is_a_conflict(self):
        self.error(Code.LINIE_NICHT_VORGESEHEN, self.book(scores={(1, "Boden", 3): (2, 1, 2)}))


class SprungTests(Base):
    def test_ak9_has_neutral_sprung_1_and_2(self):
        state = self.state(self.book(AK9, scores={(1, "Sprung", 1): (3, 1, 3), (1, "Sprung", 2): (4, 1.5, 6)}))
        got = sorted((r["code"], r["anzeige"], r["T"]) for r in self.results(state, AK9))
        self.assertEqual(got, [("sprung-1", "Sprung 1", 3), ("sprung-2", "Sprung 2", 6)])
        self.assertNotIn("Tsukahara", json.dumps(self.results(state, AK9), ensure_ascii=False))
        self.assertEqual(self.ranking(state, AK9, "AK 9", "technik")[0]["technik"], 9)
        self.assertEqual(state["diagnose"][AK9]["sprung"]["ak9_quellen"],
                         {"Sprung 1 aus Excel-Linie 1": 1, "Sprung 2 aus Excel-Linie 2": 1})

    def test_ak9_third_line_is_rejected(self):
        self.error(Code.LINIE_NICHT_VORGESEHEN, self.book(AK9, scores={(1, "Sprung", 3): (3, 1, 3)}))

    def test_ak10_with_one_alternative_is_valid(self):
        state = self.state(self.book(AK9, scores={(3, "Sprung", 1): (5, 1, 5), (3, "Sprung", 3): (6, 1, 6)}))
        got = sorted((r["code"], r["anzeige"]) for r in self.results(state, AK9))
        self.assertEqual(got, [("5-1", "Überschlag vw"), ("5-3", "Yurchenko")])
        self.assertEqual(self.ranking(state, AK9, "AK 10", "technik")[0]["technik"], 11)

    def test_ak10_without_alternative_is_an_intermediate_state(self):
        state = self.state(self.book(AK9, scores={(3, "Sprung", 1): (5, 1, 5)}))
        self.assertEqual(len(self.results(state, AK9)), 1)
        self.assertEqual(state["diagnose"][AK9]["sprung"]["ak10_12_ohne_alternative"], 2)

    def test_two_alternatives_are_rejected(self):
        self.error(Code.SPRUNG_DOPPELTE_ALTERNATIVE,
                   self.book(AK9, scores={(4, "Sprung", 2): (5, 1, 5), (4, "Sprung", 3): (6, 1, 6)}))


class ListsTests(Base):
    def test_zeitplan_is_combined_in_program_order(self):
        state = self.state(self.book(AK9), self.book(AK5))
        self.assertEqual([z["programm"] for z in state["zeitplan"]], [AK5, AK9])
        self.assertEqual(state["zeitplan"][0]["zeilen"], [
            {"zeit": "07:30", "programmpunkt": "Hallenöffnung", "hinweis": "AK5–AK9+"},
            {"zeit": "08:05–12:30", "programmpunkt": "Wettkampf", "hinweis": ""},
        ])
        self.assertEqual(state["zeitplan"][1]["zeilen"][0]["zeit"], "13:55")
        (row,) = [r for r in pm.publication_rows(state) if r["bereich"] == "zeitplan"]
        self.assertEqual(row["inhalt"]["gruppen"], state["zeitplan"])

    def test_startliste_grouped_by_program_and_start_apparatus(self):
        state = self.state(self.book(AK5), self.book(AK9))
        groups = state["startlisten"][AK5]
        self.assertEqual([g["startgeraet"] for g in groups], ["Boden", "Pferd", "Beweglichkeit"])
        pferd = groups[1]["teilnehmer"]
        self.assertEqual([p["position"] for p in pferd], [1, 2])
        self.assertTrue(all(p["programm"] == AK5 and p["startgeraet"] == "Pferd" for p in pferd))
        self.assertEqual(set(pferd[0]), {"position", "teilnehmer", "name", "vorname", "verein", "jahrgang", "ak",
                                         "programm", "startgeraet"})
        self.assertEqual([g["startgeraet"] for g in state["startlisten"][AK9]], ["Boden", "Sprung", "Reck"])

    def test_startliste_row_carries_assignment_from_codes_and_released_rotation(self):
        state = self.state(self.book(AK5, public={"published_rotation_max": 7}), self.book(AK9))
        rows = {(r["bereich"], r["programm"]): r for r in pm.publication_rows(state)}
        e5 = rows[("startliste", AK5)]["inhalt"]["einteilung"]
        stationen = [s.name for s in ep.read_program_source(self.dir / "wb1.xlsx", wait_seconds=0,
                                                              sleep=lambda _s: None).stations]
        technik = [s for s in stationen if s != "Beweglichkeit"]
        self.assertEqual(e5["geraete"], technik)  # nur Technikgeräte, Reihenfolge tab_startgeraet
        self.assertEqual((e5["rotationen"], e5["freigegeben"]), (8, 7))  # Public!B5, nie B7
        self.assertEqual([(r["rotation"], r["geraet"]) for r in e5["riegen"]],
                         [(rot, g) for rot in range(1, 9) for g in technik])
        # Riege je Rotation × Gerät = Abschnitt, der laut Startliste-Code dort turnt, in Startposition
        n = len(stationen)
        for riege in e5["riegen"]:
            start = stationen[(stationen.index(riege["geraet"]) - riege["rotation"] + 1) % n]
            gruppe = next((g for g in state["startlisten"][AK5] if g["startgeraet"] == start), {"teilnehmer": []})
            self.assertEqual(riege["teilnehmer"], [t["teilnehmer"] for t in gruppe["teilnehmer"]], riege)
        self.assertTrue(any(r["teilnehmer"] for r in e5["riegen"]))
        e9 = rows[("startliste", AK9)]["inhalt"]["einteilung"]
        self.assertEqual((e9["rotationen"], len(e9["geraete"]), len(e9["riegen"])), (7, 7, 49))
        # Freigabe ändert die Startlisten-Zeile (Prüfsumme), Gruppen und Riegen bleiben gleich
        lower = self.state(self.book(AK5, public={"published_rotation_max": 3}), self.book(AK9))
        row3 = next(r for r in pm.publication_rows(lower) if r["bereich"] == "startliste" and r["programm"] == AK5)
        self.assertEqual(row3["inhalt"]["einteilung"]["freigegeben"], 3)
        self.assertEqual(row3["inhalt"]["einteilung"]["riegen"], e5["riegen"])
        self.assertEqual(row3["inhalt"]["gruppen"], rows[("startliste", AK5)]["inhalt"]["gruppen"])
        self.assertNotEqual(row3["pruefsumme"], rows[("startliste", AK5)]["pruefsumme"])
        self.assertNotIn("rotation", row3["inhalt"])

    def test_assignment_codes_are_validated(self):
        n = len(testtag_spec(AK5)["stations"])
        faelle = {
            "fehlt": {(1, 1): None},
            "unlesbar": {(1, 1): "@1-1-1-1"},
            "andere ID": {(1, 1): "@2°1ç1^1"},
            "andere Rotation": {(1, 2): "@1°3ç2^1"},
            "anderes Gerät": {(1, 1): "@1°1ç2^1"},
            "Position 0": {(1, 1): "@1°1ç1^0"},
        }
        for fall, codes in faelle.items():
            with self.subTest(fall=fall):
                self.error(Code.EINTEILUNG_UNGUELTIG, self.book(AK5, startliste_codes=codes))
        with self.subTest(fall="ohne Legende"):
            self.error(Code.EINTEILUNG_UNGUELTIG, self.book(AK5, ohne_legende=True))
        # doppelte Startposition an einem Gerät in einer Rotation (beide Personen im Abschnitt Pferd)
        pferd = [p[0] for p in testtag_spec(AK5)["participants"] if p[7] == "Pferd"]
        self.assertGreaterEqual(len(pferd), 2)
        a, b = pferd[:2]
        stationen = [s for s, _c in testtag_spec(AK5)["stations"]]
        ger = stationen.index("Pferd") + 1
        with self.subTest(fall="doppelte Position"):
            self.error(Code.EINTEILUNG_UNGUELTIG, self.book(AK5, startliste_codes={(b, 1): f"@{b}°1ç{ger}^1"}))
        # Rotation laut Noteneingabe widerspricht der Einteilung (gültige, aber andere Rotation)
        start = next(p[7] for p in testtag_spec(AK5)["participants"] if p[0] == 1)
        falsch = (stationen.index("Boden") - stationen.index(start) + 1) % n + 1
        with self.subTest(fall="id_rotation anders"):
            self.error(Code.EINTEILUNG_UNGUELTIG, self.book(AK5, scores={(1, "Boden", 1): (5, 1, 5)},
                                                            rotation={(1, "Boden", 1): falsch}))

    def test_display_texts_are_cleaned(self):
        people = testtag_spec(AK5)["participants"]
        people[0] = (1, "  Synthaler  ", "Lea   Sophie", 2018, " TV  Testdorf ", "AK 7", 7, "Boden")
        state = self.state(self.book(participants=people))
        first = state["startlisten"][AK5][0]["teilnehmer"][0]
        self.assertEqual((first["name"], first["vorname"], first["verein"]), ("Synthaler", "Lea Sophie", "TV Testdorf"))

    def test_duplicate_public_identity_is_rejected(self):
        people = testtag_spec(AK5)["participants"]
        people.append((6, "synthaler ", " LEA", 2018, "TV   Testdorf", "AK 7", 7, "Reck"))
        exc = self.error(Code.TEILNEHMER_DOPPELT, self.book(participants=people))
        self.assertIn("Anmeldung Zeilen", str(exc))


class RankingTests(Base):
    def test_sums_and_ranks(self):
        people = testtag_spec(AK5)["participants"] + [EXTRA_AK7]
        scores = {(1, "Boden", 1): (5, 1.5, 7.5), (1, "Reck", 1): (4, 1, 4), (1, "Kraft", 1): (None, 1, 8),
                  (1, "Beweglichkeit", 3): (2, 1, 2), (2, "Boden", 1): (6, 1, 6), (4, "Pferd", 1): (6, 1, 6)}
        state = self.state(self.book(participants=people, scores=scores))
        gesamt = self.ranking(state, AK5, "AK 7", "gesamt")
        by_name = {e["name"]: e for e in gesamt}
        self.assertEqual({k: by_name["Synthaler"][k] for k in ("technik", "beweglichkeit", "kraft", "athletik",
                                                                 "gesamt")},
                         {"technik": 11.5, "beweglichkeit": 2, "kraft": 8, "athletik": 10, "gesamt": 21.5})
        self.assertEqual([(e["name"], e["rang"]) for e in gesamt],
                         [("Synthaler", 1), ("Fiktivo", 2), ("Imaginaer", 2), ("Ersonnen", 4)])
        self.assertEqual(self.ranking(state, AK5, "AK 7", "athletik")[0]["punkte"], 10)
        self.assertEqual([e["rang"] for e in self.ranking(state, AK5, "AK 7", "athletik")], [1, 2, 2, 2])
        self.assertNotIn(999, [e["gesamt"] for e in gesamt])  # Excel-Totale werden nie übernommen

    def test_all_participants_appear_even_without_results(self):
        state = self.state(self.book())
        for ak, size in (("AK 6", 1), ("AK 7", 3)):
            for liste in ("technik", "athletik", "gesamt"):
                entries = self.ranking(state, AK5, ak, liste)
                self.assertEqual(len(entries), size)
                self.assertTrue(all(e["punkte"] == 0 and e["rang"] == 1 for e in entries))
        self.assertEqual(self.results(state), [])


class LiveAndChecksumTests(Base):
    def test_new_and_corrected_results_come_first(self):
        first = self.state(self.book(scores={(1, "Boden", 1): (5, 1, 5), (2, "Boden", 1): (4, 1, 4)}), observed_at=T1)
        changed = self.book(scores={(1, "Boden", 1): (5, 1, 5), (2, "Boden", 1): (5, 1, 5), (1, "Pferd", 1): (2, 1, 2)})
        second = self.state(changed, observed_at=T2, previous=first)
        live = [(r["teilnehmer"][:6], r["code"], r["T"], r["geaendert_am"]) for r in self.results(second)]
        self.assertEqual([x[3] for x in live], [T2, T2, T1])
        self.assertEqual(live[-1][2], 5)  # unverändert, alter Zeitpunkt
        self.assertEqual({(x[1], x[2]) for x in live[:2]}, {("1-1", 5), ("2-1", 2)})

    def test_checksums_are_deterministic_and_independent_of_observation_time(self):
        path = self.book(AK9, scores={(1, "Sprung", 1): (3, 1, 3)}, direct={(3, "Kraft", 1): 2})
        a = self.state(self.book(), path, observed_at=T1)
        b = self.state(self.book(), path, observed_at=T2)
        c = self.state(self.book(), path, observed_at=T2, previous=a)
        sums = [[r["pruefsumme"] for r in pm.publication_rows(s)] for s in (a, b, c)]
        self.assertEqual(sums[0], sums[1])
        self.assertEqual(sums[0], sums[2])
        self.assertEqual({r["geaendert_am"] for r in c["einzelwertungen"]}, {T1})
        self.assertNotEqual(a["beobachtet_am"], b["beobachtet_am"])


class ExcelSafetyTests(Base):
    def test_missing_formula_result_is_rejected(self):
        self.error(Code.FORMELRESULTAT_FEHLT, self.book(scores={(1, "Boden", 1): ("=2+3", 1, "=5*1")}))

    def test_excel_error_value_in_used_cell_is_rejected(self):
        self.error(Code.EXCEL_FEHLERWERT, self.book(scores={(1, "Boden", 1): ("#N/A", 1, None)}))
        self.error(Code.EXCEL_FEHLERWERT, self.book(AK9, direct={(2, "Kraft", 4): "#REF!"}))

    def test_cosmetic_leftovers_outside_used_cells_are_ignored(self):
        state = self.state(self.book(cells={("Noteneingabe", "ZZ300"): "#REF!", ("Anmeldung", "T50"): "=1+1",
                                            ("Testprogramm", "Z90"): "#N/A"}))
        self.assertEqual(state["programme"][0]["programm"], AK5)

    def test_workbook_stays_unchanged(self):
        path = self.book(scores={(1, "Boden", 1): (5, 1, 5)})
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
        self.state(path)
        after = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [path.name])

    def test_missing_mode_label_is_rejected(self):
        self.error(Code.PUBLIC_UNGUELTIG, self.book(cells={("Public", "A7"): "Modus"}))


if __name__ == "__main__":
    unittest.main()
