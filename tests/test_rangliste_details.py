"""Ranglistenansicht: Gliederung Gesamt/Technik/Athletik, Details je Turner (nur synthetische Daten, kein Netzwerk)."""
from __future__ import annotations

import html
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import _support
from _support import AK5, AK5_BEW, AK5_KRAFT, AK9, AK9_BEW, AK9_KRAFT, make_testtag_workbook, testtag_spec

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import app  # noqa: E402
import quelle as q  # noqa: E402
from testtag_public import publication_model as pm  # noqa: E402
from testtag_public.excel_publication import read_program_source  # noqa: E402

# AK 7 (Start Boden → Boden R1, Pferd R2, Ringe R3 mit Kraft 1 vor Ort, Beweglichkeit R4)
AK5_PEOPLE = testtag_spec(AK5)["participants"] + [(5, "Ersonnen", "Mia", 2018, "TV Testdorf", "AK 7", 7, "Reck")]
AK5_SCORES = {
    (1, "Boden", 1): (5, 1.5, 7.5), (1, "Boden", 2): (4, 1, 4), (1, "Pferd", 1): (5, 1, 5),
    (1, "Kraft", 1): (None, 1, 8),            # organisatorisch an den Ringen, fachlich Kraft
    (1, "Beweglichkeit", 1): (None, 1, 0),    # abgeschlossene Nullwertung
    (2, "Pferd", 1): (6, 1, 6),               # Technik 6
    (4, "Sprung", 1): (6, 1, 6),              # Technik 6 → Gleichstand mit Person 2
}
AK9_SCORES = {(1, "Sprung", 1): (3, 1, 3), (1, "Sprung", 2): (4, 1, 4),          # AK 9: Sprung 1 / Sprung 2
              (3, "Sprung", 1): (5, 1, 5), (3, "Sprung", 3): (6, 1, 6)}          # AK 10: Pflicht + Alternative 3
AK9_DIRECT = {(3, "Kraft", 2): 4, (3, "Beweglichkeit", 1): 0}


def klartext(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


class RanglisteDetails(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.stand = cls.build()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def build(cls, rotation_max: int = 8) -> q.Stand:
        d = Path(cls._tmp.name)
        a = make_testtag_workbook(d / f"ak5-{rotation_max}.xlsx", AK5, participants=AK5_PEOPLE, scores=AK5_SCORES,
                                  public={"published_rotation_max": rotation_max})
        b = make_testtag_workbook(d / f"ak9-{rotation_max}.xlsx", AK9, scores=AK9_SCORES, direct=AK9_DIRECT)
        state = pm.build_state([read_program_source(p, wait_seconds=0, sleep=lambda _s: None) for p in (a, b)],
                               observed_at="2026-10-09T08:00:00Z")
        saetze = tuple(q.Datensatz(r["bereich"], r["programm"], r["ak"], r["pruefsumme"], "2026-10-09T08:00:00Z",
                                   r["inhalt"]) for r in pm.publication_rows(state))
        testtag = q.Testtag("t", "synth", "Synthetisch", "live", "2026-10-09T08:00:00Z")
        return q.Stand(testtag, saetze, datetime(2026, 10, 9, 8, tzinfo=timezone.utc))

    def person(self, programm: str, ak: str, vorname: str, art: str = "gesamt", stand=None):
        stand = stand or self.stand
        e = next(e for e in q.rangliste(stand, programm, ak, art) if e["vorname"] == vorname)
        alle = q.live_resultate(stand, programm, ak)
        eigene = [r for r in alle if r["teilnehmer"] == e["teilnehmer"]]
        return e, eigene, q.turner_details(e, eigene, q.ranglisten_struktur(stand, programm, ak), alle)

    # ── Gliederung aus dem Testprogramm ──
    def test_structure_lists_intended_apparatus_lines_and_tests_per_ak(self):
        ak6 = q.ranglisten_struktur(self.stand, AK5, "AK 6")
        ak7 = q.ranglisten_struktur(self.stand, AK5, "AK 7")
        geraete = [g["geraet"] for g in ak7["geraete"]]
        self.assertEqual(geraete, ["Boden", "Pferd", "Ringe", "Sprung", "Barren", "Trampolin", "Reck"])
        self.assertNotIn("Beweglichkeit", geraete)  # Athletik ist kein Technikgerät
        boden = lambda s: [li["anzeige"] for li in s["geraete"][0]["linien"]]  # noqa: E731
        self.assertEqual(boden(ak7), ["Synth Boden vorwärts", "Synth Boden rückwärts"])
        self.assertEqual(boden(ak6), ["Synth Boden vorwärts"])  # Linie 2 für AK 6 nicht vorgesehen
        self.assertEqual([t["test"] for t in ak7["beweglichkeit"]], AK5_BEW)
        self.assertEqual([t["test"] for t in ak7["kraft"]], AK5_KRAFT)
        ak9 = q.ranglisten_struktur(self.stand, AK9, "AK 9")
        self.assertEqual([t["test"] for t in ak9["kraft"]], AK9_KRAFT)
        self.assertEqual([t["test"] for t in ak9["beweglichkeit"]], AK9_BEW)

    # ── Gesamt ──
    def test_gesamt_details_total_per_apparatus_without_athletics(self):
        e, _eigene, d = self.person(AK5, "AK 7", "Lea")
        totals = {g["geraet"]: g["total"] for g in d["geraete"]}
        self.assertEqual(totals, {"Boden": 11.5, "Pferd": 5, "Ringe": None, "Sprung": None, "Barren": None,
                                  "Trampolin": None, "Reck": None})
        self.assertEqual((e["technik"], e["athletik"], e["gesamt"]), (16.5, 8, 24.5))
        self.assertEqual(sum(t for t in totals.values() if t is not None), e["technik"])  # passt zum Total
        karte = app.rang_karte(e, "gesamt", _eigene, d)
        details = karte.split("<div class='tt-details'>", 1)[1]
        # nach dem Öffnen nur drei Zeilen: Total Technik und Total Athletik (aufklappbar, zu), Gesamt
        stufen = details.split("<details class='tt-geraet tt-total'>")
        self.assertEqual([klartext(x.split("</summary>")[0]) for x in stufen[1:]], ["Total Technik 16.5", "Total Athletik 8"])
        self.assertEqual(klartext(stufen[0]), "")
        self.assertNotIn(" open", karte)
        text = klartext(karte)
        # Total Technik → Gerätetotale → Linien mit S und T
        self.assertIn("Total Technik 16.5 Boden 11.5 Synth Boden vorwärts S 5 T 7.5 Synth Boden rückwärts S 4 T 4 "
                      "Pferd 5 Synth Scheren S 5 T 5 Ringe – Synth Schwünge S – T –", text)
        # Total Athletik → Total Beweglichkeit / Total Kraft → Einzeltests (fehlend „–“, Nullwert 0)
        self.assertIn("Total Athletik 8 Total Beweglichkeit 0 Bücke 0 Arm-Rumpf-Winkel rücklings –", text)
        self.assertIn("Seitspagat – Total Kraft 8 Standweitsprung 8 Klettern / Hangeln am Tau –", text)
        self.assertTrue(text.endswith("Liegestütz – Gesamt 24.5"))

    # ── Technik ──
    def test_technik_details_lines_with_s_and_line_points(self):
        e, eigene, d = self.person(AK5, "AK 7", "Lea", "technik")
        boden = d["geraete"][0]
        self.assertEqual([(li["anzeige"], li["S"], li["T"]) for li in boden["linien"]],
                         [("Synth Boden vorwärts", 5, 7.5), ("Synth Boden rückwärts", 4, 4)])  # T = Linienpunkte
        karte = app.rang_karte(e, "technik", eigene, d)
        self.assertEqual(karte.count("<details class='tt-geraet'>"), 7)  # jedes Gerät einzeln aufklappbar
        self.assertNotIn("<details class='tt-geraet' open", karte)
        self.assertIn("Synth Boden vorwärts S 5 T 7.5", klartext(karte))
        self.assertIn("Ringe – Synth Schwünge S – T –", klartext(karte))
        self.assertNotRegex(klartext(karte), r"(?<![\d.])1\.5(?![\d])")  # TAL wird nie angezeigt

    # ── Athletik ──
    def test_athletik_details_per_test_zero_versus_missing(self):
        e, eigene, d = self.person(AK5, "AK 7", "Lea", "athletik")
        self.assertEqual([t["punkte"] for t in d["beweglichkeit"]], [0] + [None] * 6)
        self.assertEqual([t["punkte"] for t in d["kraft"]], [8] + [None] * 4)
        text = klartext(app.rang_karte(e, "athletik", eigene, d))
        self.assertIn("Bücke 0 Arm-Rumpf-Winkel rücklings –", text)  # 0 = abgeschlossene Nullwertung
        self.assertIn("Total Beweglichkeit 0", text)
        self.assertIn("Standweitsprung 8 Klettern / Hangeln am Tau –", text)
        self.assertIn("Total Kraft 8 Total Athletik 8", text)

    def test_person_without_results_shows_dashes_not_zero(self):
        for art in q.RANGLISTEN:
            e, eigene, d = self.person(AK5, "AK 7", "Mia", art)
            self.assertEqual(eigene, [])
            text = klartext(app.rang_karte(e, art, eigene, d))
            self.assertNotRegex(text, r"(?<![\d.])0(?![\d.])", art)  # keine scheinbare Nullwertung
            self.assertIn("–", text)

    # ── Rangberechnung unverändert vom Publisher ──
    def test_ties_and_order_come_from_publisher(self):
        for art, erwartet in (("technik", [1, 2, 2, 4]), ("gesamt", [1, 2, 2, 4])):
            self.assertEqual([e["rang"] for e in q.rangliste(self.stand, AK5, "AK 7", art)], erwartet, art)
        self.assertEqual([e["punkte"] for e in q.rangliste(self.stand, AK5, "AK 7", "technik")], [16.5, 6, 6, 0])

    def test_rotation_limit_applies_to_details_and_totals(self):
        stand = self.build(rotation_max=2)  # Kraft (R3) und Beweglichkeit (R4) noch nicht freigegeben
        e, eigene, d = self.person(AK5, "AK 7", "Lea", "athletik", stand=stand)
        self.assertEqual((e["technik"], e["athletik"]), (16.5, 0))
        self.assertTrue(all(t["punkte"] is None for b in ("beweglichkeit", "kraft") for t in d[b]))
        text = klartext(app.rang_karte(e, "athletik", eigene, d))
        self.assertIn("Total Athletik –", text)
        self.assertNotIn(" 8 ", f" {text} ")

    # ── AK9–AK12 ──
    def test_ak9_sprung_1_and_2_and_external_athletics(self):
        e, eigene, d = self.person(AK9, "AK 9", "Jan", "technik")
        sprung = next(g for g in d["geraete"] if g["geraet"] == "Sprung")
        self.assertEqual([(li["anzeige"], li["T"]) for li in sprung["linien"]], [("Sprung 1", 3), ("Sprung 2", 4)])
        self.assertEqual(sprung["total"], 7)
        e, eigene, d = self.person(AK9, "AK 10", "Noah", "athletik")
        self.assertEqual([t["punkte"] for t in d["kraft"]], [None, 4] + [None] * 12)
        self.assertEqual([t["punkte"] for t in d["beweglichkeit"]], [0] + [None] * 9)
        self.assertEqual(len(d["kraft"]), len(AK9_KRAFT))
        text = klartext(app.rang_karte(e, "athletik", eigene, d))
        self.assertNotIn("Rotation", text)  # ausgelagerte Tests: keine erfundenen Stationen

    def test_sprung_alternative_shows_chosen_line_only(self):
        _e, _eigene, d = self.person(AK9, "AK 10", "Noah", "technik")
        sprung = next(g for g in d["geraete"] if g["geraet"] == "Sprung")
        self.assertEqual([(li["anzeige"], li["T"]) for li in sprung["linien"]], [("Überschlag vw", 5), ("Yurchenko", 6)])
        e, eigene, d = self.person(AK9, "AK 11", "Elias", "technik")
        sprung = next(g for g in d["geraete"] if g["geraet"] == "Sprung")
        self.assertEqual([(li["anzeige"], li["alternative"]) for li in sprung["linien"]],
                         [("Überschlag vw", False), ("Tsukahara", True), ("Yurchenko", True)])
        self.assertIn("Tsukahara (Alternative) S – T –", klartext(app.rang_karte(e, "technik", eigene, d)))

    # ── Schutz vor widersprüchlicher Anzeige ──
    def test_inconsistent_totals_hide_breakdown(self):
        e, eigene, _d = self.person(AK5, "AK 7", "Lea")
        falsch = dict(e, technik=99)
        self.assertIsNone(q.turner_details(falsch, eigene, None, eigene))
        karte = klartext(app.rang_karte(falsch, "gesamt", eigene, None))
        self.assertIn("Details nicht verfügbar.", karte)

    def test_older_publication_without_structure_falls_back_to_published_results(self):
        e, eigene, _d = self.person(AK5, "AK 7", "Lea")
        alle = q.live_resultate(self.stand, AK5, "AK 7")
        d = q.turner_details(e, eigene, None, alle)
        self.assertEqual([g["geraet"] for g in d["geraete"]], ["Boden", "Pferd", "Sprung"])  # nur Veröffentlichtes
        self.assertEqual([t["test"] for t in d["kraft"]], ["Standweitsprung"])

    def test_compact_list_row_only_rank_name_club_total(self):
        e, eigene, d = self.person(AK5, "AK 7", "Lea")
        karte = app.rang_karte(e, "gesamt", eigene, d)
        kopf = klartext(karte.split("</summary>")[0])
        self.assertEqual(kopf, "1 Lea Synthaler TV Testdorf 24.5 ▾")
        for verboten in ("ergebnis_id", "teilnehmer", "sprung-", "TAL", e["teilnehmer"][:16]):
            self.assertNotIn(verboten, karte)


if __name__ == "__main__":
    unittest.main()
