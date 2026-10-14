"""Streamlit-App mit `streamlit.testing.v1.AppTest` und synthetischer öffentlicher Schnittstelle (kein Netzwerk)."""
from __future__ import annotations

import html
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _support
from _support import (AK5, AK9, ARCHIV_SLUG, LIVE_ID, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SLUG, TT_TITLE,
                      FakeOeffentlich, synthetic_publication)

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import quelle as q  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(_ROOT / "app.py")
VERBOTEN = [TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY, "sb_secret_", "Failing row", "Traceback", "example-ref",
            "HTTP_503", "PROBEINHALT", "ergebnis_id", "excel_id", "TAL", "sprung-1", "sprung-2", "Bearer"]
NAV = ["Live", "Ranglisten", "Startliste", "Zeitplan"]
_BILD = re.compile(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+")


def klartext(fragment: str) -> str:
    """Sichtbarer Text eines HTML-Fragments (Tags entfernt, Entitäten aufgelöst, Leerraum normalisiert)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data = synthetic_publication(Path(cls._tmp.name))
        cls.keys = {p["teilnehmer"] for groups in cls.data["state"]["startlisten"].values()
                    for g in groups for p in g["teilnehmer"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        import streamlit as st
        st.cache_data.clear()  # gemeinsamer Cache gilt prozessweit: jeder Test beginnt leer
        q.AUSFALL.zuruecksetzen()  # Ausfallbremse gilt prozessweit
        self.versatz = 0.0  # simulierte verstrichene Zeit für die Cache-Zeitfenster der App
        echte_zeit = time.time
        uhr = mock.patch("time.time", new=lambda: echte_zeit() + self.versatz)
        uhr.start()
        self.addCleanup(uhr.stop)
        # Jeder Test beginnt am Anfang eines Kontrollfensters (300 s): sonst lädt eine zufällig überschrittene
        # Fenstergrenze den Stand zusätzlich und Zählungen der Abrufe schwanken.
        self.ausrichten(300)
        self.api =FakeOeffentlich(self.data["testtage"], self.data["rows"], self.data["snapshots"])
        patcher = mock.patch.object(q, "https_transport", self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, t: str | None = None, secrets: dict | None = None, ansicht: str | None = None,
            live_url: str | None = None, statische_bereiche=None, adresse: dict | None = None) -> AppTest:
        at = AppTest.from_file(APP, default_timeout=60)
        for key, value in (secrets or {"supabase_url": "https://example-ref.supabase.co",
                                       "supabase_publishable_key": TEST_PUBLISHABLE_KEY}).items():
            at.secrets[key] = value
        if live_url is not None:
            at.secrets["live_url"] = live_url
        if statische_bereiche is not None:
            at.secrets["statische_bereiche"] = statische_bereiche
        if t:
            at.query_params["t"] = t
        if ansicht:
            at.query_params["ansicht"] = ansicht
        for key, value in (adresse or {}).items():
            at.query_params[key] = value
        at.run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        return at

    # ── Hilfen zum Auslesen der Darstellung ──
    @staticmethod
    def roh(at: AppTest) -> str:
        parts = []
        for kind in ("title", "subheader", "markdown", "caption", "info", "warning", "error", "success"):
            parts += [str(e.value) for e in getattr(at, kind)]
        parts += [str(b.label) for b in at.button]
        parts += [str(o) for s in at.selectbox for o in s.options]
        return _BILD.sub("data:bild", "\n".join(parts))  # eingebettetes Logo nicht als Text prüfen

    def text(self, at: AppTest) -> str:
        return klartext(self.roh(at))

    def assert_clean(self, at: AppTest) -> None:
        for text in (self.roh(at), self.text(at)):
            for marker in VERBOTEN:
                self.assertNotIn(marker, text)
            for key in self.keys:
                self.assertNotIn(key[:16], text)

    @staticmethod
    def block(at: AppTest, klasse: str) -> str:
        for m in at.markdown:
            if f"'{klasse}'" in str(m.value) or f" {klasse}'" in str(m.value):
                return str(m.value)
        return ""

    def cards(self, at: AppTest) -> list[str]:
        return [klartext(k) for k in self.block(at, "tt-liste tt-live").split("<div class='tt-karte'>")[1:]]

    def raenge(self, at: AppTest) -> list[tuple[str, str]]:
        """(Rang, Punkte) je Ranglistenkarte in der angezeigten Reihenfolge."""
        out = []
        for karte in self.block(at, "tt-liste tt-rangliste").split("tt-rangkarte")[1:]:
            rang = re.search(r"title='Rang ([^']*)'", karte).group(1)
            punkte = re.search(r"<div class='tt-note'>([^<]*)</div>", karte).group(1)
            out.append((html.unescape(rang), html.unescape(punkte)))
        return out

    def kopf(self, at: AppTest) -> str:
        return self.block(at, "tt-kopf")

    def go(self, at: AppTest, bereich: str) -> AppTest:
        at.radio(key="bereich").set_value(bereich).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        return at

    # ── Kopfbereich, Status, Übersicht und Navigation ──
    def test_overview_groups_cards_and_navigation(self):
        at = self.app()
        self.assertIn("<h1>Testtage</h1>", self.kopf(at))
        self.assertEqual([s.value for s in at.subheader], ["Live", "Demnächst", "Archiv"])
        labels = [b.label for b in at.button]
        self.assertIn(TT_TITLE, labels)
        self.assertIn("Synthetischer Testtag 2025 – Archivhausen", labels)
        self.assertNotIn("Nicht öffentlich", labels)
        text = self.text(at)
        self.assertNotIn("Adresse ?t=", text)
        roh = self.roh(at)
        for klasse in ("tt-badge live", "tt-badge demnaechst", "tt-badge archiv"):
            self.assertIn(klasse, roh)
        at.button(key=f"oeffnen|{TT_SLUG}").click().run()
        self.assertEqual(at.query_params["t"], [TT_SLUG])
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assert_clean(at)

    def test_uniform_header_logo_status_and_footer_in_every_view(self):
        for kennung, badge in ((None, None), (TT_SLUG, "Live"),
                               (ARCHIV_SLUG, "Archiv"),
                               ("synth-testtag-2027", "Demnächst")):
            with self.subTest(kennung=kennung):
                at = self.app(kennung)
                koepfe = [m for m in at.markdown if "class='tt-kopf'" in str(m.value)]
                self.assertEqual(len(koepfe), 1)
                kopf = str(koepfe[0].value)
                self.assertIn("<img src='data:image/png;base64,", kopf)  # lokales Logo, kein Hotlink
                self.assertNotIn("http", _BILD.sub("", kopf))
                if badge:
                    self.assertIn(badge, klartext(kopf))
                self.assertIn("Turnsport Aargau · Testtag", self.text(at))  # Fuss
                self.assertNotIn("AMM", self.text(at))
                self.assert_clean(at)

    def test_slug_opens_correct_testtag_and_uuid_is_redirected_to_slug(self):
        at = self.app(TT_SLUG)
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assertIn("tt-badge live", self.kopf(at))
        at = self.app(LIVE_ID)
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assertEqual(at.query_params["t"], [TT_SLUG])

    def test_unknown_or_invalid_testtag_shows_neutral_message(self):
        for kennung in ("gibt-es-nicht", "synth-geheim", "x&select=*"):
            with self.subTest(kennung=kennung):
                at = self.app(kennung)
                self.assertIn("Dieser Testtag ist nicht verfügbar.", [i.value for i in at.info])
                self.assertEqual(self.cards(at), [])
                self.assertEqual(self.kopf(at), "")
                self.assert_clean(at)

    def test_four_areas_are_reachable_with_navigation_pills(self):
        at = self.app(TT_SLUG)
        radio = at.radio(key="bereich")
        self.assertEqual(radio.options, NAV)
        self.assertIn("COLLAPSED", str(radio.label_visibility).upper())
        self.assertIn(".st-key-bereich [role=\"radiogroup\"]", self.roh(at))  # Pillen-Stil eng begrenzt
        for bereich in q.BEREICHE:
            self.go(at, bereich)
            self.assert_clean(at)

    # ── Adresse mit Bereich und statische Live-Ansicht ──
    def test_view_parameter_opens_area_and_area_change_is_written_to_address(self):
        for wert, bereich in (("rangliste", q.RANGLISTE), ("startliste", q.STARTLISTE), ("zeitplan", q.ZEITPLAN),
                              ("live", q.LIVE), ("unbekannt", q.LIVE), ("", q.LIVE)):
            with self.subTest(ansicht=wert):
                at = self.app(TT_SLUG, ansicht=wert)
                self.assertEqual(at.radio(key="bereich").value, bereich)
                self.assertNotIn("live_url", self.roh(at))
                self.assert_clean(at)
        at = self.app(TT_SLUG, ansicht="rangliste")
        self.assertTrue(self.raenge(at))
        self.go(at, q.ZEITPLAN)
        self.assertEqual(at.query_params["ansicht"], ["zeitplan"])
        self.assertEqual(at.query_params["t"], [TT_SLUG])
        self.assertEqual(at.radio(key="bereich").value, q.ZEITPLAN)
        at = self.app(TT_SLUG)  # bestehende ?t=-Links: unverändert Live, keine Ergänzung der Adresse
        self.assertEqual(at.radio(key="bereich").value, q.LIVE)
        self.assertNotIn("ansicht", at.query_params)
        self.assertTrue(self.cards(at))

    def test_static_live_link_replaces_live_area_without_loading_data(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        at = self.app(TT_SLUG, live_url=basis)
        roh = self.roh(at)
        self.assertIn(f"href='{basis}?t={TT_SLUG}'", roh)
        self.assertIn("Live-Resultate öffnen", self.text(at))
        radio = at.radio(key="bereich")
        self.assertEqual(radio.options, NAV[1:])
        self.assertIsNone(radio.value)
        self.assertEqual(self.cards(at), [])
        self.assertEqual(sum("/veroeffentlichung?" in c["url"] for c in self.api.calls), 0)  # nur der Testtag
        self.assert_clean(at)
        self.go(at, q.RANGLISTE)
        self.assertTrue(self.raenge(at))
        self.assertEqual(at.query_params["ansicht"], ["rangliste"])
        self.assertIn(f"href='{basis}?t={TT_SLUG}'", self.roh(at))  # Live-Pille bleibt in der Navigation
        for wert in ("live", "unbekannt"):
            with self.subTest(ansicht=wert):
                at = self.app(TT_SLUG, ansicht=wert, live_url=basis)
                self.assertIsNone(at.radio(key="bereich").value)
                self.assertEqual(self.cards(at), [])
        at = self.app(TT_SLUG, ansicht="startliste", live_url=basis.rstrip("/"))
        self.assertEqual(at.radio(key="bereich").value, q.STARTLISTE)
        self.assertIn(f"href='{basis}?t={TT_SLUG}'", self.roh(at))

    def test_static_ranking_link_replaces_ranking_area_without_loading_data(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        live, rangliste = f"href='{basis}?t={TT_SLUG}'", f"href='{basis}?t={TT_SLUG}&amp;ansicht=rangliste'"
        at = self.app(TT_SLUG, ansicht="rangliste", live_url=basis, statische_bereiche=["rangliste"])
        roh = self.roh(at)
        self.assertIn(live, roh)
        self.assertIn(rangliste, roh)
        self.assertIn("Ranglisten öffnen", self.text(at))
        radio = at.radio(key="bereich")
        self.assertEqual(radio.options, NAV[2:])
        self.assertIsNone(radio.value)
        self.assertEqual(self.raenge(at), [])
        self.assertEqual(sum("/veroeffentlichung?" in c["url"] for c in self.api.calls), 0)  # nur der Testtag
        self.assert_clean(at)
        at = self.app(TT_SLUG, live_url=basis, statische_bereiche=["rangliste"])
        self.assertIn("Live-Resultate öffnen", self.text(at))
        self.go(at, q.STARTLISTE)
        self.assertEqual(at.query_params["ansicht"], ["startliste"])
        self.assertIn(rangliste, self.roh(at))  # beide Pillen bleiben in der Navigation
        at = self.app(ARCHIV_SLUG, ansicht="rangliste", live_url=basis, statische_bereiche=["rangliste"])
        self.assertEqual(at.radio(key="bereich").value, q.RANGLISTE)  # Archiv bleibt vollständig hier
        self.assertTrue(self.raenge(at))
        self.assertNotIn(f"{basis}?t=", self.roh(at))  # höchstens die Übersicht
        for live_url, schalter in ((None, ["rangliste"]), (basis, "rangliste"), (basis, []), (basis, ["unbekannt"])):
            with self.subTest(live_url=live_url, schalter=schalter):  # ohne live_url oder ohne true: Rangliste hier
                at = self.app(TT_SLUG, ansicht="rangliste", live_url=live_url, statische_bereiche=schalter)
                self.assertEqual(at.radio(key="bereich").value, q.RANGLISTE)
                self.assertTrue(self.raenge(at))
                self.assertNotIn("ansicht=rangliste'", self.roh(at))

    def test_all_areas_static_streamlit_shows_only_links_and_loads_no_data(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        alle = ["rangliste", "startliste", "zeitplan"]
        for ansicht, knopf in (("startliste", "Startliste öffnen"), ("zeitplan", "Zeitplan öffnen"),
                               (None, "Live-Resultate öffnen")):
            with self.subTest(ansicht=ansicht):
                self.api.calls.clear()
                at = self.app(TT_SLUG, ansicht=ansicht, live_url=basis, statische_bereiche=alle)
                roh = self.roh(at)
                for ziel in ("", "&amp;ansicht=rangliste", "&amp;ansicht=startliste", "&amp;ansicht=zeitplan"):
                    self.assertIn(f"href='{basis}?t={TT_SLUG}{ziel}'", roh)
                self.assertIn(knopf, self.text(at))
                self.assertEqual(len(at.radio), 0)  # kein Bereich mehr in Streamlit
                self.assertEqual(sum("/veroeffentlichung?" in c["url"] for c in self.api.calls), 0)
                self.assertNotIn("tt-startliste'>", roh)
                self.assert_clean(at)
        at = self.app(TT_SLUG, ansicht="zeitplan", live_url=basis, statische_bereiche=["rangliste", "startliste"])
        self.assertEqual(at.radio(key="bereich").value, q.ZEITPLAN)  # nur Zeitplan bleibt hier
        at = self.app(ARCHIV_SLUG, ansicht="startliste", live_url=basis, statische_bereiche=alle)
        self.assertEqual(at.radio(key="bereich").value, q.STARTLISTE)  # Archiv bleibt vollständig hier
        self.assertNotIn(f"{basis}?t=", self.roh(at))  # höchstens die Übersicht

    def test_back_link_leads_to_static_overview_when_configured(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        for slug in (TT_SLUG, ARCHIV_SLUG):
            with self.subTest(slug=slug):
                at = self.app(slug, live_url=basis)
                self.assertIn(f"<a class='tt-pille tt-alle' href='{basis}' target='_top'>← Alle Testtage</a>", self.roh(at))
                self.assertFalse([b for b in at.button if b.key == "zurueck"])
        at = self.app(TT_SLUG)  # ohne live_url: bisheriger Knopf zur Streamlit-Übersicht
        self.assertTrue([b for b in at.button if b.key == "zurueck"])
        self.assertNotIn("tt-alle'", self.roh(at))

    def test_static_live_link_in_overview_but_archive_stays_in_streamlit(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        at = self.app(live_url=basis)
        roh = self.roh(at)
        self.assertIn(f"href='{basis}?t={TT_SLUG}'", roh)
        self.assertNotIn(f"?t={ARCHIV_SLUG}'", roh)
        at = self.app(ARCHIV_SLUG, live_url=basis)
        self.assertEqual(at.radio(key="bereich").options, NAV)
        self.assertNotIn(f"{basis}?t=", self.roh(at))  # höchstens die Übersicht
        self.assertTrue(self.cards(at))

    def test_invalid_static_live_address_keeps_live_in_streamlit(self):
        for wert in ("http://stv-sev.github.io/testtag-public/", "https://x.example/?t=a", "javascript:alert(1)",
                     "", "https://"):
            with self.subTest(live_url=wert):
                at = self.app(TT_SLUG, live_url=wert)
                self.assertEqual(at.radio(key="bereich").options, NAV)
                self.assertTrue(self.cards(at))
                self.assertNotIn("class='tt-verweis'", self.roh(at))

    def test_hidden_or_unknown_testtag_with_static_live_shows_no_link_and_no_data(self):
        basis = "https://stv-sev.github.io/testtag-public/"
        at = self.app(TT_SLUG, ansicht="rangliste", live_url=basis)
        self.assertTrue(self.raenge(at))
        for t in self.api.testtage:
            if t["slug"] == TT_SLUG:
                t["ist_oeffentlich"] = False
        self.versatz += 10
        at.run()
        self.assertIn("Dieser Testtag ist nicht verfügbar.", [i.value for i in at.info])
        self.assertEqual(self.raenge(at), [])
        self.assertNotIn(f"{basis}?t=", self.roh(at))  # höchstens die Übersicht
        self.assert_clean(at)
        at = self.app("gibt-es-nicht", ansicht="rangliste", live_url=basis)
        self.assertNotIn(f"{basis}?t=", self.roh(at))  # höchstens die Übersicht
        self.assertEqual(self.kopf(at), "")

    # ── Live ──
    def test_live_newest_first_both_programs_and_filters(self):
        at = self.app(TT_SLUG)
        self.assertEqual(at.selectbox(key="live_programm").options, ["Alle", "AK5–AK9+", "AK9–AK12"])
        cards = self.cards(at)
        times = [re.search(r"\b(\d\d:\d\d:\d\d)\b", c).group(1) for c in cards]
        self.assertEqual(times[0], "11:30:00")  # neueste Änderung (09:30 UTC) zuerst
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertTrue(any("AK5–AK9+" in c for c in cards) and any("AK9–AK12" in c for c in cards))
        self.assertIn(f"{len(cards)} Resultate, neueste zuerst", self.text(at))
        at.selectbox(key="live_programm").set_value(AK9).run()
        self.assertTrue(all("AK9–AK12" in c for c in self.cards(at)))
        self.assertEqual(at.selectbox(key="live_ak").options, ["Alle", "AK 9", "AK 10", "AK 11"])
        at.selectbox(key="live_ak").set_value("AK 9").run()
        ak9 = self.cards(at)
        self.assertTrue(ak9 and all(" · AK 9 " in c for c in ak9))
        self.assertTrue(any(re.search(r"\bSprung 3 .* Sprung 1 S 3 T 3$", c) for c in ak9), ak9)
        self.assertTrue(any(re.search(r"\bSprung 4 .* Sprung 2 S 4 T 4$", c) for c in ak9), ak9)
        self.assertNotIn("Tsukahara", self.text(at))
        self.assert_clean(at)

    def test_live_cards_show_s_and_t_for_technik_and_points_for_athletics(self):
        at = self.app(TT_SLUG)
        cards = self.cards(at)
        self.assertTrue(any(c.endswith("Synth Boden vorwärts S 5 T 7.5") and "Boden 7.5" in c for c in cards))
        self.assertTrue(any(c.endswith("Standweitsprung Punkte 8") and "Kraft 8" in c for c in cards))
        text = " ".join(cards)
        self.assertNotIn("1.5", text)  # TAL
        self.assertNotIn("AK 5 ", text)  # Tombstone-AK erscheint nicht
        self.assertNotIn("AK 5", at.selectbox(key="live_ak").options)
        roh = self.block(at, "tt-liste tt-live")
        self.assertEqual(roh.count("<div class='tt-karte'>"), len(cards))
        self.assertIn("tt-chip tt-end", roh)

    # ── Ranglisten ──
    def test_ranking_keeps_publisher_ranks_and_marks_missing_results(self):
        at = self.go(self.app(TT_SLUG), q.RANGLISTE)
        self.assertEqual(at.selectbox(key="rl_programm").value, AK5)
        self.assertEqual(at.selectbox(key="rl_ak").options, ["AK 6", "AK 7"])
        self.assertEqual(at.radio(key="rl_art").value, "gesamt")
        at.selectbox(key="rl_ak").set_value("AK 7").run()
        self.assertEqual(self.raenge(at), [("1", "15.5"), ("2", "6"), ("2", "6"), ("4", "–")])
        rl = self.block(at, "tt-liste tt-rangliste")
        self.assertIn("background:#FFD700", rl)  # Rang 1 golden
        self.assertEqual(rl.count("background:#FFD700"), 1)  # keine Medaille ohne Resultat
        karten = rl.split("tt-rangkarte'>")[1:]
        kompakt = [klartext(k.split("</summary>")[0]) for k in karten]  # Liste: Rang, Turner, Verein, Total
        self.assertEqual(kompakt[0], "1 Lea Synthaler TV Testdorf 15.5 ▾")
        self.assertNotIn("tt-chip", rl)
        erste = klartext(karten[0].split("</summary>", 1)[1])  # Details erst nach Antippen
        self.assertIn("Total Technik 7.5 Boden 7.5 Synth Boden vorwärts S 5 T 7.5", erste)  # gestuft
        self.assertIn("Total Athletik 8 Total Beweglichkeit – Bücke –", erste)  # Beweglichkeit vor Kraft
        self.assertIn("Seitspagat – Total Kraft 8 Standweitsprung 8", erste)
        self.assertIn("Liegestütz – Gesamt 15.5", erste)
        ohne = klartext(karten[-1].split("</summary>", 1)[1])  # Person ohne Resultat: keine scheinbaren Nullwerte
        self.assertTrue(ohne.startswith("Total Technik – Boden –"), ohne)
        self.assertIn("Total Athletik – Total Beweglichkeit –", ohne)
        self.assertIn("Seitspagat – Total Kraft –", ohne)
        self.assertIn("Liegestütz – Gesamt –", ohne)
        at.radio(key="rl_art").set_value("technik").run()
        rl = self.block(at, "tt-liste tt-rangliste")
        self.assertIn("Boden 7.5 Synth Boden vorwärts S 5 T 7.5", klartext(rl))  # Gerät aufklappbar, S/T
        self.assertNotIn("<details class='tt-geraet' open", rl)
        at.radio(key="rl_art").set_value("athletik").run()
        self.assertEqual([p for _r, p in self.raenge(at)], ["8", "–", "–", "–"])
        self.assertIn("Alle Rotationen", self.text(at))  # Hinweis des Publishers
        self.assertEqual(len(at.dataframe), 0)
        self.assert_clean(at)

    def test_rotation_view_falls_back_to_technik_ranking_with_hint(self):
        """Rückfall: „Gerät“ der statischen Seite gibt es hier nicht – Hinweis und Technik-Rangliste."""
        at = self.app(TT_SLUG, ansicht="rangliste", adresse={"p": AK5, "art": "technik", "sicht": "rotation",
                                                             "g": "Boden", "r": "3"})
        self.assertEqual(at.radio(key="bereich").value, q.RANGLISTE)
        self.assertEqual(at.radio(key="rl_art").value, "technik")
        hinweis = [i.value for i in at.info]
        self.assertEqual(len(hinweis), 1, hinweis)
        self.assertEqual(hinweis[0], "«Gerät» gibt es hier nicht, angezeigt wird die Technik-Rangliste.")
        self.assertTrue(self.raenge(at))  # normale Technik-Rangliste mit Rängen
        self.assertFalse({"art", "sicht", "g", "r"} & set(at.query_params))  # einmal übernommen, Ebene frei wählbar
        self.versatz += 5
        at.run()  # Takt: Hinweis und Technik bleiben
        self.assertEqual(at.radio(key="rl_art").value, "technik")
        self.assertEqual([i.value for i in at.info], hinweis)
        at.radio(key="rl_art").set_value("gesamt").run()  # Ebene frei wählbar, Hinweis erledigt
        self.assertEqual(at.radio(key="rl_art").value, "gesamt")
        self.assertEqual([i.value for i in at.info], [])
        at.radio(key="rl_art").set_value("technik").run()
        self.assertEqual([i.value for i in at.info], [])
        self.assert_clean(at)
        for adresse, ebene in (({"art": "athletik"}, "athletik"), ({"art": "athletik", "sicht": "rotation"}, "athletik"),
                               ({"art": "unbekannt", "sicht": "rotation"}, "gesamt")):
            with self.subTest(adresse=adresse):
                at = self.app(TT_SLUG, ansicht="rangliste", adresse=adresse)
                self.assertEqual(at.radio(key="rl_art").value, ebene)
                self.assertEqual([i.value for i in at.info], [])
        basis = "https://stv-sev.github.io/testtag-public/"  # Rangliste statisch: Adresse bleibt für die Seite
        at = self.app(TT_SLUG, ansicht="rangliste", live_url=basis, statische_bereiche=["rangliste"],
                      adresse={"art": "technik", "sicht": "rotation"})
        self.assertEqual(at.query_params["sicht"], ["rotation"])
        self.assertEqual([i.value for i in at.info], [])
        # neue Adresse &ansicht=geraet im Rückfall: ebenfalls Technik-Rangliste mit Hinweis, Adresse auf Ranglisten
        at = self.app(TT_SLUG, ansicht="geraet", adresse={"g": "Boden", "r": "1"})
        self.assertEqual(at.radio(key="bereich").value, q.RANGLISTE)
        self.assertEqual(at.radio(key="rl_art").value, "technik")
        self.assertEqual([i.value for i in at.info], hinweis)
        self.assertEqual(at.query_params["ansicht"], ["rangliste"])
        self.assertFalse({"g", "r"} & set(at.query_params))
        self.assertTrue(self.raenge(at))

    def test_geraet_pill_follows_static_rankings(self):
        """„Gerät“ gibt es nur statisch: Pille nach Live, sobald die Ranglisten statisch sind; Verweis ohne Datenabruf."""
        basis = "https://stv-sev.github.io/testtag-public/"
        alle = ["rangliste", "startliste", "zeitplan"]
        at = self.app(TT_SLUG, ansicht="geraet", live_url=basis, statische_bereiche=alle)
        roh = self.roh(at)
        reihenfolge = [roh.index(f"href='{basis}?t={TT_SLUG}{z}'") for z in
                       ("", "&amp;ansicht=geraet", "&amp;ansicht=rangliste", "&amp;ansicht=startliste",
                        "&amp;ansicht=zeitplan")]
        self.assertEqual(reihenfolge, sorted(reihenfolge))  # Live · Gerät · Ranglisten · Startliste · Zeitplan
        self.assertIn("Gerät", self.text(at))
        self.assertIn("Gerät öffnen", self.text(at))
        self.assertEqual(sum("/veroeffentlichung?" in c["url"] for c in self.api.calls), 0)
        self.assert_clean(at)
        for schalter in (["startliste", "zeitplan"], []):  # Ranglisten in Streamlit: keine Pille „Gerät“
            with self.subTest(schalter=schalter):
                at = self.app(TT_SLUG, live_url=basis, statische_bereiche=schalter)
                self.assertNotIn("ansicht=geraet", self.roh(at))
                self.assertNotIn("Gerät", self.text(at))

    # ── Startliste und Zeitplan ──
    def test_startliste_grouped_by_program_and_apparatus_with_five_columns(self):
        at = self.go(self.app(TT_SLUG), q.STARTLISTE)
        roh = self.roh(at)
        self.assertEqual(re.findall(r"<div class='tt-programm'>([^<]+)</div>", roh), ["AK5–AK9+", "AK9–AK12"])
        gruppen = re.findall(r"Startgerät / Riege: ([^<]+)</div>", roh)
        self.assertEqual(gruppen[:4], ["Boden", "Pferd", "Beweglichkeit", "Reck"])
        self.assertNotIn("Ringe", gruppen)  # leere Gruppe
        for kopfzeile in re.findall(r"<thead>(.*?)</thead>", roh):
            self.assertEqual(re.findall(r"<th>([^<]+)</th>", kopfzeile), ["Pos.", "Turner", "Jg.", "AK", "Verein"])
        self.assertEqual(len(at.dataframe), 0)
        self.assert_clean(at)

    def test_startliste_combined_turner_name_keeps_all_information(self):
        at = self.go(self.app(TT_SLUG), q.STARTLISTE)
        zeilen = [[klartext(z) for z in re.findall(r"<td[^>]*>(.*?)</td>", tr)]
                  for tr in re.findall(r"<tr><td class='tt-pos'>.*?</tr>", self.roh(at))]
        erwartet = [[str(p["position"]), f"{p['vorname']} {p['name']}", str(p["jahrgang"]), p["ak"], p["verein"]]
                    for programm in (AK5, AK9) for g in self.data["state"]["startlisten"][programm]
                    for p in g["teilnehmer"]]
        self.assertEqual(zeilen, erwartet)
        self.assertEqual([z[0] for z in zeilen[1:3]], ["1", "2"])  # Pferd: Positionen in Publisher-Reihenfolge

    def test_zeitplan_is_one_day_in_publisher_order_without_table(self):
        at = self.go(self.app(TT_SLUG), q.ZEITPLAN)
        block = self.block(at, "tt-tabelle tt-zeitplan")
        self.assertEqual(re.findall(r"<div class='tt-titel'>([^<]+)</div>", block), ["AK5–AK9+", "AK9–AK12"])
        zeilen = [klartext(z) for z in re.findall(r"<div class='tt-zeile'>(.*?)</span></div>", block)]
        self.assertEqual(zeilen, ["07:30 Hallenöffnung AK5–AK9+", "08:05–12:30 Wettkampf",
                                  "13:55 Besammlung und Einmarsch AK9–AK12", "14:00 – 16:00 Wettkampf"])
        self.assertNotIn("<table", block)

    # ── Letzter gültiger Stand, Fehler, Aktualisierung ──
    def test_failed_reload_keeps_last_valid_state(self):
        at = self.app(TT_SLUG)
        before = self.cards(at)
        self.assertTrue(before)
        for fehler in ("http", "pruefsumme"):
            with self.subTest(fehler=fehler):
                if fehler == "http":
                    self.api.status = 503
                else:
                    self.api.status = None
                    live = next(t for t in self.api.testtage if t["id"] == LIVE_ID)
                    live["stand_am"] = "2026-10-09T09:20:00+00:00"  # neue Publikation, Zeilen beschädigt

                    def kaputt(rows):
                        for row in rows:
                            if row["bereich"] == "resultate_live" and row["inhalt"].get("aktiv") is not False:
                                row["inhalt"]["gruppen"][0]["resultate"] = []
                        return rows
                    self.api.manipulieren = kaputt
                self.versatz += 3
                at.run()
                self.assertEqual(self.cards(at), before)
                warnings = [w.value for w in at.warning]
                self.assertTrue(any(w.startswith("Verbindung unterbrochen, angezeigt wird der Stand von ")
                                    for w in warnings), warnings)
                self.assert_clean(at)
        self.api.status, self.api.manipulieren = None, None
        self.versatz += 3
        at.run()
        self.assertEqual([w.value for w in at.warning], [])

    def ausrichten(self, sekunden: int) -> None:
        """Simulierte Zeit an den Anfang eines Cache-Zeitfensters legen (keine zufällige Fenstergrenze im Test)."""
        jetzt = time.time()
        self.versatz += sekunden - (jetzt % sekunden) + 0.05

    def _zeilen_abrufe(self) -> int:
        return sum("/veroeffentlichung?" in c["url"] for c in self.api.calls)

    def test_live_reloads_rows_only_when_stand_am_changes(self):
        at = self.app(TT_SLUG)
        geladen = self._zeilen_abrufe()
        self.assertEqual(geladen, 1)
        for _ in range(3):  # Takte ohne neue Publikation: nur die kleine Testtag-Abfrage
            self.versatz += 5
            at.run()
        self.assertEqual(self._zeilen_abrufe(), geladen)
        self.assertIn("Stand ", self.text(at))
        live = next(t for t in self.api.testtage if t["id"] == LIVE_ID)
        live["stand_am"] = "2026-10-09T09:15:00+00:00"  # Publisher setzt stand_am nach allen Zeilen
        self.versatz += 5
        at.run()
        self.assertEqual(self._zeilen_abrufe(), geladen + 1)
        self.versatz += 5
        at.run()
        self.assertEqual(self._zeilen_abrufe(), geladen + 1)
        self.assert_clean(at)

    def test_no_reload_per_viewer_only_rare_shared_control_load(self):
        self.ausrichten(300)
        zuschauer = [self.app(TT_SLUG) for _ in range(3)]
        self.assertEqual(self._zeilen_abrufe(), 1)  # ein gemeinsamer Abruf für alle
        for _ in range(12):  # eine Minute ohne Änderung
            self.versatz += 5
            for at in zuschauer:
                at.run()
        self.assertEqual(self._zeilen_abrufe(), 1)  # kein Reload je Zuschauer alle 60 s
        self.versatz += 300  # neues Kontrollfenster
        for at in zuschauer:
            at.run()
        self.assertEqual(self._zeilen_abrufe(), 2)  # eine gemeinsame Kontrollladung

    def test_viewers_share_small_check_within_window(self):
        zuschauer = [self.app(TT_SLUG) for _ in range(4)]
        pruefungen = lambda: sum("/testtage?" in c["url"] for c in self.api.calls)  # noqa: E731
        vorher = pruefungen()
        self.ausrichten(3)
        for at in zuschauer:
            at.run()
        self.assertEqual(pruefungen(), vorher + 1)  # ein Abruf für vier Zuschauer
        self.assertEqual(self._zeilen_abrufe(), 1)

    def test_new_publication_reaches_all_viewers_with_one_load(self):
        zuschauer = [self.app(TT_SLUG) for _ in range(3)]
        live = next(t for t in self.api.testtage if t["id"] == LIVE_ID)
        live["stand_am"] = "2026-10-09T09:45:00+00:00"
        self.versatz += 3
        for at in zuschauer:
            at.run()
            self.assertIn("Stand ", self.text(at))
        self.assertEqual(self._zeilen_abrufe(), 2)  # erster Abruf + ein gemeinsamer für den neuen Stand

    def test_hidden_testtag_is_not_shown_from_cache_or_session(self):
        at = self.app(TT_SLUG)
        self.assertTrue(self.cards(at))
        anderer = self.app(TT_SLUG)  # Stand liegt im gemeinsamen Cache
        live = next(t for t in self.api.testtage if t["id"] == LIVE_ID)
        live["ist_oeffentlich"] = False  # Sperre: Publisher verbirgt den Testtag
        self.versatz += 3
        at.run()
        anderer.run()
        neu = self.app(TT_SLUG)
        for sicht in (at, anderer, neu):
            self.assertEqual(self.cards(sicht), [])
            self.assertIn("Dieser Testtag ist nicht verfügbar.", self.text(sicht))
        for sicht in (at, anderer):  # auch der frühere Sitzungsstand ist verworfen
            self.assertNotIn(f"stand|{LIVE_ID}", sicht.session_state)
        self.assert_clean(neu)

    def test_outage_keeps_last_valid_state_and_recovers(self):
        at = self.app(TT_SLUG)
        before = self.cards(at)
        self.api.ausnahme = OSError("Netz weg")
        for _ in range(3):
            self.versatz += 5
            at.run()
            self.assertEqual(self.cards(at), before)
            self.assertTrue(any(w.value.startswith("Verbindung unterbrochen") for w in at.warning))
        self.api.ausnahme = None
        for _ in range(4):  # Wiederherstellung spätestens nach der längsten gemeinsamen Sperrzeit (10 s)
            self.versatz += 5
            at.run()
        self.assertEqual([w.value for w in at.warning], [])
        self.assertEqual(self.cards(at), before)

    def test_new_viewer_during_outage_gets_data_after_recovery(self):
        self.api.ausnahme = OSError("Netz weg")
        at = self.app(TT_SLUG)
        self.assertIn("Die Seite versucht es automatisch erneut.", self.text(at))
        self.assertEqual(self.cards(at), [])  # keine scheinbar gültigen Daten
        self.api.ausnahme = None
        for _ in range(4):
            self.versatz += 5
            at.run()
        self.assertTrue(self.cards(at))
        self.assertNotIn("versucht es automatisch erneut", self.text(at))
        self.assert_clean(at)

    def test_waiting_view_rebuilds_page_after_recovery(self):
        app = __import__("app")
        with mock.patch.object(app.st, "rerun") as neu, mock.patch.object(app.st, "info") as info:
            app._warten(mock.Mock(side_effect=q.QuellenFehler("weg", "NETZ")))
            neu.assert_not_called()
            info.assert_called_once_with(app.WARTET)
            app._warten(lambda: None)
            neu.assert_called_once_with(scope="app")

    def test_testtag_hidden_during_outage_is_not_shown_after_recovery(self):
        alt = self.app(TT_SLUG)
        self.assertTrue(self.cards(alt))
        self.api.ausnahme = OSError("Netz weg")
        self.versatz += 5
        alt.run()
        neu = self.app(TT_SLUG)  # neuer Zuschauer während des Ausfalls
        live = next(t for t in self.api.testtage if t["id"] == LIVE_ID)
        live["ist_oeffentlich"] = False  # während des Ausfalls verborgen
        self.api.ausnahme = None
        for _ in range(4):
            self.versatz += 5
            alt.run()
            neu.run()
        for sicht in (alt, neu):
            self.assertEqual(self.cards(sicht), [])
            self.assertIn("Dieser Testtag ist nicht verfügbar.", self.text(sicht))
            self.assertNotIn(f"stand|{LIVE_ID}", sicht.session_state)

    def test_outage_attempts_are_limited_across_viewers(self):
        zuschauer = [self.app(TT_SLUG) for _ in range(5)]
        self.api.ausnahme = OSError("Netz weg")
        vorher = len(self.api.calls)
        for _ in range(12):  # eine Minute Ausfall, 5 Zuschauer im 5-s-Takt
            self.versatz += 5
            for at in zuschauer:
                at.run()
        versuche = len(self.api.calls) - vorher
        self.assertLessEqual(versuche, 8)  # statt rund 60 ohne gemeinsame Bremse
        self.assertGreaterEqual(versuche, 3)  # es wird weiter geprüft
        self.api.ausnahme = None
        for _ in range(4):
            self.versatz += 5
            for at in zuschauer:
                at.run()
        for at in zuschauer:
            self.assertTrue(self.cards(at))
            self.assertEqual([w.value for w in at.warning], [])

    def test_archive_and_prepared_testtag_are_not_polled(self):
        at = self.app(ARCHIV_SLUG)
        abrufe = len(self.api.calls)
        self.versatz += 600  # auch nach Ablauf aller Zeitfenster: Archiv bleibt eingefroren
        at.run()
        self.assertEqual(sum("/archiv_snapshots?" in c["url"] for c in self.api.calls[abrufe:]), 0)

    def test_first_load_error_shows_no_seemingly_valid_ranking(self):
        self.api.manipulieren = lambda rows: rows + [rows[0]]  # doppelter Primärschlüssel
        at = self.app(TT_SLUG)
        self.assertIn("Daten zurzeit nicht erreichbar", self.text(at))
        self.assertEqual(self.cards(at), [])
        self.assertEqual(self.block(at, "tt-liste tt-rangliste"), "")
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assert_clean(at)

    def test_archive_is_not_refreshed_automatically(self):
        self.assertIsNone(__import__("app").takt_fuer("archiviert"))
        self.assertIsNone(__import__("app").takt_fuer("vorbereitet"))
        self.assertEqual(__import__("app").takt_fuer("live"), "5s")
        at = self.app(ARCHIV_SLUG)
        self.assertIn("Archiv", klartext(self.kopf(at)))
        self.assertIn("tt-badge archiv", self.kopf(at))
        archiv_loads = lambda: sum("/archiv_snapshots?" in c["url"] for c in self.api.calls)  # noqa: E731
        self.assertEqual(archiv_loads(), 1)
        at.run()
        self.go(at, q.RANGLISTE)
        self.assertEqual(archiv_loads(), 1)  # kein erneutes Laden
        pruefungen = lambda: sum("/testtage?" in c["url"] for c in self.api.calls)  # noqa: E731
        live = self.app(TT_SLUG)
        before = pruefungen()
        self.versatz += 3
        live.run()
        self.assertGreater(pruefungen(), before)  # Live prüft bei jedem Takt den Stand (gemeinsam je 3 s)

    # ── Archiv (P1F) ──
    def test_archive_is_shown_only_from_snapshot(self):
        self.api.rows = [r for r in self.api.rows if r["oeffentliche_testtag_id"] != _support.ARCHIV_ID]
        at = self.go(self.app(ARCHIV_SLUG), q.RANGLISTE)
        self.assertFalse(any("/veroeffentlichung?" in c["url"] for c in self.api.calls))
        at.selectbox(key="rl_ak").set_value("AK 7").run()
        expected = next(g["eintraege"] for r in self.data["rows"] if r["oeffentliche_testtag_id"] == _support.ARCHIV_ID
                        and r["bereich"] == "resultate_rangliste" and r["ak"] == "AK 7"
                        for g in r["inhalt"]["gruppen"] if g["liste"] == "gesamt")
        self.assertEqual([r for r, _p in self.raenge(at)], [str(e["rang"]) for e in expected])
        self.go(at, q.LIVE)
        self.assertTrue(self.cards(at))
        self.assertNotIn("AK 5", at.selectbox(key="live_ak").options)  # Tombstone
        self.assert_clean(at)

    def test_damaged_snapshot_shows_neutral_message_without_fallback(self):
        self.api.manipulieren_archiv = lambda snaps: [dict(snaps[0], pruefsumme="0" * 64)]
        at = self.app(ARCHIV_SLUG)
        self.assertIn("Daten zurzeit nicht erreichbar", self.text(at))
        self.assertEqual((self.block(at, "tt-liste tt-rangliste"), self.cards(at)), ("", []))
        self.assertFalse(any("/veroeffentlichung?" in c["url"] for c in self.api.calls))
        self.assert_clean(at)

    def test_damaged_snapshot_keeps_last_valid_session_state(self):
        at = self.app(ARCHIV_SLUG)
        before = self.cards(at)
        self.assertTrue(before)
        archiv = next(t for t in self.api.testtage if t["id"] == _support.ARCHIV_ID)
        # Sitzungsstand stammt noch aus "live"; jetzt archiviert, Snapshot aber beschädigt
        at.session_state[f"quelle|{_support.ARCHIV_ID}"] = "live"
        self.api.manipulieren_archiv = lambda snaps: []
        self.versatz += 300  # neues Kontrollfenster: nicht aus dem gemeinsamen Cache
        at.run()
        self.assertEqual(self.cards(at), before)
        self.assertTrue(any(w.value.startswith("Verbindung unterbrochen") for w in at.warning))
        self.assertEqual(archiv["status"], "archiviert")
        self.assert_clean(at)

    def test_access_errors_are_neutral(self):
        at = self.app(TT_SLUG, secrets={"supabase_url": "https://example-ref.supabase.co",
                                        "supabase_publishable_key": TEST_PUBLISHABLE_KEY,
                                        "supabase_secret_key": TEST_SECRET_KEY})
        self.assertIn("Die öffentliche Ansicht darf keinen Schreibschlüssel erhalten.", self.text(at))
        self.assertEqual(self.api.calls, [])
        self.assert_clean(at)
        at = self.app(TT_SLUG, secrets={"irgendwas": "x"})
        self.assertIn("noch kein Zugang hinterlegt", self.text(at))

    def test_only_publishable_key_in_apikey_header(self):
        at = self.app(TT_SLUG)
        for bereich in q.BEREICHE:
            self.go(at, bereich)
        self.assertTrue(self.api.calls)
        for call in self.api.calls:
            self.assertEqual(call["kopf"], {"apikey": TEST_PUBLISHABLE_KEY, "Accept": "application/json"})

    def test_compact_layout_for_narrow_screens(self):
        at = self.app(TT_SLUG)
        for bereich in q.BEREICHE:
            self.go(at, bereich)
            self.assertEqual(len(at.dataframe), 0)  # keine breiten Datentabellen
            for kopfzeile in re.findall(r"<thead>(.*?)</thead>", self.roh(at)):
                self.assertLessEqual(kopfzeile.count("<th>"), 5)
        stil = self.roh(at)
        self.assertIn("@media (max-width: 640px)", stil)
        self.assertIn("overflow-wrap: anywhere", stil)  # lange Vereinsnamen brechen um
        self.assertIn("min-height: 40px", stil)  # Touch-Ziele der Navigation

    def test_empty_states_are_consistent(self):
        leer = [dict(r) for r in self.data["rows"] if r["bereich"] not in ("resultate_live",)]
        api = FakeOeffentlich(self.data["testtage"], leer, self.data["snapshots"])
        with mock.patch.object(q, "https_transport", api):
            at = self.app(TT_SLUG)
            self.assertIn("<div class='tt-leer'>Noch keine Resultate.</div>", self.roh(at))
            self.assert_clean(at)

    # ── Darstellung: Texte werden nie als HTML oder Markdown ausgeführt ──
    def test_display_texts_are_escaped(self):
        import app
        karte = app.live_karte(1, {"bereich": "technik", "vorname": "<script>x</script>", "name": "*Fett* $a$",
                                   "verein": "TV [Link](http://x)", "programm": AK5, "ak": "AK 7", "geraet": "Boden",
                                   "anzeige": "Linie | 1", "S": 5, "T": 7.5, "geaendert_am": "2026-10-09T08:00:00Z"})
        self.assertNotIn("<script>", karte)
        self.assertIn("&lt;script&gt;", karte)
        for roh in ("*Fett*", "$a$", "[Link]", "|"):
            self.assertNotIn(roh, karte)
        self.assertEqual(klartext(karte).split(" Boden ")[0],
                         "1 <script>x</script> *Fett* $a$ TV [Link](http://x) · AK5–AK9+ · AK 7")
        tabelle = app.startliste_tabelle({"startgeraet": "<b>Reck</b>", "teilnehmer": [
            {"position": 1, "vorname": "A&B", "name": "C", "jahrgang": 2015, "ak": "AK 7", "verein": "<i>V</i>"}]})
        self.assertNotIn("<b>", tabelle)
        self.assertNotIn("<i>", tabelle)
        self.assertIn("A&amp;B C", tabelle)


if __name__ == "__main__":
    unittest.main()
