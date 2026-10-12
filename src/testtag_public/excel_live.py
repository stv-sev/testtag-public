"""Geöffnete Excel-Arbeitsmappe als zusätzlicher Auslöser für den Dauerpublisher (nur Windows, Excel Desktop).

Fall: Eine Note wird in Excel Online eingegeben. Über Co-Authoring erscheint sie in der auf dem Publisher-Laptop
geöffneten Desktop-Arbeitsmappe, die lokale Datei erhält aber (noch) keinen neuen Speicherstand. Der Dateiweg sieht
die Änderung dann nicht.

Dieses Modul greift nur lesend auf die geöffnete Mappe zu:

- `zustand()`: Fingerabdruck (SHA-256) über die verwendeten Bereiche aller vom Publisher gelesenen Blätter. Kein
  Fingerabdruck, solange Excel beschäftigt ist (z. B. Zelle in Bearbeitung), die Berechnung nicht automatisch ist
  oder noch läuft.
- `kopie()`: `Workbook.SaveCopyAs` in eine temporäre Datei. Das Original, sein Speicherzustand und der
  SharePoint-Stand bleiben unverändert (nachgewiesen: Original bytegleich, `Saved` unverändert, kein Hochladen).
  Vor und nach der Kopie müssen Berechnungsstatus und Fingerabdruck stimmen, sonst wird die Kopie verworfen.

Die Kopie durchläuft danach unverändert die bestehende Prüfung (`read_program_source`): fehlende Formelresultate,
Excel-Fehlerwerte und alle fachlichen Regeln führen wie beim Dateiweg dazu, dass nichts publiziert wird.
Es wird nie gespeichert, geschlossen oder eine Zelle verändert.
"""
from __future__ import annotations

import hashlib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .excel_publication import SHEETS

XL_BERECHNUNG_AUTOMATISCH = -4105  # xlCalculationAutomatic
XL_BERECHNUNG_FERTIG = 0  # xlDone

NICHT_GEOEFFNET = "nicht_geoeffnet"
BESCHAEFTIGT = "beschaeftigt"
BERECHNUNG_LAEUFT = "berechnung_laeuft"
BERECHNUNG_MANUELL = "berechnung_manuell"


@dataclass(frozen=True)
class Zustand:
    fingerabdruck: Optional[str]
    grund: Optional[str] = None  # warum kein Fingerabdruck


def excel_anwendung():
    """Laufende Excel-Instanz oder None (kein Excel, kein pywin32)."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
        return win32com.client.GetActiveObject("Excel.Application")
    except Exception:  # pywintypes.com_error: Excel läuft nicht
        return None


def verfuegbar() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def passt(workbook, path: Path) -> bool:
    """Gleiche Mappe: gleicher Dateiname und gleicher Ordnername (lokaler Pfad oder SharePoint-Adresse)."""
    try:
        name, voll = str(workbook.Name), str(workbook.FullName)
    except Exception:
        return False
    voll = urllib.parse.unquote(voll).replace("\\", "/").lower()
    return name.lower() == path.name.lower() and voll.endswith(f"/{path.parent.name}/{path.name}".lower())


def _berechnung(app) -> Optional[str]:
    if app.Calculation != XL_BERECHNUNG_AUTOMATISCH:
        return BERECHNUNG_MANUELL
    if app.CalculationState != XL_BERECHNUNG_FERTIG:
        return BERECHNUNG_LAEUFT
    if not bool(app.Ready):
        return BESCHAEFTIGT
    return None


def fingerabdruck(workbook) -> str:
    h = hashlib.sha256()
    for name in SHEETS:
        bereich = workbook.Worksheets(name).UsedRange
        h.update(repr((name, bereich.Address, bereich.Value2)).encode("utf-8"))
    return h.hexdigest()


class OffeneMappe:
    """Nur lesender Zugriff auf eine in Excel Desktop geöffnete Arbeitsmappe."""

    def __init__(self, path: Path, *, anwendung: Callable[[], object] = excel_anwendung):
        self.path = Path(path)
        self.anwendung = anwendung

    def _mappe(self):
        app = self.anwendung()
        if app is None:
            return None, None
        for workbook in app.Workbooks:
            if passt(workbook, self.path):
                return app, workbook
        return app, None

    def zustand(self) -> Zustand:
        try:
            app, workbook = self._mappe()
            if workbook is None:
                return Zustand(None, NICHT_GEOEFFNET)
            grund = _berechnung(app)
            if grund:
                return Zustand(None, grund)
            wert = fingerabdruck(workbook)
            grund = _berechnung(app)  # während des Lesens begonnen?
            return Zustand(None, grund) if grund else Zustand(wert)
        except Exception:  # COM abgewiesen (Excel beschäftigt) oder Mappe geschlossen
            return Zustand(None, BESCHAEFTIGT)

    def kopie(self, ziel: Path, erwartet: str) -> bool:
        """SaveCopyAs nach `ziel`, nur wenn Berechnung fertig und Fingerabdruck vorher wie nachher `erwartet`."""
        try:
            app, workbook = self._mappe()
            if workbook is None or _berechnung(app) or fingerabdruck(workbook) != erwartet:
                return False
            workbook.SaveCopyAs(str(ziel))
            return _berechnung(app) is None and fingerabdruck(workbook) == erwartet and Path(ziel).is_file()
        except Exception:
            return False
