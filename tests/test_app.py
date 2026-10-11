"""Streamlit-App mit `streamlit.testing.v1.AppTest` und synthetischer öffentlicher Schnittstelle (kein Netzwerk)."""
from __future__ import annotations

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

    @staticmethod
    def text(at: AppTest) -> str:
        parts = []
        for kind in ("title", "subheader", "markdown", "caption", "info", "warning", "error", "success"):
            parts += [str(e.value) for e in getattr(at, kind)]
        parts += [str(b.label) for b in at.button]
        parts += [df.value.to_string() for df in at.dataframe]
        return "\n".join(parts)

    def assert_clean(self, at: AppTest) -> None:
        text = self.text(at)
        for marker in VERBOTEN:
            self.assertNotIn(marker, text)
        for key in self.keys:
            self.assertNotIn(key[:16], text)

    @staticmethod
    def cards(at: AppTest) -> list[str]:
        return [m.value for m in at.markdown if " · " in str(m.value) and "  \n" in str(m.value)]

    def go(self, at: AppTest, bereich: str) -> AppTest:
        at.radio(key="bereich").set_value(bereich).run()
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        return at

    # ── Übersicht und Navigation ──
    def test_overview_without_parameter_separates_live_and_archive(self):
        at = self.app()
        self.assertEqual(at.title[0].value, "Testtage")
        self.assertEqual([s.value for s in at.subheader], ["Live", "Demnächst", "Archiv"])
        labels = [b.label for b in at.button]
        self.assertIn(f"● LIVE · {TT_TITLE}", labels)
        self.assertIn("Synthetischer Testtag 2025 – Archivhausen", labels)
        self.assertNotIn("Nicht öffentlich", labels)
        self.assertIn(f"?t={TT_SLUG}", self.text(at))
        at.button(key=f"oeffnen|{TT_SLUG}").click().run()
        self.assertEqual(at.query_params["t"], [TT_SLUG])
        self.assertEqual(at.title[0].value, TT_TITLE)
        self.assert_clean(at)

    def test_slug_opens_correct_testtag_and_uuid_is_redirected_to_slug(self):
        at = self.app(TT_SLUG)
        self.assertEqual(at.title[0].value, TT_TITLE)
        self.assertIn("**● Live**", self.text(at))
        at = self.app(LIVE_ID)
        self.assertEqual(at.title[0].value, TT_TITLE)
        self.assertEqual(at.query_params["t"], [TT_SLUG])

    def test_unknown_or_invalid_testtag_shows_neutral_message(self):
        for kennung in ("gibt-es-nicht", "synth-geheim", "x&select=*"):
            with self.subTest(kennung=kennung):
                at = self.app(kennung)
                self.assertIn("Dieser Testtag ist nicht verfügbar.", [i.value for i in at.info])
                self.assertEqual(len(at.dataframe), 0)
                self.assert_clean(at)

    def test_four_areas_are_reachable(self):
        at = self.app(TT_SLUG)
        self.assertEqual(at.radio(key="bereich").options, ["Live", "Ranglisten", "Startliste", "Zeitplan"])
        for bereich in q.BEREICHE:
            self.go(at, bereich)
            self.assert_clean(at)

    # ── Live ──
    def test_live_newest_first_both_programs_and_filters(self):
        at = self.app(TT_SLUG)
        self.assertEqual(at.selectbox(key="live_programm").options, ["Alle", "AK5–AK9+", "AK9–AK12"])
        cards = self.cards(at)
        times = [re.search(r"(\d\d:\d\d:\d\d)$", c).group(1) for c in cards]
        self.assertEqual(times[0], "11:30:00")  # neueste Änderung (09:30 UTC) zuerst
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertTrue(any("AK5–AK9+" in c for c in cards) and any("AK9–AK12" in c for c in cards))
        at.selectbox(key="live_programm").set_value(AK9).run()
        self.assertTrue(all("AK9–AK12" in c for c in self.cards(at)))
        self.assertEqual(at.selectbox(key="live_ak").options, ["Alle", "AK 9", "AK 10", "AK 11"])
        at.selectbox(key="live_ak").set_value("AK 9").run()
        ak9 = self.cards(at)
        self.assertTrue(ak9 and all(" · AK 9 · " in c for c in ak9))
        self.assertTrue(any("Sprung · Sprung 1 · S 3 · T 3" in c for c in ak9))
        self.assertTrue(any("Sprung · Sprung 2 · S 4 · T 4" in c for c in ak9))
        self.assertNotIn("Tsukahara", self.text(at))
        self.assert_clean(at)

    def test_live_shows_s_and_t_for_technik_and_points_for_athletics(self):
        at = self.app(TT_SLUG)
        text = "\n".join(self.cards(at))
        self.assertIn("Boden · Synth Boden vorwärts · S 5 · T 7.5", text)
        self.assertIn("Kraft · Standweitsprung · Punkte 8", text)
        self.assertNotIn("1.5", text)  # TAL
        self.assertNotIn("AK 5 ·", text)  # Tombstone-AK erscheint nicht
        self.assertNotIn("AK 5", at.selectbox(key="live_ak").options)

    # ── Ranglisten ──
    def test_ranking_keeps_publisher_ranks_and_marks_missing_results(self):
        at = self.go(self.app(TT_SLUG), q.RANGLISTE)
        self.assertEqual(at.selectbox(key="rl_programm").value, AK5)
        self.assertEqual(at.selectbox(key="rl_ak").options, ["AK 6", "AK 7"])
        self.assertEqual(at.radio(key="rl_art").value, "gesamt")
        at.selectbox(key="rl_ak").set_value("AK 7").run()
        df = at.dataframe[0].value
        self.assertEqual(list(df.columns), ["Rang", "Name", "Vorname", "Verein", "Punkte"])
        self.assertEqual(list(df["Rang"]), ["1", "2", "2", "4"])
        self.assertEqual(list(df["Punkte"]), ["15.5", "6", "6", "–"])
        at.radio(key="rl_art").set_value("athletik").run()
        self.assertEqual(list(at.dataframe[0].value["Punkte"]), ["8", "–", "–", "–"])
        self.assertIn("Alle Rotationen freigegeben.", [c.value for c in at.caption])  # Hinweis des Publishers
        self.assert_clean(at)

    # ── Startliste und Zeitplan ──
    def test_startliste_grouped_by_program_and_start_apparatus(self):
        at = self.go(self.app(TT_SLUG), q.STARTLISTE)
        self.assertEqual([s.value for s in at.subheader], ["AK5–AK9+", "AK9–AK12"])
        gruppen = [m.value for m in at.markdown if str(m.value).startswith("**Startgerät / Riege:")]
        self.assertEqual(gruppen[:4], ["**Startgerät / Riege: Boden**", "**Startgerät / Riege: Pferd**",
                                       "**Startgerät / Riege: Beweglichkeit**", "**Startgerät / Riege: Reck**"])
        self.assertNotIn("**Startgerät / Riege: Ringe**", gruppen)  # leere Gruppe
        first = at.dataframe[1].value
        self.assertEqual(list(first.columns), ["Pos.", "Name", "Vorname", "Jg.", "AK", "Verein"])
        self.assertEqual(list(first["Pos."]), ["1", "2"])
        self.assert_clean(at)

    def test_zeitplan_is_one_day_in_publisher_order(self):
        at = self.go(self.app(TT_SLUG), q.ZEITPLAN)
        self.assertEqual([s.value for s in at.subheader], ["AK5–AK9+", "AK9–AK12"])
        zeilen = [m.value for m in at.markdown if str(m.value).startswith("**") and " · " in str(m.value)]
        self.assertEqual(zeilen, ["**07:30** · Hallenöffnung – AK5–AK9+", "**08:05–12:30** · Wettkampf",
                                  "**13:55** · Besammlung und Einmarsch – AK9–AK12", "**14:00 – 16:00** · Wettkampf"])

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
        self.assertEqual(len(at.dataframe), 0)
        self.assertEqual(self.cards(at), [])
        self.assert_clean(at)

    def test_archive_is_not_refreshed_automatically(self):
        self.assertIsNone(__import__("app").takt_fuer("archiviert"))
        self.assertIsNone(__import__("app").takt_fuer("vorbereitet"))
        self.assertEqual(__import__("app").takt_fuer("live"), "10s")
        at = self.app(ARCHIV_SLUG)
        self.assertIn("**Archiv** – abgeschlossener Testtag, keine Aktualisierung", self.text(at))
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
        self.assertEqual(list(at.dataframe[0].value["Rang"]), [str(e["rang"]) for e in expected])
        self.go(at, q.LIVE)
        self.assertTrue(self.cards(at))
        self.assertNotIn("AK 5", at.selectbox(key="live_ak").options)  # Tombstone
        self.assert_clean(at)

    def test_damaged_snapshot_shows_neutral_message_without_fallback(self):
        self.api.manipulieren_archiv = lambda snaps: [dict(snaps[0], pruefsumme="0" * 64)]
        at = self.app(ARCHIV_SLUG)
        self.assertIn("Die Daten dieses Testtags sind zurzeit nicht verfügbar", self.text(at))
        self.assertEqual((len(at.dataframe), self.cards(at)), (0, []))
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
        self.assertEqual(len(at.dataframe), 0)  # Live: kompakte Karten statt breiter Tabelle
        self.go(at, q.RANGLISTE)
        self.assertLessEqual(max(len(df.value.columns) for df in at.dataframe), 5)
        self.go(at, q.ZEITPLAN)
        self.assertEqual(len(at.dataframe), 0)
        self.assertNotIn("– \n", self.text(at))  # leere Hinweise erzeugen keine leere Spalte


if __name__ == "__main__":
    unittest.main()
