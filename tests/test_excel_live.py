"""excel_live: nur lesender Zugriff auf eine geöffnete Excel-Mappe (COM-Attrappe, kein Excel nötig)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (Pfade, Netzwerksperre)

from testtag_public import excel_live as el
from testtag_public.excel_publication import SHEETS


class Bereich:
    def __init__(self, werte):
        self.Address, self.Value2 = "$A$1:$C$2", werte


class Blatt:
    def __init__(self, werte):
        self.UsedRange = Bereich(werte)


class Mappe:
    """Attrappe einer Excel-Arbeitsmappe; jeder andere Zugriff als Lesen und SaveCopyAs wird festgehalten."""

    erlaubt = {"Name", "FullName", "Worksheets", "SaveCopyAs", "blaetter", "aufrufe", "waehrend_kopie"}

    def __init__(self, name, voll):
        object.__setattr__(self, "aufrufe", [])
        self.Name, self.FullName = name, voll
        self.blaetter = {s: Blatt(((1.0, "x", None), (2.5, None, 3.0))) for s in SHEETS}
        self.waehrend_kopie = None

    def __setattr__(self, key, value):
        if key not in self.erlaubt:
            raise AssertionError(f"Schreibzugriff auf {key}")
        object.__setattr__(self, key, value)

    def __getattr__(self, key):  # nur für nicht definierte Attribute (Save, Close, Range …)
        self.aufrufe.append(key)
        raise AttributeError(key)

    def Worksheets(self, name):  # noqa: N802
        return self.blaetter[name]

    def SaveCopyAs(self, ziel):  # noqa: N802
        self.aufrufe.append("SaveCopyAs")
        Path(ziel).write_bytes(b"kopie")
        if self.waehrend_kopie:
            self.waehrend_kopie()


class Excel:
    def __init__(self, *mappen):
        self.Workbooks = list(mappen)
        self.Calculation, self.CalculationState, self.Ready = el.XL_BERECHNUNG_AUTOMATISCH, 0, True


PFAD = Path("C:/Daten/Wettkampf/00-tool/261009-Test-AK5-AK9plus.xlsx")
URL = "https://example.sharepoint.com/sites/x/Freigegebene%20Dokumente/Wettkampf/00-tool/261009-Test-AK5-AK9plus.xlsx"


class ExcelLiveTests(unittest.TestCase):
    def setUp(self):
        self.mappe = Mappe(PFAD.name, URL)
        self.excel = Excel(Mappe(PFAD.name, URL.replace("00-tool", "anderer-ordner")), self.mappe)
        self.offen = el.OffeneMappe(PFAD, anwendung=lambda: self.excel)

    def test_matches_by_file_and_folder_name_local_or_sharepoint(self):
        self.assertTrue(el.passt(self.mappe, PFAD))
        self.assertTrue(el.passt(Mappe(PFAD.name, str(PFAD).replace("/", "\\")), PFAD))
        self.assertFalse(el.passt(self.excel.Workbooks[0], PFAD))  # gleicher Name, anderer Ordner
        self.assertFalse(el.passt(Mappe("ak9.xlsx", URL), PFAD))

    def test_fingerprint_follows_content(self):
        erster = self.offen.zustand()
        self.assertIsNotNone(erster.fingerabdruck)
        self.assertEqual(self.offen.zustand(), erster)
        self.mappe.blaetter["Noteneingabe"].UsedRange.Value2 = ((1.0, "x", None), (2.5, None, 3.5))
        self.assertNotEqual(self.offen.zustand().fingerabdruck, erster.fingerabdruck)

    def test_no_fingerprint_while_calculation_incomplete_or_excel_busy(self):
        for attr, wert, grund in (("CalculationState", 1, el.BERECHNUNG_LAEUFT),
                                  ("Calculation", -4135, el.BERECHNUNG_MANUELL),
                                  ("Ready", False, el.BESCHAEFTIGT)):
            with self.subTest(attr=attr):
                excel = Excel(self.mappe)
                setattr(excel, attr, wert)
                self.assertEqual(el.OffeneMappe(PFAD, anwendung=lambda: excel).zustand(), el.Zustand(None, grund))
        self.assertEqual(el.OffeneMappe(PFAD, anwendung=lambda: None).zustand().grund, el.NICHT_GEOEFFNET)

        def abgewiesen():
            raise OSError("Aufruf vom Empfänger abgelehnt")
        self.assertEqual(el.OffeneMappe(PFAD, anwendung=abgewiesen).zustand().grund, el.BESCHAEFTIGT)

    def test_copy_only_with_expected_state_and_never_saves_original(self):
        fp = self.offen.zustand().fingerabdruck
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / PFAD.name
            self.assertFalse(self.offen.kopie(ziel, "anderer-stand"))
            self.assertFalse(ziel.exists())
            self.assertTrue(self.offen.kopie(ziel, fp))
            self.assertTrue(ziel.is_file())
        self.assertEqual(self.mappe.aufrufe, ["SaveCopyAs"])  # kein Save, Close oder Zellzugriff

    def test_copy_is_rejected_if_state_changes_or_calculation_starts(self):
        fp = self.offen.zustand().fingerabdruck

        def eingabe():
            self.mappe.blaetter["Noteneingabe"].UsedRange.Value2 = ((9.0,),)

        def rechnet():
            self.excel.CalculationState = 1
        for nebenwirkung in (eingabe, rechnet):
            with self.subTest(nebenwirkung=nebenwirkung.__name__), tempfile.TemporaryDirectory() as tmp:
                self.setUp()
                fp = self.offen.zustand().fingerabdruck
                self.mappe.waehrend_kopie = nebenwirkung
                self.assertFalse(self.offen.kopie(Path(tmp) / PFAD.name, fp))


if __name__ == "__main__":
    unittest.main()
