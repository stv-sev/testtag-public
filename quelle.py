"""
quelle.py – Lesequelle der öffentlichen Testtag-Ansicht (Etappe E15b)

Teil der **eigenständigen** öffentlichen App. Sie ist vollständig vom internen Kern getrennt: Dieses Modul
importiert weder `speicherung` noch `bedienung`, `veroeffentlichung` oder irgendein anderes Modul aus
`tool/testtag/`, kennt die interne Testtag-ID nicht und besitzt keinen Schreibweg.

Gelesen wird ausschliesslich die veröffentlichte Tabelle über die REST-Schnittstelle – mit dem **Publishable
Key** aus den Streamlit-Cloud-Secrets, niemals mit einem Secret- oder Service-Role-Key. Zusätzlich beschränken
Row Level Security und Datenbankrechte den öffentlichen Zugriff auf `SELECT`; die App ist damit technisch
nur lesend.

Offline prüfbar: Jeder Aufruf nimmt einen austauschbaren `transport`. Die Vorgabe spricht HTTPS, die Tests
speisen synthetische Antworten ein – dadurch entsteht in den Tests **kein** Netzverkehr.
"""
from __future__ import annotations

import json
import socket
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

TABELLE = "veroeffentlichung"
ZEITUEBERSCHREITUNG = 5.0
# Die öffentliche Ansicht nimmt ausschliesslich den Publishable Key. Ein Schreibschlüssel (`sb_secret_…`) und
# ältere JWT-Schlüssel (`eyJ…`) werden abgelehnt – unabhängig davon, unter welchem Namen sie hinterlegt sind.
PUBLISHABLE_PRAEFIX = "sb_publishable_"
FELDER = ("oeffentliche_testtag_id", "bereich", "programm", "ak", "pruefsumme", "stand_am", "inhalt")

STARTLISTE = "startliste"
ZEITPLAN = "zeitplan"
LIVE = "resultate_live"
RANGLISTE = "resultate_rangliste"
BEREICHE = (LIVE, RANGLISTE, STARTLISTE, ZEITPLAN)
BEREICH_TEXT = {LIVE: "Live", RANGLISTE: "Rangliste", STARTLISTE: "Startliste", ZEITPLAN: "Zeitplan"}
ART_TEXT = {"live": "Live-Wertungen", "zwischenstand": "Zwischenrangliste", "endstand": "Endstand",
            "freigegeben": "veröffentlicht"}


class QuellenFehler(Exception):
    """Die öffentlichen Daten konnten nicht geladen werden; die Meldung nennt nie Schlüssel oder URL."""
    pass


class NichtEingerichtet(QuellenFehler):
    """Es ist kein Zugang hinterlegt (Streamlit-Secrets fehlen)."""
    pass


@dataclass(frozen=True)
class Zugang:
    """Öffentlicher Lesezugang. Der Schlüssel erscheint nie in `repr()` oder in einer Meldung."""
    url: str
    schluessel: str = field(repr=False)
    zeitueberschreitung: float = ZEITUEBERSCHREITUNG

    def __str__(self) -> str:
        return "Öffentlicher Lesezugang (Schlüssel nicht angezeigt)"


@dataclass(frozen=True)
class Datensatz:
    """Ein veröffentlichter Datensatz: Bereich bzw. Strom, Programm, AK, Prüfsumme, Stand und Inhalt."""
    bereich: str
    programm: str
    ak: str
    pruefsumme: str
    stand_am: str
    inhalt: dict

    @property
    def art(self) -> str:
        return self.inhalt.get("art") or ""

    @property
    def kopf(self) -> dict:
        gruppen = self.inhalt.get("gruppen") or []
        return gruppen[0].get("kopf", {}) if gruppen else {}

    @property
    def zeilen(self) -> list:
        return [z for g in (self.inhalt.get("gruppen") or []) for z in (g.get("zeilen") or [])]


def lade_zugang(secrets) -> Zugang:
    """Liest URL und **Publishable Key** aus den Streamlit-Secrets.

    Ein Secret- oder Service-Role-Key wird ausdrücklich abgelehnt: In der öffentlichen App darf er nie liegen."""
    if not secrets:
        raise NichtEingerichtet("Für diese Ansicht ist noch kein Zugang hinterlegt.")
    try:
        url = str(secrets["supabase_url"]).rstrip("/")
        schluessel = str(secrets["supabase_publishable_key"])
    except (KeyError, TypeError):
        raise NichtEingerichtet("Für diese Ansicht ist noch kein Zugang hinterlegt.") from None
    verboten = {"supabase_secret_key", "supabase_service_role_key", "service_role", "secret_key"}
    if verboten & {str(k).lower() for k in secrets}:
        raise QuellenFehler("Die öffentliche Ansicht darf keinen Schreibschlüssel erhalten.")
    if not url.startswith("https://"):
        raise QuellenFehler("Die hinterlegte Adresse ist nicht sicher (https).")
    if not schluessel.strip():
        raise NichtEingerichtet("Für diese Ansicht ist noch kein Zugang hinterlegt.")
    if not schluessel.startswith(PUBLISHABLE_PRAEFIX):
        # Bewusst allgemein: Weder der Wert noch sein Anfang darf in einer Meldung erscheinen.
        raise QuellenFehler("Der hinterlegte Zugang hat nicht die erwartete Form eines öffentlichen "
                            "Leseschlüssels.")
    zeit = secrets.get("zeitueberschreitung", ZEITUEBERSCHREITUNG) if hasattr(secrets, "get") \
        else ZEITUEBERSCHREITUNG
    if not isinstance(zeit, (int, float)) or isinstance(zeit, bool) or not 0 < float(zeit) <= 60:
        zeit = ZEITUEBERSCHREITUNG
    return Zugang(url, schluessel, float(zeit))


def https_transport(url: str, kopf: dict, zeitueberschreitung: float):
    """Vorgabetransport: genau ein lesender HTTPS-Aufruf mit fester Zeitüberschreitung."""
    if not url.startswith("https://"):
        raise QuellenFehler("Nur HTTPS ist zulässig.")
    anfrage = urllib.request.Request(url, headers=kopf, method="GET")
    try:
        with urllib.request.urlopen(anfrage, timeout=zeitueberschreitung) as antwort:
            return antwort.status, antwort.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
    except socket.timeout:
        raise QuellenFehler("Zeitüberschreitung beim Laden.") from None
    except urllib.error.URLError:
        raise QuellenFehler("Keine Verbindung.") from None
    except OSError:
        raise QuellenFehler("Netzwerkfehler beim Laden.") from None


def _pruefe_kennung(oeffentliche_id: str) -> str:
    kennung = str(oeffentliche_id or "").strip()
    erlaubt = set("0123456789abcdefABCDEF-")
    if len(kennung) != 36 or set(kennung) - erlaubt:
        raise QuellenFehler("Die Adresse nennt keinen gültigen Testtag.")
    return kennung


def lade(zugang: Zugang, oeffentliche_id: str, *, transport=None) -> tuple:
    """Lädt alle veröffentlichten Datensätze **eines** Testtags (nur lesend, ohne Rückkanal)."""
    kennung = _pruefe_kennung(oeffentliche_id)
    holen = transport or https_transport
    felder = ",".join(FELDER)
    url = (f"{zugang.url}/rest/v1/{TABELLE}?oeffentliche_testtag_id=eq."
           f"{urllib.parse.quote(kennung, safe='')}&select={felder}")
    kopf = {"apikey": zugang.schluessel, "Authorization": f"Bearer {zugang.schluessel}",
            "Accept": "application/json"}
    status, text = holen(url, kopf, zugang.zeitueberschreitung)
    if status in (401, 403):
        raise QuellenFehler("Zugang abgelehnt.")
    if status == 404:
        raise QuellenFehler("Die Daten sind zurzeit nicht verfügbar.")
    if not 200 <= status < 300:
        raise QuellenFehler(f"Die Daten sind zurzeit nicht verfügbar (HTTP {status}).")
    try:
        zeilen = json.loads(text)
    except ValueError:
        raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.") from None
    if not isinstance(zeilen, list):
        raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.")
    saetze = []
    for zeile in zeilen:
        if not isinstance(zeile, dict) or any(f not in zeile for f in FELDER):
            raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.")
        inhalt = zeile["inhalt"]
        if isinstance(inhalt, str):                      # je nach Rückgabeform der Schnittstelle
            inhalt = json.loads(inhalt)
        saetze.append(Datensatz(zeile["bereich"], zeile["programm"] or "", zeile["ak"] or "",
                                zeile["pruefsumme"], zeile["stand_am"], inhalt))
    return tuple(sorted(saetze, key=lambda d: (BEREICHE.index(d.bereich) if d.bereich in BEREICHE else 9,
                                               d.programm, d.ak)))


# ── Auswahl und Suche (reine Anzeigelogik, keine Fachberechnung) ─────────────

def programme(saetze, bereich: str) -> tuple:
    """Programmbezeichnungen der vorhandenen Datensätze eines Bereichs (Anzeigename aus dem Kopf)."""
    namen = {}
    for d in saetze:
        if d.bereich == bereich and d.programm:
            namen.setdefault(d.programm, d.kopf.get("programm") or d.programm)
    return tuple(sorted(namen.items(), key=lambda p: p[1]))


def altersklassen(saetze, bereich: str, programm: str) -> tuple:
    return tuple(sorted({d.ak for d in saetze if d.bereich == bereich and d.programm == programm and d.ak}))


def waehle(saetze, bereich: str, programm: str = "", ak: str = "") -> Optional[Datensatz]:
    for d in saetze:
        if d.bereich == bereich and d.programm == (programm or "") and d.ak == (ak or ""):
            return d
    return None


def _normal(text) -> str:
    """Vergleichsform für die Suche: ohne Gross-/Kleinschreibung und ohne Akzente."""
    zerlegt = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(z for z in zerlegt if not unicodedata.combining(z)).casefold().strip()


def suche(zeilen, text: str) -> list:
    """Filtert Anzeigezeilen über Name, Vorname, Verein und Riege; leere Eingabe lässt alles stehen."""
    begriffe = [_normal(t) for t in str(text or "").split() if t.strip()]
    if not begriffe:
        return list(zeilen)
    gefunden = []
    for zeile in zeilen:
        heuhaufen = " ".join(_normal(zeile.get(f, "")) for f in ("vorname", "name", "verein", "riege"))
        if all(b in heuhaufen for b in begriffe):
            gefunden.append(zeile)
    return gefunden


def oeffentliche_url(basis: str, oeffentliche_id: str) -> str:
    """Stabile öffentliche Adresse eines Testtags: **eine** App-URL, der Testtag folgt als Parameter `t`.

    Genau diese Adresse steht später im QR-Code. Die interne Testtag-ID erscheint nie darin."""
    kennung = _pruefe_kennung(oeffentliche_id)
    adresse = str(basis or "").strip().rstrip("/")
    if not adresse.startswith("https://"):
        raise QuellenFehler("Die öffentliche Basisadresse muss mit https:// beginnen.")
    return f"{adresse}/?t={kennung}"
