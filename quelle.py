"""
quelle.py – Lesequelle der öffentlichen Testtag-Ansicht (P1D)

Liest ausschliesslich über die Supabase Data API (PostgREST) mit dem **Publishable Key** – nur im Header
`apikey`, nie als `Authorization: Bearer`. Ein Secret- oder Service-Role-Key wird abgewiesen. Es gibt keinen
Schreibweg.

Datenfluss: öffentliche Testtage (`testtage`) → Testtag über Slug (oder alte UUID) auflösen → alle Zeilen
`veroeffentlichung` dieses Testtags laden → prüfen (Primärschlüssel eindeutig, `schema_version`, Prüfsumme über
den kanonischen Inhalt) → Tombstones (`inhalt.aktiv == false`), `probe` und unbekannte Bereiche ausblenden →
validierter Gesamtstand (`Stand`). Ein fehlerhafter Ladevorgang liefert nie einen Teilstand.

Archivierte Testtage (P1F) lesen ausschliesslich ihren unveränderlichen Snapshot aus `archiv_snapshots`. Fehlt er
oder ist er ungültig, wird kein Ersatzstand aus `veroeffentlichung` zusammengesetzt.

Prüfsumme, Schema-Version, Programmreihenfolge und Live-Sortierung stammen unverändert aus dem P1B-Modell
(`src/testtag_public/publication_model.py`); es gibt keine abweichende Kopie.

Offline prüfbar: Jeder Aufruf nimmt einen austauschbaren `transport(url, kopf, zeitueberschreitung)`.
"""
from __future__ import annotations

import json
import math
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:  # lokal aus dem Projektstamm und auf Streamlit Community Cloud
    sys.path.insert(0, str(_SRC))

from testtag_public.archive import ArchiveFormatError, verify_archive, verify_snapshot_row  # noqa: E402
from testtag_public.publication_model import (  # noqa: E402
    PROGRAM_ORDER, SCHEMA_VERSION, content_checksum, number_out, sort_live,
)

ZEITUEBERSCHREITUNG = 5.0
PUBLISHABLE_PRAEFIX = "sb_publishable_"
VERBOTENE_NAMEN = {"supabase_secret_key", "supabase_service_role_key", "service_role", "service_role_key",
                   "secret_key", "sb_secret_key"}
FELDER = ("oeffentliche_testtag_id", "bereich", "programm", "ak", "pruefsumme", "stand_am", "inhalt")
TESTTAG_FELDER = ("id", "slug", "titel", "status", "stand_am")
ARCHIV_FELDER = ("oeffentliche_testtag_id", "schema_version", "pruefsumme", "inhalt")

LIVE = "resultate_live"
RANGLISTE = "resultate_rangliste"
STARTLISTE = "startliste"
ZEITPLAN = "zeitplan"
BEREICHE = (LIVE, RANGLISTE, STARTLISTE, ZEITPLAN)
BEREICH_TEXT = {LIVE: "Live", RANGLISTE: "Ranglisten", STARTLISTE: "Startliste", ZEITPLAN: "Zeitplan"}
STATUS_REIHENFOLGE = ("live", "vorbereitet", "archiviert")
STATUS_TEXT = {"live": "Live", "vorbereitet": "Vorbereitet", "archiviert": "Archiv"}
RANGLISTEN = ("gesamt", "technik", "athletik")
RANGLISTE_TEXT = {"gesamt": "Gesamt", "technik": "Technik", "athletik": "Athletik"}
PROGRAMM_TEXT = {"AK5-AK9+": "AK5–AK9+", "AK9-AK12": "AK9–AK12"}
BEREICH_ATHLETIK_TEXT = {"beweglichkeit": "Beweglichkeit", "kraft": "Kraft"}

SLUG_MUSTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")  # wie testtage_slug_check im Schema
UUID_MUSTER = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class QuellenFehler(Exception):
    """Öffentliche Daten nicht ladbar. Neutrale Meldung mit stabilem Code; nie Schlüssel, URL oder Inhalte."""

    def __init__(self, meldung: str, code: str = "NICHT_VERFUEGBAR"):
        super().__init__(meldung)
        self.code = code


class NichtEingerichtet(QuellenFehler):
    def __init__(self, meldung: str = "Für diese Ansicht ist noch kein Zugang hinterlegt."):
        super().__init__(meldung, "NICHT_EINGERICHTET")


@dataclass(frozen=True)
class Zugang:
    """Öffentlicher Lesezugang. Der Schlüssel erscheint nie in `repr()` oder in einer Meldung."""
    url: str
    schluessel: str = field(repr=False)
    zeitueberschreitung: float = ZEITUEBERSCHREITUNG

    def __str__(self) -> str:
        return "Öffentlicher Lesezugang (Schlüssel nicht angezeigt)"


@dataclass(frozen=True)
class Testtag:
    id: str
    slug: str
    titel: str
    status: str
    stand_am: str


@dataclass(frozen=True)
class Datensatz:
    bereich: str
    programm: str
    ak: str
    pruefsumme: str
    stand_am: str
    inhalt: dict

    @property
    def gruppen(self) -> list:
        return list(self.inhalt.get("gruppen") or [])


@dataclass(frozen=True)
class Stand:
    """Vollständig validierter Anzeigestand eines Testtags (nur aktive, unterstützte Zeilen)."""
    testtag: Testtag
    saetze: tuple
    geladen_am: datetime


# ── Zugang und Transport ─────────────────────────────────────────────────────────────────────────────

def lade_zugang(secrets) -> Zugang:
    """URL und **Publishable Key** aus den Streamlit-Secrets; Schreibschlüssel werden abgewiesen."""
    try:
        namen = {str(k).lower() for k in secrets} if secrets else set()
    except Exception:
        namen = set()
    if not namen:
        raise NichtEingerichtet()
    if VERBOTENE_NAMEN & namen:
        raise QuellenFehler("Die öffentliche Ansicht darf keinen Schreibschlüssel erhalten.", "SCHREIBSCHLUESSEL")
    try:
        url = str(secrets["supabase_url"]).strip().rstrip("/")
        schluessel = str(secrets["supabase_publishable_key"]).strip()
    except (KeyError, TypeError):
        raise NichtEingerichtet() from None
    teile = urllib.parse.urlsplit(url)
    if teile.scheme != "https" or not teile.netloc or teile.query or teile.username or teile.password \
            or teile.path not in ("", "/"):
        raise QuellenFehler("Die hinterlegte Adresse ist nicht zulässig (https ohne Pfad).", "ADRESSE")
    if not schluessel:
        raise NichtEingerichtet()
    if not schluessel.startswith(PUBLISHABLE_PRAEFIX) or len(schluessel) <= len(PUBLISHABLE_PRAEFIX) \
            or any(z.isspace() for z in schluessel):
        # Bewusst allgemein: Weder der Wert noch sein Anfang darf in einer Meldung erscheinen.
        raise QuellenFehler("Der hinterlegte Zugang ist kein öffentlicher Leseschlüssel.", "SCHLUESSELFORM")
    zeit = secrets.get("zeitueberschreitung", ZEITUEBERSCHREITUNG) if hasattr(secrets, "get") else ZEITUEBERSCHREITUNG
    if not isinstance(zeit, (int, float)) or isinstance(zeit, bool) or not 0 < float(zeit) <= 60:
        zeit = ZEITUEBERSCHREITUNG
    return Zugang(url, schluessel, float(zeit))


def https_transport(url: str, kopf: dict, zeitueberschreitung: float):
    """Vorgabetransport: genau ein lesender HTTPS-Aufruf mit fester Zeitüberschreitung."""
    if not url.startswith("https://"):
        raise QuellenFehler("Nur HTTPS ist zulässig.", "ADRESSE")
    anfrage = urllib.request.Request(url, headers=kopf, method="GET")
    try:
        with urllib.request.urlopen(anfrage, timeout=zeitueberschreitung) as antwort:
            return antwort.status, antwort.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, ""  # Fehlerantworten werden nie ausgewertet oder angezeigt
        finally:
            e.close()
    except (socket.timeout, TimeoutError):
        raise QuellenFehler("Zeitüberschreitung beim Laden.", "ZEITUEBERSCHREITUNG") from None
    except urllib.error.URLError:
        raise QuellenFehler("Keine Verbindung.", "NETZ") from None
    except OSError:
        raise QuellenFehler("Netzwerkfehler beim Laden.", "NETZ") from None


def _kopf(zugang: Zugang) -> dict:
    return {"apikey": zugang.schluessel, "Accept": "application/json"}  # bewusst ohne Authorization


def _url(zugang: Zugang, tabelle: str, parameter: list[tuple[str, str]]) -> str:
    abfrage = urllib.parse.urlencode(parameter, quote_via=urllib.parse.quote, safe=",")
    return f"{zugang.url}/rest/v1/{urllib.parse.quote(tabelle, safe='')}?{abfrage}"


def _hole(zugang: Zugang, tabelle: str, parameter: list[tuple[str, str]], transport) -> list:
    holen = transport or https_transport
    try:
        status, text = holen(_url(zugang, tabelle, parameter), _kopf(zugang), zugang.zeitueberschreitung)
    except QuellenFehler:
        raise
    except Exception as e:  # nie Exception-Text weitergeben (könnte Adresse oder Inhalte enthalten)
        raise QuellenFehler("Die Daten sind zurzeit nicht verfügbar.", f"NETZ_{type(e).__name__.upper()}") from None
    if status in (401, 403):
        raise QuellenFehler("Zugang abgelehnt.", f"HTTP_{status}")
    if not 200 <= status < 300:
        raise QuellenFehler(f"Die Daten sind zurzeit nicht verfügbar (HTTP {status}).", f"HTTP_{status}")
    try:
        daten = json.loads(text)
    except (ValueError, TypeError):
        raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "JSON") from None
    if not isinstance(daten, list) or not all(isinstance(d, dict) for d in daten):
        raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "FORMAT")
    return daten


# ── Testtage ─────────────────────────────────────────────────────────────────────────────────────────

def zeit(text: str) -> datetime:
    wert = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if wert.tzinfo is None:
        raise ValueError("ohne Zeitzone")
    return wert


def _testtag(zeile: dict) -> Optional[Testtag]:
    try:
        werte = {f: zeile[f] for f in TESTTAG_FELDER}
        if werte["status"] not in STATUS_REIHENFOLGE or not SLUG_MUSTER.match(str(werte["slug"])) \
                or not UUID_MUSTER.match(str(werte["id"])):
            return None
        zeit(werte["stand_am"])
        return Testtag(str(werte["id"]).lower(), werte["slug"], str(werte["titel"] or werte["slug"]),
                       werte["status"], str(werte["stand_am"]))
    except (KeyError, ValueError, TypeError):
        return None


def sortiere_testtage(testtage) -> list[Testtag]:
    """Live vor vorbereitet vor Archiv; innerhalb der Gruppe der zuletzt aktualisierte zuerst."""
    nach_zeit = sorted(testtage, key=lambda t: (zeit(t.stand_am), t.slug), reverse=True)
    return sorted(nach_zeit, key=lambda t: STATUS_REIHENFOLGE.index(t.status))


def lade_testtage(zugang: Zugang, *, transport=None) -> list[Testtag]:
    zeilen = _hole(zugang, "testtage", [("select", ",".join(TESTTAG_FELDER))], transport)
    return sortiere_testtage(t for t in (_testtag(z) for z in zeilen) if t is not None)


def pruefe_kennung(kennung: str) -> tuple[str, str]:
    """`?t=`: Slug (bevorzugt) oder alte 36-stellige UUID. Liefert (Spalte, Wert) für einen sicheren Filter."""
    wert = str(kennung or "").strip()
    if UUID_MUSTER.match(wert):
        return "id", wert.lower()
    if SLUG_MUSTER.match(wert):
        return "slug", wert
    raise QuellenFehler("Die Adresse nennt keinen gültigen Testtag.", "KENNUNG")


def finde_testtag(zugang: Zugang, kennung: str, *, transport=None) -> Optional[Testtag]:
    """Öffentlich sichtbaren Testtag über Slug oder UUID auflösen; None, wenn unbekannt oder nicht öffentlich."""
    spalte, wert = pruefe_kennung(kennung)
    zeilen = _hole(zugang, "testtage", [("select", ",".join(TESTTAG_FELDER)), (spalte, f"eq.{wert}")], transport)
    gefunden = [t for t in (_testtag(z) for z in zeilen) if t is not None]
    if len(gefunden) > 1:
        raise QuellenFehler("Die Daten sind zurzeit nicht eindeutig.", "TESTTAG_MEHRDEUTIG")
    return gefunden[0] if gefunden else None


def oeffentliche_url(basis: str, slug: str) -> str:
    """Öffentliche Adresse eines Testtags – immer mit dem Slug (z. B. für den QR-Code)."""
    if not SLUG_MUSTER.match(str(slug or "")):
        raise QuellenFehler("Ungültiger Slug.", "KENNUNG")
    adresse = str(basis or "").strip().rstrip("/")
    if not adresse.startswith("https://"):
        raise QuellenFehler("Die öffentliche Basisadresse muss mit https:// beginnen.", "ADRESSE")
    return f"{adresse}/?t={urllib.parse.quote(slug, safe='')}"


# ── Publikationszeilen ───────────────────────────────────────────────────────────────────────────────

def ist_tombstone(inhalt) -> bool:
    return isinstance(inhalt, dict) and inhalt.get("aktiv") is False


def lade_stand(zugang: Zugang, testtag: Testtag, *, transport=None, jetzt: Optional[datetime] = None) -> Stand:
    """Lädt und prüft den Gesamtstand eines Testtags. Jeder Fehler verwirft den ganzen Ladevorgang.

    Archivierte Testtage werden ausschliesslich aus ihrem unveränderlichen Snapshot gelesen (`lade_archiv`)."""
    if testtag.status == "archiviert":
        return lade_archiv(zugang, testtag, transport=transport, jetzt=jetzt)
    parameter = [("select", ",".join(FELDER)), ("oeffentliche_testtag_id", f"eq.{testtag.id}")]
    zeilen = _hole(zugang, "veroeffentlichung", parameter, transport)
    gesehen = set()
    saetze = []
    for zeile in zeilen:
        if any(f not in zeile for f in FELDER):
            raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "FORMAT")
        schluessel = (str(zeile["oeffentliche_testtag_id"]).lower(), zeile["bereich"], zeile["programm"] or "",
                      zeile["ak"] or "")
        if schluessel in gesehen:
            raise QuellenFehler("Der veröffentlichte Stand ist nicht eindeutig.", "DOPPELT")
        gesehen.add(schluessel)
        if schluessel[0] != testtag.id:
            raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "FREMDE_ZEILE")
        if zeile["bereich"] not in BEREICHE:  # z. B. probe: nie anzeigen
            continue
        inhalt = zeile["inhalt"]
        if isinstance(inhalt, str):
            try:
                inhalt = json.loads(inhalt)
            except ValueError:
                raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "JSON") from None
        if not isinstance(inhalt, dict) or inhalt.get("schema_version") != SCHEMA_VERSION:
            raise QuellenFehler("Der veröffentlichte Stand hat ein unbekanntes Format.", "SCHEMA")
        if ist_tombstone(inhalt):
            continue
        if not isinstance(zeile["pruefsumme"], str) or content_checksum(inhalt) != zeile["pruefsumme"]:
            raise QuellenFehler("Der veröffentlichte Stand ist unvollständig übertragen.", "PRUEFSUMME")
        if not isinstance(inhalt.get("gruppen"), list):
            raise QuellenFehler("Die Daten sind zurzeit nicht lesbar.", "FORMAT")
        saetze.append(Datensatz(zeile["bereich"], schluessel[2], schluessel[3], zeile["pruefsumme"],
                                str(zeile["stand_am"]), inhalt))
    return Stand(testtag, tuple(saetze), jetzt or datetime.now(timezone.utc))


def lade_archiv(zugang: Zugang, testtag: Testtag, *, transport=None, jetzt: Optional[datetime] = None) -> Stand:
    """Archivstand nur aus `archiv_snapshots`; nie aus den veränderlichen Zeilen in `veroeffentlichung`.

    Geprüft werden Testtag-ID, Archiv-Schema, Prüfsumme über den kanonischen Inhalt sowie jeder Eintrag
    (bekannter Bereich, keine Probe, kein Tombstone, eindeutiger Schlüssel, Publikations-Schema, Prüfsumme)."""
    parameter = [("select", ",".join(ARCHIV_FELDER)), ("oeffentliche_testtag_id", f"eq.{testtag.id}")]
    zeilen = _hole(zugang, "archiv_snapshots", parameter, transport)
    if not zeilen:
        raise QuellenFehler("Der Archivstand ist zurzeit nicht verfügbar.", "ARCHIV_FEHLT")
    if len(zeilen) > 1:
        raise QuellenFehler("Der Archivstand ist nicht eindeutig.", "ARCHIV_DOPPELT")
    try:
        inhalt = verify_snapshot_row(zeilen[0], testtag.id)
        eintraege = verify_archive(inhalt, testtag.id)
    except ArchiveFormatError as fehler:
        raise QuellenFehler("Der Archivstand ist nicht lesbar.", f"ARCHIV_{fehler.code}") from None
    saetze = tuple(Datensatz(e["bereich"], e["programm"] or "", e["ak"] or "", e["pruefsumme"], str(e["stand_am"]),
                             e["inhalt"]) for e in eintraege)
    return Stand(testtag, saetze, jetzt or datetime.now(timezone.utc))


# ── Auswahl (reine Anzeigelogik, keine Fachberechnung) ───────────────────────────────────────────────

def _programm_rang(programm: str) -> tuple:
    return (PROGRAM_ORDER.index(programm) if programm in PROGRAM_ORDER else len(PROGRAM_ORDER), programm)


def _ak_rang(ak: str) -> tuple:
    zahl = re.search(r"\d+", ak or "")
    return (int(zahl.group()) if zahl else 999, ak)


def programme(stand: Stand, bereich: str) -> list[str]:
    return sorted({d.programm for d in stand.saetze if d.bereich == bereich and d.programm}, key=_programm_rang)


def altersklassen(stand: Stand, bereich: str, programm: Optional[str] = None) -> list[str]:
    return sorted({d.ak for d in stand.saetze if d.bereich == bereich and d.ak
                   and (programm is None or d.programm == programm)}, key=_ak_rang)


def live_resultate(stand: Stand, programm: Optional[str] = None, ak: Optional[str] = None) -> list[dict]:
    """Sichtbare Resultate, neueste Änderung zuerst (Sortierung des P1B-Modells)."""
    resultate = []
    for d in stand.saetze:
        if d.bereich != LIVE or (programm and d.programm != programm) or (ak and d.ak != ak):
            continue
        for gruppe in d.gruppen:
            resultate.extend(r for r in gruppe.get("resultate") or [] if isinstance(r, dict))
    return sort_live(resultate)


def rangliste(stand: Stand, programm: str, ak: str, art: str) -> list[dict]:
    for d in stand.saetze:
        if d.bereich == RANGLISTE and d.programm == programm and d.ak == ak:
            for gruppe in d.gruppen:
                if gruppe.get("liste") == art:
                    return list(gruppe.get("eintraege") or [])
    return []


def ranglisten_struktur(stand: Stand, programm: str, ak: str) -> Optional[dict]:
    """Vorgesehene Geräte, Linien und Athletiktests der AK (ab Publikation mit `struktur`), sonst None."""
    for d in stand.saetze:
        if d.bereich == RANGLISTE and d.programm == programm and d.ak == ak:
            struktur = d.inhalt.get("struktur")
            return struktur if isinstance(struktur, dict) else None
    return None


TOLERANZ = 1e-6


def _summe(werte: list) -> Optional[float]:
    return number_out(math.fsum(float(w) for w in werte)) if werte else None


def _natuerlich(code: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", str(code)) if t)


def _abgeleitete_struktur(alle: list[dict]) -> dict:
    """Ersatz für ältere Stände ohne `struktur`: nur die Linien/Tests, die in der AK veröffentlicht sind."""
    geraete: dict[str, dict[str, dict]] = {}
    athletik: dict[str, dict[str, dict]] = {"beweglichkeit": {}, "kraft": {}}
    for r in alle:
        if r.get("bereich") == "technik":
            geraete.setdefault(str(r.get("geraet")), {}).setdefault(str(r.get("code")), {
                "code": r.get("code"), "anzeige": r.get("anzeige")})
        elif r.get("bereich") in athletik:
            athletik[r["bereich"]].setdefault(str(r.get("code")), {"code": r.get("code"), "test": r.get("test")})
    ordnung = sorted(geraete, key=lambda g: min(_natuerlich(c) for c in geraete[g]))
    return {"geraete": [{"geraet": g, "linien": [geraete[g][c] for c in sorted(geraete[g], key=_natuerlich)]}
                        for g in ordnung],
            **{b: [t[c] for c in sorted(t, key=_natuerlich)] for b, t in athletik.items()}}


def turner_details(eintrag: dict, eigene: list[dict], struktur: Optional[dict], alle: list[dict]) -> Optional[dict]:
    """Aufschlüsselung eines Ranglisteneintrags aus seinen veröffentlichten Einzelwertungen (reine Anzeige).

    Keine Rangberechnung: Totale stammen aus dem Ranglisteneintrag. Gerätetotale sind Summen der Linienpunkte (T)
    dieses Geräts. Fehlt ein vorgesehenes Resultat, ist der Wert None („–“); eine Nullwertung bleibt 0. Passt die
    Aufschlüsselung nicht exakt zu den veröffentlichten Totalen, wird None geliefert (keine widersprüchliche Anzeige).
    """
    struktur = struktur if isinstance(struktur, dict) else _abgeleitete_struktur(alle)
    technik = {str(r.get("code")): r for r in eigene if r.get("bereich") == "technik"}
    athletik = {b: {str(r.get("code")): r for r in eigene if r.get("bereich") == b} for b in ("beweglichkeit", "kraft")}
    for teil, werte in (("technik", [r.get("T") for r in technik.values()]),
                        *((b, [r.get("punkte") for r in athletik[b].values()]) for b in athletik)):
        try:
            if abs(math.fsum(float(w) for w in werte) - float(eintrag.get(teil) or 0)) > TOLERANZ:
                return None
        except (TypeError, ValueError):
            return None
    if sum(1 for r in eigene if r.get("bereich") == "technik") != len(technik) or \
            sum(1 for r in eigene if r.get("bereich") in athletik) != sum(len(t) for t in athletik.values()):
        return None  # doppelte Codes: nicht eindeutig zuordenbar

    geraete, zugeordnet = [], set()
    for geraet in struktur.get("geraete") or []:
        linien = []
        alternativen = [li for li in geraet.get("linien") or [] if li.get("alternative")]
        gewaehlt = any(str(li.get("code")) in technik for li in alternativen)
        for linie in geraet.get("linien") or []:
            code = str(linie.get("code"))
            r = technik.get(code)
            if linie.get("alternative") and gewaehlt and r is None:
                continue  # die andere Sprung-Alternative wurde gewählt
            zugeordnet.add(code)
            linien.append({"anzeige": linie.get("anzeige"), "S": r.get("S") if r else None,
                           "T": r.get("T") if r else None, "alternative": bool(linie.get("alternative")) and r is None})
        werte = [technik[str(li.get("code"))]["T"] for li in geraet.get("linien") or [] if str(li.get("code")) in technik]
        geraete.append({"geraet": geraet.get("geraet"), "total": _summe(werte), "linien": linien})
    rest = [r for c, r in technik.items() if c not in zugeordnet]  # nicht in der Gliederung: trotzdem zeigen
    for r in sorted(rest, key=lambda r: _natuerlich(r.get("code"))):
        ziel = next((g for g in geraete if g["geraet"] == r.get("geraet")), None)
        if ziel is None:
            ziel = {"geraet": r.get("geraet"), "total": None, "linien": []}
            geraete.append(ziel)
        ziel["linien"].append({"anzeige": r.get("anzeige"), "S": r.get("S"), "T": r.get("T"), "alternative": False})
        ziel["total"] = _summe([*([ziel["total"]] if ziel["total"] is not None else []), r.get("T")])

    out = {"geraete": geraete}
    for bereich, tests in athletik.items():
        zeilen = []
        for test in struktur.get(bereich) or []:
            r = tests.get(str(test.get("code")))
            zeilen.append({"test": test.get("test"), "punkte": r.get("punkte") if r else None})
        bekannt = {str(t.get("code")) for t in struktur.get(bereich) or []}
        zeilen += [{"test": r.get("test"), "punkte": r.get("punkte")}
                   for c, r in sorted(tests.items(), key=lambda kv: _natuerlich(kv[0])) if c not in bekannt]
        out[bereich] = zeilen
    return out


def startlisten(stand: Stand) -> list[tuple[str, list[dict]]]:
    """(Programm, Gruppen je Startgerät) in Programmreihenfolge; leere Gruppen entfallen."""
    out = []
    for d in sorted((d for d in stand.saetze if d.bereich == STARTLISTE), key=lambda d: _programm_rang(d.programm)):
        gruppen = [g for g in d.gruppen if g.get("teilnehmer")]
        if gruppen:
            out.append((d.programm, gruppen))
    return out


def zeitplan(stand: Stand) -> list[dict]:
    """Gemeinsamer Tagesablauf in der vom Publisher gelieferten Reihenfolge; leere Blöcke entfallen."""
    for d in stand.saetze:
        if d.bereich == ZEITPLAN:
            return [g for g in d.gruppen if g.get("zeilen")]
    return []


def hinweis(stand: Stand, bereich: str, programm: str, ak: str) -> str:
    for d in stand.saetze:
        if d.bereich == bereich and d.programm == programm and d.ak == ak:
            return str(d.inhalt.get("hinweis") or "")
    return ""


# ── Zeit in Europe/Zurich ────────────────────────────────────────────────────────────────────────────

def _letzter_sonntag(jahr: int, monat: int) -> datetime:
    tag = datetime(jahr, monat + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if monat < 12 else \
        datetime(jahr, 12, 31, tzinfo=timezone.utc)
    return tag - timedelta(days=(tag.weekday() + 1) % 7)


def nach_zuerich(wert: datetime) -> datetime:
    """UTC → Europe/Zurich (zoneinfo; ohne Zeitzonendaten Rückfall auf die EU-Sommerzeitregel)."""
    try:
        from zoneinfo import ZoneInfo
        return wert.astimezone(ZoneInfo("Europe/Zurich"))
    except Exception:
        utc = wert.astimezone(timezone.utc)
        beginn = _letzter_sonntag(utc.year, 3).replace(hour=1)
        ende = _letzter_sonntag(utc.year, 10).replace(hour=1)
        stunden = 2 if beginn <= utc < ende else 1
        return utc.astimezone(timezone(timedelta(hours=stunden)))


def zeit_text(text: str, *, mit_datum: bool = False) -> str:
    try:
        lokal = nach_zuerich(zeit(text))
    except (ValueError, TypeError):
        return ""
    return lokal.strftime("%d.%m.%Y %H:%M" if mit_datum else "%H:%M:%S")
