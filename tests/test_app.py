"""Streamlit-App mit `streamlit.testing.v1.AppTest` und synthetischer öffentlicher Schnittstelle (kein Netzwerk)."""
from __future__ import annotations

import html
import re
import sys
import tempfile
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
NAV = ["⚡ Live", "🏅 Ranglisten", "📋 Startliste", "🕐 Zeitplan"]
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
        self.api = FakeOeffentlich(self.data["testtage"], self.data["rows"], self.data["snapshots"])
        patcher = mock.patch.object(q, "https_transport", self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, t: str | None = None, secrets: dict | None = None) -> AppTest:
        at = AppTest.from_file(APP, default_timeout=60)
        for key, value in (secrets or {"supabase_url": "https://example-ref.supabase.co",
                                       "supabase_publishable_key": TEST_PUBLISHABLE_KEY}).items():
            at.secrets[key] = value
        if t:
            at.query_params["t"] = t
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
        self.assertIn(f"?t={TT_SLUG}", text)
        roh = self.roh(at)
        for klasse in ("tt-badge live", "tt-badge demnaechst", "tt-badge archiv"):
            self.assertIn(klasse, roh)
        at.button(key=f"oeffnen|{TT_SLUG}").click().run()
        self.assertEqual(at.query_params["t"], [TT_SLUG])
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assert_clean(at)

    def test_uniform_header_logo_status_and_footer_in_every_view(self):
        for kennung, badge in ((None, None), (TT_SLUG, "Live – wird etwa alle 10 Sekunden aktualisiert"),
                               (ARCHIV_SLUG, "Archiv – abgeschlossener Testtag, keine Aktualisierung"),
                               ("synth-testtag-2027", "Vorbereitet – noch nicht gestartet")):
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

    # ── Live ──
    def test_live_newest_first_both_programs_and_filters(self):
        at = self.app(TT_SLUG)
        self.assertEqual(at.selectbox(key="live_programm").options, ["Alle", "AK5–AK9+", "AK9–AK12"])
        cards = self.cards(at)
        times = [re.search(r"\b(\d\d:\d\d:\d\d)\b", c).group(1) for c in cards]
        self.assertEqual(times[0], "11:30:00")  # neueste Änderung (09:30 UTC) zuerst
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertTrue(any("AK5–AK9+" in c for c in cards) and any("AK9–AK12" in c for c in cards))
        self.assertIn(f"{len(cards)} Resultate – neueste Änderung zuerst", self.text(at))
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
        self.assertIn("Noch keine Einzelresultate veröffentlicht.", klartext(rl))
        ohne = klartext(rl.split("tt-rangkarte")[-1])  # Person ohne Resultat: keine scheinbaren Nullwerte
        self.assertIn("Technik – Beweglichkeit – Kraft – Athletik – Gesamt –", ohne)
        erste = klartext(rl.split("tt-rangkarte")[1])  # Technik und Kraft vorhanden, Beweglichkeit nicht
        self.assertIn("Technik 7.5 Beweglichkeit – Kraft 8 Athletik 8 Gesamt 15.5", erste)
        self.assertIn("Boden · Synth Boden vorwärts · S 5 · T 7.5", klartext(rl))  # Einzelheiten
        at.radio(key="rl_art").set_value("athletik").run()
        self.assertEqual([p for _r, p in self.raenge(at)], ["8", "–", "–", "–"])
        self.assertIn("Alle Rotationen freigegeben.", self.text(at))  # Hinweis des Publishers
        self.assertEqual(len(at.dataframe), 0)
        self.assert_clean(at)

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

                    def kaputt(rows):
                        for row in rows:
                            if row["bereich"] == "resultate_live" and row["inhalt"].get("aktiv") is not False:
                                row["inhalt"]["gruppen"][0]["resultate"] = []
                        return rows
                    self.api.manipulieren = kaputt
                at.run()
                self.assertEqual(self.cards(at), before)
                warnings = [w.value for w in at.warning]
                self.assertTrue(any(w.startswith("Aktualisierung vorübergehend nicht möglich – letzter gültiger "
                                                 "Stand wird angezeigt (Stand ") for w in warnings), warnings)
                self.assert_clean(at)
        self.api.status, self.api.manipulieren = None, None
        at.run()
        self.assertEqual([w.value for w in at.warning], [])

    def test_first_load_error_shows_no_seemingly_valid_ranking(self):
        self.api.manipulieren = lambda rows: rows + [rows[0]]  # doppelter Primärschlüssel
        at = self.app(TT_SLUG)
        self.assertIn("Die Daten dieses Testtags sind zurzeit nicht verfügbar", self.text(at))
        self.assertEqual(self.cards(at), [])
        self.assertEqual(self.block(at, "tt-liste tt-rangliste"), "")
        self.assertIn(f"<h1>{TT_TITLE}</h1>", self.kopf(at))
        self.assert_clean(at)

    def test_archive_is_not_refreshed_automatically(self):
        self.assertIsNone(__import__("app").takt_fuer("archiviert"))
        self.assertIsNone(__import__("app").takt_fuer("vorbereitet"))
        self.assertEqual(__import__("app").takt_fuer("live"), "10s")
        at = self.app(ARCHIV_SLUG)
        self.assertIn("Archiv – abgeschlossener Testtag, keine Aktualisierung", klartext(self.kopf(at)))
        self.assertIn("tt-badge archiv", self.kopf(at))
        archiv_loads = lambda: sum("/archiv_snapshots?" in c["url"] for c in self.api.calls)  # noqa: E731
        self.assertEqual(archiv_loads(), 1)
        at.run()
        self.go(at, q.RANGLISTE)
        self.assertEqual(archiv_loads(), 1)  # kein erneutes Laden
        loads = lambda: sum("/veroeffentlichung?" in c["url"] for c in self.api.calls)  # noqa: E731
        live = self.app(TT_SLUG)
        before = loads()
        live.run()
        self.assertGreater(loads(), before)

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
        self.assertIn("Die Daten dieses Testtags sind zurzeit nicht verfügbar", self.text(at))
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
        at.run()
        self.assertEqual(self.cards(at), before)
        self.assertTrue(any(w.value.startswith("Aktualisierung vorübergehend nicht möglich") for w in at.warning))
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
            self.assertIn("<div class='tt-leer'>Es sind noch keine Live-Resultate veröffentlicht.</div>", self.roh(at))
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
