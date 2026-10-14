"""P1B: reines Publikationsmodell (ohne Excel, Netzwerk oder Datenbank).

Eingabe sind die aus Excel gelesenen Rohdaten je Programm (`ProgramSource`, siehe `excel_publication`).
Daraus entsteht ein kanonischer, JSON-serialisierbarer Publikationsstand:

- Public-Parameter, Athletikmodi und Struktur werden validiert; jeder Struktur- oder Datenkonflikt bricht
  die gesamte Verarbeitung ab (`PublicationError` mit stabilen Fehlercodes, ohne Personendaten).
- Sichtbar sind Technik- und Vor-Ort-Athletikresultate nur bis zur freigegebenen Rotation (`Public!B5`);
  extern erfasste Athletikwerte haben keine Rotation und sind immer sichtbar.
- Summen und Ranglisten werden ausschliesslich aus den sichtbaren Einzelwertungen berechnet
  (Excel-Totale werden nie übernommen). Gleichstand: gleicher Rang, nächster Rang übersprungen (1, 2, 2, 4).
- Live-Änderungen entstehen durch Vergleich mit dem vorherigen Stand (`merge_live`).
- `publication_rows` bereitet künftige Zeilen für `veroeffentlichung` vor (ohne Datenbank-ID, ohne Upload).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .excel_probe import ProbeError
from .supabase_database import canonical_json, canonical_sha256

SCHEMA_VERSION = "publikation-1"

MODUS_VOR_ORT = "vor_ort"
MODUS_EXTERN = "extern"

BEREICH_TECHNIK = "technik"
BEREICH_BEWEGLICHKEIT = "beweglichkeit"
BEREICH_KRAFT = "kraft"
ATHLETIK_BEREICHE = (BEREICH_BEWEGLICHKEIT, BEREICH_KRAFT)
# Gerätecodes der Athletik im Testprogramm (ç4 = Beweglichkeit, ç9 = Kraft).
ATHLETIK_GERAETECODE = {BEREICH_BEWEGLICHKEIT: 4, BEREICH_KRAFT: 9}
ATHLETIK_STATION = "Beweglichkeit"
SPRUNG = "Sprung"

QUELLE_STATION = "station"
# Einteilungscodes im Blatt Startliste (`@ID°Rotation çGerät ^Startposition`); die Zeichen stehen in der Legende.
EINTEILUNG_FELDER = ("id_athlete", "id_rotation", "id_apparatus", "id_position")
QUELLE_DIREKT = "direkt"

STATUS_GEWERTET = "gewertet"
END_TOLERANCE = 1e-6
MAX_ISSUES_IN_MESSAGE = 25
VOLATILE_KEYS = frozenset({"geaendert_am"})


@dataclass(frozen=True)
class ProgramDefinition:
    name: str
    rotationen: int
    athletik_modi: tuple[str, ...]
    beweglichkeit_tests: int
    kraft_tests: int
    sprung_regel: bool  # AK9: Sprung 1/2 aus Linie 1/2; AK10–AK12: Pflichtsprung + genau eine Alternative


PROGRAMS: dict[str, ProgramDefinition] = {
    "AK5-AK9+": ProgramDefinition("AK5-AK9+", 8, (MODUS_VOR_ORT, MODUS_EXTERN), 7, 5, False),
    "AK9-AK12": ProgramDefinition("AK9-AK12", 7, (MODUS_EXTERN,), 10, 14, True),
}
PROGRAM_ORDER = tuple(PROGRAMS)


# ── Fehler ───────────────────────────────────────────────────────────────────────────────────────────

class Code:
    PUBLIC_UNGUELTIG = "PUBLIC_UNGUELTIG"
    EVENT_WIDERSPRUCH = "EVENT_WIDERSPRUCH"
    PROGRAMM_UNBEKANNT = "PROGRAMM_UNBEKANNT"
    PROGRAMM_DOPPELT = "PROGRAMM_DOPPELT"
    ATHLETIKMODUS_UNGUELTIG = "ATHLETIKMODUS_UNGUELTIG"
    ATHLETIK_BEIDE_QUELLEN = "ATHLETIK_BEIDE_QUELLEN"
    ATHLETIK_INAKTIVE_QUELLE = "ATHLETIK_INAKTIVE_QUELLE"
    TEILNEHMER_UNBEKANNT = "TEILNEHMER_UNBEKANNT"
    TEILNEHMER_DOPPELT = "TEILNEHMER_DOPPELT"
    TEILNEHMER_UNGUELTIG = "TEILNEHMER_UNGUELTIG"
    FORMELRESULTAT_FEHLT = "FORMELRESULTAT_FEHLT"
    EXCEL_FEHLERWERT = "EXCEL_FEHLERWERT"
    ROTATION_UNGUELTIG = "ROTATION_UNGUELTIG"
    LINIE_NICHT_VORGESEHEN = "LINIE_NICHT_VORGESEHEN"
    SPRUNG_DOPPELTE_ALTERNATIVE = "SPRUNG_DOPPELTE_ALTERNATIVE"
    ERGEBNIS_ID_DOPPELT = "ERGEBNIS_ID_DOPPELT"
    ENDWERT_INKONSISTENT = "ENDWERT_INKONSISTENT"
    WERT_UNGUELTIG = "WERT_UNGUELTIG"
    STRUKTUR_UNGUELTIG = "STRUKTUR_UNGUELTIG"
    EINTEILUNG_UNGUELTIG = "EINTEILUNG_UNGUELTIG"
    SUBDIVISION_UNGUELTIG = "SUBDIVISION_UNGUELTIG"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


class PublicationError(ProbeError):
    """Struktur- oder Datenkonflikt: kein Publikationsstand. Meldungen enthalten keine Personendaten."""

    exit_code = 4

    def __init__(self, issues: Iterable[Issue]):
        self.issues = list(issues) or [Issue(Code.STRUKTUR_UNGUELTIG, "Unbekannter Validierungsfehler.")]
        lines = [f"[{i.code}] {i.message}" for i in self.issues[:MAX_ISSUES_IN_MESSAGE]]
        if len(self.issues) > MAX_ISSUES_IN_MESSAGE:
            lines.append(f"… und {len(self.issues) - MAX_ISSUES_IN_MESSAGE} weitere Meldungen.")
        super().__init__("\n".join(lines))

    @property
    def code(self) -> str:
        return self.issues[0].code

    @property
    def codes(self) -> list[str]:
        return sorted({i.code for i in self.issues})


class Issues:
    def __init__(self) -> None:
        self.items: list[Issue] = []

    def add(self, code: str, message: str) -> None:
        self.items.append(Issue(code, message))

    def __bool__(self) -> bool:
        return bool(self.items)

    def raise_if_any(self) -> None:
        if self.items:
            raise PublicationError(self.items)


# ── Rohdaten aus Excel ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceInfo:
    filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Station:
    name: str
    geraet_code: int  # GerID (= Gerätecode im Testprogramm)


@dataclass(frozen=True)
class LineDefinition:
    geraet_code: int
    nummer: int
    name: str
    versatz_min: Optional[int] = None  # Spaltenindex in akMinMax (1 = AK-Spalte)
    versatz_max: Optional[int] = None


@dataclass(frozen=True)
class RawParticipant:
    excel_id: int
    row: int  # Zeile in Anmeldung, nur für Diagnosemeldungen
    name: Any
    vorname: Any
    jahrgang: Any
    verein: Any
    ak: Any
    ak_kontr: Any
    startgeraet: Any


@dataclass(frozen=True)
class StartlistEntry:
    startgeraet: str
    position: Any
    excel_id: int
    row: int
    codes: tuple = ()  # je Rotationsspalte (Rotation laut Kopf, Gerät laut Kopf, Codewert, Zelle)


@dataclass(frozen=True)
class ScoreSlot:
    """Ein Ergebnisplatz einer Person in `Noteneingabe` (Rohwerte, noch nicht validiert)."""

    excel_id: int
    bereich: str  # technik | beweglichkeit | kraft
    quelle: str  # station | direkt
    geraet: str  # Gerät (Technik) bzw. Bereichsbezeichnung (Athletik)
    nummer: int  # Linie (Technik) bzw. Testnummer (Athletik)
    rotation: Any = None
    sks: Any = None
    tal: Any = None
    end: Any = None
    zelle: str = ""  # z. B. "Noteneingabe!N12" (Endwert), nur Diagnose


@dataclass(frozen=True)
class DirectField:
    bereich: str
    nummer: int
    name: str
    spalte: str


@dataclass(frozen=True)
class OnsiteBlock:
    bereich: str
    nummer: int
    bezeichnung: str
    spalte: str


@dataclass
class ProgramSource:
    public: dict
    source: SourceInfo
    stations: list[Station]
    line_definitions: list[LineDefinition]
    ak_minmax: dict[int, tuple]
    participants: list[RawParticipant]
    startliste: list[StartlistEntry]
    startliste_sections: list[str]
    zeitplan: list[dict]
    noten_rows: list[tuple[int, int]]  # (Excel-ID, Zeile) in Noteneingabe
    slots: list[ScoreSlot]
    direct_fields: list[DirectField]
    onsite_blocks: list[OnsiteBlock]
    technik_blocks: list[tuple[str, int, str]] = field(default_factory=list)  # (Gerät, Linie, Spalte)
    einteilung_zeichen: Optional[dict] = None  # Legende der Einteilungscodes im Blatt Startliste


# ── Normalisierung und öffentlicher Schlüssel ────────────────────────────────────────────────────────

_SPACES = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """Anzeigetext: Leerzeichen am Rand entfernen, Mehrfachleerzeichen zusammenfassen (Schreibweise bleibt)."""
    if value is None:
        return ""
    return _SPACES.sub(" ", str(value)).strip()


def normalize_key_text(value: Any) -> str:
    """Schlüsseltext: Unicode NFKC, Leerzeichen bereinigt, Gross-/Kleinschreibung vereinheitlicht."""
    return clean_text(unicodedata.normalize("NFKC", "" if value is None else str(value))).casefold()


def participant_key(slug: str, programm: str, name: Any, vorname: Any, jahrgang: Any, verein: Any) -> str:
    parts = [normalize_key_text(slug), normalize_key_text(programm), normalize_key_text(name),
             normalize_key_text(vorname), normalize_key_text(jahrgang), normalize_key_text(verein)]
    return canonical_sha256(parts)


def number_out(value: float) -> int | float:
    """Stabile Zahlendarstellung: auf 6 Stellen gerundet, ganzzahlige Werte als int."""
    rounded = round(float(value), 6) + 0.0
    if rounded.is_integer():
        return int(rounded)
    return rounded


def utc_text(value: datetime | str) -> str:
    if isinstance(value, str):
        value = parse_utc(value)
    if value.tzinfo is None:
        raise ValueError("Beobachtungszeitpunkt braucht eine Zeitzone.")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Zeitpunkt ohne Zeitzone.")
    return parsed


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _number(value: Any, where: str, issues: Issues) -> Optional[float]:
    if _is_blank(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        issues.add(Code.WERT_UNGUELTIG, f"{where}: kein gültiger Zahlenwert.")
        return None
    return float(value)


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


# ── Validierung je Programm ──────────────────────────────────────────────────────────────────────────

@dataclass
class Participant:
    excel_id: int
    key: str
    name: str
    vorname: str
    jahrgang: int
    verein: str
    ak: str
    ak_kontr: int
    startgeraet: str
    startposition: Optional[int] = None

    def sort_key(self) -> tuple:
        return (normalize_key_text(self.name), normalize_key_text(self.vorname), self.key)

    def public(self) -> dict:
        return {"teilnehmer": self.key, "name": self.name, "vorname": self.vorname, "verein": self.verein}


def public_program_name(source: ProgramSource) -> str:
    """Öffentlicher Programmname: Subdivision (Public!B9), sonst das Programm (Public!B4)."""
    return clean_text(source.public.get("subdivision")) or clean_text(source.public.get("program"))


_SUBDIVISION = re.compile(r"^[^\x00-\x1f]{1,40}$")


@dataclass
class ProgramResult:
    definition: ProgramDefinition
    source: ProgramSource
    rotation_max: int
    active: bool
    modes: dict[str, str]
    participants: list[Participant]
    results: list[dict]  # sichtbare Einzelwertungen (öffentlich, ohne geaendert_am)
    diagnose: dict
    einteilung: dict = field(default_factory=dict)  # (Rotation, Gerät) → [(Startposition, Excel-ID)]
    name: str = ""  # öffentlicher Programmname (Subdivision oder Programm)


def validate_public(source: ProgramSource, issues: Issues) -> Optional[tuple[ProgramDefinition, int, bool, dict]]:
    public = source.public
    program = public.get("program")
    definition = PROGRAMS.get(program) if isinstance(program, str) else None
    if definition is None:
        issues.add(Code.PROGRAMM_UNBEKANNT, f"{source.source.filename}: Public!B4 enthält kein bekanntes Programm "
                                            f"(zulässig: {', '.join(PROGRAM_ORDER)}).")
        return None
    rotation = _integer(public.get("published_rotation_max"))
    if rotation is None or not 0 <= rotation <= definition.rotationen:
        issues.add(Code.PUBLIC_UNGUELTIG, f"{definition.name}: Public!B5 muss eine ganze Zahl von 0 bis "
                                          f"{definition.rotationen} sein.")
    active = public.get("public_active")
    if not isinstance(active, bool):
        issues.add(Code.PUBLIC_UNGUELTIG, f"{definition.name}: Public!B6 muss WAHR oder FALSCH sein.")
    modes = {}
    for bereich, cell, label in ((BEREICH_KRAFT, "B7", "Kraftmodus"), (BEREICH_BEWEGLICHKEIT, "B8", "Beweglichkeitsmodus")):
        value = public.get(f"{bereich}_modus")
        text = value.strip() if isinstance(value, str) else value
        if text not in definition.athletik_modi:
            issues.add(Code.ATHLETIKMODUS_UNGUELTIG, f"{definition.name}: Public!{cell} ({label}) muss "
                                                     f"{' oder '.join(definition.athletik_modi)} sein.")
        modes[bereich] = text
    if rotation is None or not isinstance(active, bool):
        return None
    return definition, rotation, active, modes


def _validate_structure(src: ProgramSource, definition: ProgramDefinition, issues: Issues) -> None:
    name = definition.name
    station_names = [s.name for s in src.stations]
    if len(station_names) != definition.rotationen or len(set(station_names)) != len(station_names):
        issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: tab_startgeraet muss genau {definition.rotationen} "
                                            f"verschiedene Stationen enthalten.")
    if len({s.geraet_code for s in src.stations}) != len(src.stations):
        issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: GerID in tab_startgeraet ist nicht eindeutig.")
    for station in src.stations:
        if station.name == ATHLETIK_STATION and station.geraet_code != ATHLETIK_GERAETECODE[BEREICH_BEWEGLICHKEIT]:
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Station Beweglichkeit muss GerID 4 haben.")
    seen = Counter((d.geraet_code, d.nummer) for d in src.line_definitions)
    for (code, nummer), count in sorted(seen.items()):
        if count > 1:
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Testprogramm definiert ç{code}§{nummer} mehrfach.")

    # Athletik-Testdefinitionen im Testprogramm
    expected = {BEREICH_BEWEGLICHKEIT: definition.beweglichkeit_tests, BEREICH_KRAFT: definition.kraft_tests}
    for bereich, count in expected.items():
        code = ATHLETIK_GERAETECODE[bereich]
        numbers = sorted(d.nummer for d in src.line_definitions if d.geraet_code == code and d.nummer >= 1)
        if numbers != list(range(1, count + 1)):
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Testprogramm muss genau {count} Tests für {bereich} "
                                                f"(ç{code}§1…{count}) enthalten.")
        names = [normalize_key_text(d.name) for d in src.line_definitions if d.geraet_code == code and d.nummer >= 1]
        if len(set(names)) != len(names):
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Testnamen für {bereich} sind nicht eindeutig.")

        # Direktfelder: genau 1…N, Namen wie im Testprogramm
        fields = [f for f in src.direct_fields if f.bereich == bereich]
        if sorted(f.nummer for f in fields) != list(range(1, count + 1)):
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Noteneingabe muss genau {count} Direktfelder für "
                                                f"{bereich} (Nummer 1…{count}) besitzen.")
        defs = _line_map(src)
        for f in fields:
            d = defs.get((code, f.nummer))
            if d is None or normalize_key_text(d.name) != normalize_key_text(f.name):
                issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Direktfeld Noteneingabe!{f.spalte} (Zeile 6) passt "
                                                    f"nicht zum Testprogramm ç{code}§{f.nummer}.")

        # Vor-Ort-Blöcke: nur wo vor_ort zulässig ist, dann genau 1…N
        blocks = [b for b in src.onsite_blocks if b.bereich == bereich]
        if MODUS_VOR_ORT in definition.athletik_modi:
            if sorted(b.nummer for b in blocks) != list(range(1, count + 1)):
                issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Noteneingabe muss genau {count} Vor-Ort-Blöcke für "
                                                    f"{bereich} (Nummer 1…{count}) besitzen.")
        elif blocks:
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Vor-Ort-Blöcke für {bereich} sind in diesem Programm "
                                                f"nicht vorgesehen (Noteneingabe!{blocks[0].spalte}).")

    # Technikblöcke: jede definierte Linie braucht genau einen Block in Noteneingabe
    block_count = Counter((g, n) for g, n, _c in src.technik_blocks)
    for (g, n), count in sorted(block_count.items()):
        if count > 1:
            issues.add(Code.ERGEBNIS_ID_DOPPELT, f"{name}: Noteneingabe enthält mehrere Blöcke für {g} Linie {n}.")
    codes = {s.name: s.geraet_code for s in src.stations}
    for station in src.stations:
        if station.name == ATHLETIK_STATION:
            continue
        for d in src.line_definitions:
            if d.geraet_code == station.geraet_code and d.nummer >= 1 and d.versatz_min is not None:
                if (station.name, d.nummer) not in block_count:
                    issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Noteneingabe enthält keinen Block für "
                                                        f"{station.name} Linie {d.nummer}.")
    for g, n, col in src.technik_blocks:
        if g not in codes:
            issues.add(Code.STRUKTUR_UNGUELTIG, f"{name}: Noteneingabe!{col}: unbekanntes Gerät.")


def _line_map(src: ProgramSource) -> dict[tuple[int, int], LineDefinition]:
    return {(d.geraet_code, d.nummer): d for d in src.line_definitions}


def _validate_participants(src: ProgramSource, definition: ProgramDefinition, slug: str,
                           issues: Issues, programm: Optional[str] = None) -> dict[int, Participant]:
    name = programm or definition.name
    stations = {s.name for s in src.stations}
    by_id: dict[int, Participant] = {}
    for raw in src.participants:
        where = f"{name}: Anmeldung Zeile {raw.row}"
        if raw.excel_id in by_id:
            issues.add(Code.TEILNEHMER_DOPPELT, f"{where}: interne ID ist doppelt vergeben.")
            continue
        texts = {k: clean_text(getattr(raw, k)) for k in ("name", "vorname", "verein", "ak", "startgeraet")}
        jahrgang = _integer(raw.jahrgang)
        ak_kontr = _integer(raw.ak_kontr)
        problems = [k for k in ("name", "vorname", "verein", "ak") if not texts[k]]
        if jahrgang is None or not 1900 <= jahrgang <= 2100:
            problems.append("Jahrgang")
        if ak_kontr is None or ak_kontr not in src.ak_minmax:
            problems.append("akKontr")
        if texts["startgeraet"] not in stations:
            problems.append("Startgerät")
        if problems:
            issues.add(Code.TEILNEHMER_UNGUELTIG, f"{where}: ungültige Angaben ({', '.join(problems)}).")
            continue
        by_id[raw.excel_id] = Participant(
            excel_id=raw.excel_id,
            key=participant_key(slug, name, texts["name"], texts["vorname"], jahrgang, texts["verein"]),
            name=texts["name"], vorname=texts["vorname"], jahrgang=jahrgang, verein=texts["verein"],
            ak=texts["ak"], ak_kontr=ak_kontr, startgeraet=texts["startgeraet"],
        )
    rows_by_key = defaultdict(list)
    accepted: dict[int, int] = {}
    for raw in src.participants:
        if raw.excel_id in by_id:
            accepted.setdefault(raw.excel_id, raw.row)
    for excel_id, row in accepted.items():
        rows_by_key[by_id[excel_id].key].append(row)
    for key, rows in rows_by_key.items():
        if len(rows) > 1:
            issues.add(Code.TEILNEHMER_DOPPELT, f"{name}: Anmeldung Zeilen {', '.join(map(str, sorted(rows)))} "
                                                f"ergeben dieselbe öffentliche Teilnehmeridentität.")

    # Startliste: jede angemeldete Person genau einmal, im Abschnitt ihres Startgeräts
    seen: Counter = Counter()
    positions: dict[str, set] = defaultdict(set)
    for entry in src.startliste:
        where = f"{name}: Startliste Zeile {entry.row}"
        seen[entry.excel_id] += 1
        p = by_id.get(entry.excel_id)
        if p is None:
            issues.add(Code.TEILNEHMER_UNBEKANNT, f"{where}: Person ist nicht angemeldet.")
            continue
        if seen[entry.excel_id] > 1:
            issues.add(Code.TEILNEHMER_DOPPELT, f"{where}: Person steht mehrfach in der Startliste.")
            continue
        position = _integer(entry.position)
        if entry.startgeraet != p.startgeraet:
            issues.add(Code.TEILNEHMER_UNGUELTIG, f"{where}: Abschnitt passt nicht zum Startgerät der Anmeldung.")
        if position is None or position < 1 or position in positions[entry.startgeraet]:
            issues.add(Code.TEILNEHMER_UNGUELTIG, f"{where}: Startposition fehlt, ist ungültig oder doppelt.")
        else:
            positions[entry.startgeraet].add(position)
            p.startposition = position
    for p in by_id.values():
        if seen[p.excel_id] == 0:
            issues.add(Code.TEILNEHMER_UNBEKANNT, f"{name}: angemeldete Person fehlt in der Startliste.")

    noten: Counter = Counter(i for i, _r in src.noten_rows)
    for excel_id, row in src.noten_rows:
        if excel_id not in by_id and not any(r.excel_id == excel_id for r in src.participants):
            issues.add(Code.TEILNEHMER_UNBEKANNT, f"{name}: Noteneingabe Zeile {row}: Person ist nicht angemeldet.")
    for excel_id, count in noten.items():
        if count > 1:
            issues.add(Code.TEILNEHMER_DOPPELT, f"{name}: Person steht mehrfach in Noteneingabe.")
    for p in by_id.values():
        if noten[p.excel_id] == 0:
            issues.add(Code.TEILNEHMER_UNBEKANNT, f"{name}: angemeldete Person fehlt in Noteneingabe.")
    return by_id


def _intended(src: ProgramSource, line: LineDefinition, ak_kontr: int, where: str, issues: Issues) -> bool:
    """Linie für diese AK vorgesehen? (akMinMax min = max = 0 bedeutet: nicht vorgesehen)."""
    row = src.ak_minmax.get(ak_kontr)
    try:
        lo, hi = row[line.versatz_min - 1], row[line.versatz_max - 1]
    except (TypeError, IndexError):
        issues.add(Code.STRUKTUR_UNGUELTIG, f"{where}: akMinMax enthält keine Grenzen für diese Linie.")
        return False
    if _integer(lo) is None or _integer(hi) is None:
        issues.add(Code.STRUKTUR_UNGUELTIG, f"{where}: akMinMax-Grenzen sind keine ganzen Zahlen.")
        return False
    return not (lo == 0 and hi == 0)


def _rotation(value: Any, definition: ProgramDefinition, where: str, issues: Issues) -> Optional[int]:
    rotation = _integer(value)
    if rotation is None or not 1 <= rotation <= definition.rotationen:
        issues.add(Code.ROTATION_UNGUELTIG, f"{where}: Rotation fehlt oder liegt nicht zwischen 1 und "
                                            f"{definition.rotationen}.")
        return None
    return rotation


def _validate_einteilung(src: ProgramSource, definition: ProgramDefinition, people: dict[int, Participant],
                         issues: Issues) -> dict[tuple[int, str], list[tuple[int, int]]]:
    """Einteilung je Rotation und Gerät aus den Codes der Startliste (nicht berechnet).

    Jeder Code muss zur Zeile (ID) und zur Spalte (Rotation, Gerät; ç = Position in tab_startgeraet) passen; jede
    Person steht in jeder Rotation und an jeder Station genau einmal; Startpositionen je Rotation und Gerät sind
    eindeutig; die Rotation jeder Technikzeile der Noteneingabe (`id_rotation`) stimmt mit der Einteilung überein.
    """
    name = definition.name
    zeichen = src.einteilung_zeichen or {}
    if not all(zeichen.get(f) for f in EINTEILUNG_FELDER):
        issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: Startliste: Legende der Einteilungscodes "
                                              f"(id_athlete … id_position) fehlt.")
        return {}
    muster = re.compile("^" + "".join(re.escape(zeichen[f]) + r"(\d+)" for f in EINTEILUNG_FELDER) + "$")
    stationen = [s.name for s in src.stations]
    index = {n: i for i, n in enumerate(stationen, start=1)}
    riegen: dict[tuple[int, str], list[tuple[int, int]]] = defaultdict(list)
    zuteilung: dict[tuple[int, int], str] = {}
    for entry in src.startliste:
        if entry.excel_id not in people:
            continue  # bereits gemeldet
        rotationen, geraete = [], []
        for rotation, geraet, wert, zelle in entry.codes:
            treffer = muster.match(clean_text(wert))
            if treffer is None:
                issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: {zelle}: Einteilungscode fehlt oder ist ungültig.")
                continue
            code_id, rot, ger, pos = map(int, treffer.groups())
            if code_id != entry.excel_id or rot != _integer(rotation) or ger != index.get(geraet) or pos < 1:
                issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: {zelle}: Einteilungscode passt nicht zu ID, Rotation "
                                                      f"oder Gerät der Spalte.")
                continue
            rotationen.append(rot)
            geraete.append(geraet)
            zuteilung[(entry.excel_id, rot)] = geraet
            riegen[(rot, geraet)].append((pos, entry.excel_id))
        if sorted(rotationen) != list(range(1, definition.rotationen + 1)) or sorted(geraete) != sorted(stationen):
            issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: Startliste Zeile {entry.row}: Einteilung enthält nicht "
                                                  f"jede Rotation und jede Station genau einmal.")
    for (rot, geraet), liste in sorted(riegen.items()):
        positionen = [pos for pos, _i in liste]
        if len(set(positionen)) != len(positionen):
            issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: Startliste: Startposition in Rotation {rot} an {geraet} "
                                                  f"doppelt.")
    gemeldet = set()
    for slot in src.slots:
        rot = _integer(slot.rotation)
        if slot.bereich != BEREICH_TECHNIK or slot.quelle != QUELLE_STATION or rot is None:
            continue
        geraet = zuteilung.get((slot.excel_id, rot))
        if geraet is not None and geraet != slot.geraet and (slot.excel_id, slot.geraet) not in gemeldet:
            gemeldet.add((slot.excel_id, slot.geraet))
            issues.add(Code.EINTEILUNG_UNGUELTIG, f"{name}: {slot.zelle}: Rotation laut Noteneingabe passt nicht zur "
                                                  f"Einteilung der Startliste.")
    return dict(riegen)


def evaluate_program(src: ProgramSource, slug: str, issues: Issues) -> Optional[ProgramResult]:
    checked = validate_public(src, issues)
    if checked is None:
        return None
    definition, rotation_max, active, modes = checked
    _validate_structure(src, definition, issues)
    name = public_program_name(src) if _SUBDIVISION.match(public_program_name(src)) else definition.name
    people = _validate_participants(src, definition, slug, issues, name)
    einteilung = _validate_einteilung(src, definition, people, issues)
    lines = _line_map(src)
    codes = {s.name: s.geraet_code for s in src.stations}
    prog = name

    diag = {
        "technik": Counter(), BEREICH_BEWEGLICHKEIT: Counter(), BEREICH_KRAFT: Counter(),
        "sprung_ak9": Counter(), "sprung_ohne_alternative": 0,
    }
    results: list[dict] = []
    slot_keys: Counter = Counter()
    for slot in src.slots:
        slot_keys[(slot.excel_id, slot.bereich, slot.quelle, slot.geraet, slot.nummer)] += 1
    for key, count in slot_keys.items():
        if count > 1:
            issues.add(Code.ERGEBNIS_ID_DOPPELT, f"{prog}: Ergebnisplatz {key[3]} {key[4]} ist für eine Person "
                                                 f"mehrfach vorhanden.")

    # ── Technik ──
    sprung_values: dict[int, dict[int, bool]] = defaultdict(dict)
    for slot in (s for s in src.slots if s.bereich == BEREICH_TECHNIK):
        p = people.get(slot.excel_id)
        if p is None:
            continue
        where = f"{prog}: {slot.zelle}"
        sks = _number(slot.sks, where, issues)
        tal = _number(slot.tal, where, issues)
        end = _number(slot.end, where, issues)
        has_value = sks is not None or end is not None
        line = lines.get((codes.get(slot.geraet), slot.nummer))
        if line is None or line.versatz_min is None or line.versatz_max is None:
            if has_value:
                issues.add(Code.LINIE_NICHT_VORGESEHEN, f"{where}: Wertung in einer Linie ohne Testprogramm-Definition.")
            continue
        sprung = definition.sprung_regel and slot.geraet == SPRUNG
        ak9_slot2 = sprung and p.ak_kontr == 9 and slot.nummer == 2
        if not _intended(src, line, p.ak_kontr, where, issues) and not ak9_slot2:
            if has_value:
                issues.add(Code.LINIE_NICHT_VORGESEHEN, f"{where}: Wertung in einer für diese AK nicht vorgesehenen Linie.")
            continue
        if sprung:
            sprung_values[p.excel_id][slot.nummer] = has_value
        if not has_value:
            diag["technik"]["fehlend"] += 1
            continue
        if end is None:
            diag["technik"]["unvollstaendig"] += 1
            continue
        if sks is None or tal is None or abs(end - sks * tal) > END_TOLERANCE:
            issues.add(Code.ENDWERT_INKONSISTENT, f"{where}: Endwert entspricht nicht SKS × TAL.")
            continue
        rotation = _rotation(slot.rotation, definition, where, issues)
        if rotation is None:
            continue
        if rotation > rotation_max:
            diag["technik"]["verborgen_rotation"] += 1
            continue
        if sprung and p.ak_kontr == 9:
            code, anzeige = f"sprung-{slot.nummer}", f"Sprung {slot.nummer}"
            diag["sprung_ak9"][f"Sprung {slot.nummer} aus Excel-Linie {slot.nummer}"] += 1
        else:
            code, anzeige = f"{line.geraet_code}-{line.nummer}", clean_text(line.name)
        diag["technik"]["sichtbar"] += 1
        results.append({
            **p.public(), "ergebnis_id": f"{prog}:{p.key[:16]}:{BEREICH_TECHNIK}:{code}", "programm": prog,
            "ak": p.ak, "bereich": BEREICH_TECHNIK, "geraet": slot.geraet, "code": code, "anzeige": anzeige,
            "rotation": rotation, "S": number_out(sks), "T": number_out(end), "status": STATUS_GEWERTET,
        })
    if definition.sprung_regel:
        for excel_id, values in sprung_values.items():
            p = people[excel_id]
            if p.ak_kontr >= 10:
                if values.get(2) and values.get(3):
                    issues.add(Code.SPRUNG_DOPPELTE_ALTERNATIVE, f"{prog}: Noteneingabe Zeile "
                                                                 f"{_noten_row(src, excel_id)}: Sprung-Alternativen "
                                                                 f"2 und 3 sind gleichzeitig befüllt.")
                elif not values.get(2) and not values.get(3):
                    diag["sprung_ohne_alternative"] += 1

    # ── Athletik ──
    defs_by_bereich = {b: {d.nummer: d for d in src.line_definitions
                           if d.geraet_code == ATHLETIK_GERAETECODE[b] and d.nummer >= 1} for b in ATHLETIK_BEREICHE}
    slots_by = defaultdict(dict)
    for slot in (s for s in src.slots if s.bereich in ATHLETIK_BEREICHE):
        slots_by[(slot.excel_id, slot.bereich, slot.nummer)][slot.quelle] = slot
    for (excel_id, bereich, nummer), sources in sorted(slots_by.items()):
        p = people.get(excel_id)
        if p is None:
            continue
        line = defs_by_bereich[bereich].get(nummer)
        mode = modes.get(bereich)
        active_quelle = QUELLE_STATION if mode == MODUS_VOR_ORT else QUELLE_DIREKT
        present = {}
        values = {}
        for quelle, slot in sources.items():
            where = f"{prog}: {slot.zelle}"
            sks = _number(slot.sks, where, issues) if quelle == QUELLE_STATION else None
            end = _number(slot.end, where, issues)
            # TAL allein (vorbefüllt) ist kein Resultat; numerische Null zählt als Resultat.
            present[quelle] = sks is not None or end is not None
            values[quelle] = (end, slot, where)
        inactive = QUELLE_DIREKT if active_quelle == QUELLE_STATION else QUELLE_STATION
        if present.get(inactive):
            where = values[inactive][2]
            if present.get(active_quelle):
                issues.add(Code.ATHLETIK_BEIDE_QUELLEN, f"{where}: {bereich} Test {nummer} ist vor Ort und extern "
                                                        f"erfasst (Modus {mode}).")
            else:
                issues.add(Code.ATHLETIK_INAKTIVE_QUELLE, f"{where}: {bereich} Test {nummer} ist in der nicht "
                                                          f"gewählten Quelle erfasst (Modus {mode}).")
            continue
        if mode not in (MODUS_VOR_ORT, MODUS_EXTERN) or line is None or not present.get(active_quelle):
            if line is not None and mode in (MODUS_VOR_ORT, MODUS_EXTERN):
                diag[bereich]["fehlend"] += 1
            continue
        end, slot, where = values[active_quelle]
        if end is None:
            diag[bereich]["unvollstaendig"] += 1
            continue
        rotation = None
        if active_quelle == QUELLE_STATION:
            rotation = _rotation(slot.rotation, definition, where, issues)
            if rotation is None:
                continue
            if rotation > rotation_max:
                diag[bereich]["verborgen_rotation"] += 1
                continue
        code = f"{ATHLETIK_GERAETECODE[bereich]}-{nummer}"
        diag[bereich]["sichtbar"] += 1
        results.append({
            **p.public(), "ergebnis_id": f"{prog}:{p.key[:16]}:{bereich}:{code}", "programm": prog, "ak": p.ak,
            "bereich": bereich, "test": clean_text(line.name), "code": code, "erfassung": mode,
            "rotation": rotation, "punkte": number_out(end), "status": STATUS_GEWERTET,
        })

    ids = Counter(r["ergebnis_id"] for r in results)
    for rid, count in ids.items():
        if count > 1:
            issues.add(Code.ERGEBNIS_ID_DOPPELT, f"{prog}: Ergebnis-ID ist nicht eindeutig.")

    participants = sorted(people.values(), key=lambda p: (p.ak_kontr, p.ak, p.sort_key()))
    diagnose = {
        "teilnehmer": len(participants),
        "teilnehmer_pro_ak": dict(Counter(p.ak for p in participants)),  # Reihenfolge nach akKontr
        "technik": dict(sorted(diag["technik"].items())),
        BEREICH_BEWEGLICHKEIT: dict(sorted(diag[BEREICH_BEWEGLICHKEIT].items())),
        BEREICH_KRAFT: dict(sorted(diag[BEREICH_KRAFT].items())),
        "startliste_eintraege": sum(1 for p in participants if p.startposition is not None),
        "zeitplan_zeilen": len(src.zeitplan),
    }
    if definition.sprung_regel:
        diagnose["sprung"] = {"ak9_quellen": dict(sorted(diag["sprung_ak9"].items())),
                              "ak10_12_ohne_alternative": diag["sprung_ohne_alternative"]}
    return ProgramResult(definition, src, rotation_max, active, modes, participants, results, diagnose, einteilung,
                         name)


def _noten_row(src: ProgramSource, excel_id: int) -> str:
    rows = [r for i, r in src.noten_rows if i == excel_id]
    return str(rows[0]) if rows else "?"


# ── Ranglisten ───────────────────────────────────────────────────────────────────────────────────────

def assign_ranks(entries: list[dict], points_key: str = "punkte") -> list[dict]:
    """Absteigend nach Punkten; Gleichstand = gleicher Rang, nächster Rang übersprungen (1, 2, 2, 4).

    `_sortierung` ist eine reine Darstellungsreihenfolge innerhalb gleicher Punkte und verändert den Rang nicht.
    """
    ordered = sorted(entries, key=lambda e: (-e[points_key], e["_sortierung"]))
    out = []
    previous = None
    rank = 0
    for index, entry in enumerate(ordered, start=1):
        if previous is None or entry[points_key] != previous:
            rank = index
            previous = entry[points_key]
        clean = {k: v for k, v in entry.items() if k != "_sortierung"}
        clean["rang"] = rank
        out.append(clean)
    return out


def ranking_structure(program: ProgramResult, ak_kontr: int) -> dict:
    """Vorgesehene Geräte, Linien und Athletiktests einer AK (nur Testprogramm, keine Resultate).

    Dient der Rangliste als Gliederung, damit ein noch fehlendes Einzelresultat als „–“ erscheinen kann. Codes und
    Bezeichnungen entsprechen denen der Einzelwertungen (`code`, `anzeige`, `test`). Sprung AK9: „Sprung 1“ und
    „Sprung 2“; AK10–AK12: Pflichtsprung plus genau eine Alternative (`alternative`).
    """
    src, definition = program.source, program.definition
    ignore = Issues()  # Strukturfehler meldet bereits die Validierung
    geraete = []
    for station in src.stations:
        if station.geraet_code in ATHLETIK_GERAETECODE.values():
            continue
        linien = []
        for line in sorted((d for d in src.line_definitions if d.geraet_code == station.geraet_code and d.nummer >= 1
                            and d.versatz_min is not None and d.versatz_max is not None), key=lambda d: d.nummer):
            sprung = definition.sprung_regel and station.name == SPRUNG
            if sprung and ak_kontr == 9:
                if line.nummer in (1, 2):
                    linien.append({"code": f"sprung-{line.nummer}", "anzeige": f"Sprung {line.nummer}"})
                continue
            if not _intended(src, line, ak_kontr, "", ignore):
                continue
            eintrag = {"code": f"{line.geraet_code}-{line.nummer}", "anzeige": clean_text(line.name)}
            if sprung and line.nummer >= 2:
                eintrag["alternative"] = True
            linien.append(eintrag)
        if linien:
            geraete.append({"geraet": station.name, "linien": linien})
    out = {"geraete": geraete}
    for bereich in ATHLETIK_BEREICHE:
        code = ATHLETIK_GERAETECODE[bereich]
        tests = sorted((d for d in src.line_definitions if d.geraet_code == code and d.nummer >= 1),
                       key=lambda d: d.nummer)
        aktiv = program.modes.get(bereich) in (MODUS_VOR_ORT, MODUS_EXTERN)
        out[bereich] = [{"code": f"{code}-{d.nummer}", "test": clean_text(d.name)} for d in tests] if aktiv else []
    return out


def build_rankings(program: ProgramResult) -> list[dict]:
    """Je AK: Technik-, Athletik- und Gesamtrangliste aus den sichtbaren Einzelwertungen."""
    sums: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in program.results:
        value = r["T"] if r["bereich"] == BEREICH_TECHNIK else r["punkte"]
        sums[r["teilnehmer"]][r["bereich"]].append(float(value))
    groups: dict[str, list[Participant]] = defaultdict(list)
    for p in program.participants:
        groups[p.ak].append(p)
    out = []
    for ak, people in sorted(groups.items(), key=lambda kv: (kv[1][0].ak_kontr, kv[0])):
        rows = []
        for p in people:
            parts = {b: number_out(math.fsum(sums[p.key][b])) for b in (BEREICH_TECHNIK, *ATHLETIK_BEREICHE)}
            athletik = number_out(math.fsum([parts[BEREICH_BEWEGLICHKEIT], parts[BEREICH_KRAFT]]))
            gesamt = number_out(math.fsum([parts[BEREICH_TECHNIK], athletik]))
            rows.append({**p.public(), "jahrgang": p.jahrgang, "startgeraet": p.startgeraet,
                         "technik": parts[BEREICH_TECHNIK], "beweglichkeit": parts[BEREICH_BEWEGLICHKEIT],
                         "kraft": parts[BEREICH_KRAFT], "athletik": athletik, "gesamt": gesamt,
                         "_sortierung": p.sort_key()})
        out.append({
            "ak": ak,
            "technik": assign_ranks([dict(r, punkte=r["technik"]) for r in rows]),
            "athletik": assign_ranks([dict(r, punkte=r["athletik"]) for r in rows]),
            "gesamt": assign_ranks([dict(r, punkte=r["gesamt"]) for r in rows]),
            "struktur": ranking_structure(program, people[0].ak_kontr),
        })
    return out


# ── Live-Änderungen ──────────────────────────────────────────────────────────────────────────────────

def strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def sort_live(results: list[dict]) -> list[dict]:
    """Neueste zuerst; bei gleichem Zeitpunkt stabil nach Ergebnis-ID."""
    by_id = sorted(results, key=lambda r: r["ergebnis_id"])
    return sorted(by_id, key=lambda r: parse_utc(r["geaendert_am"]), reverse=True)


def merge_live(previous: Optional[list[dict]], current: list[dict], observed_at: datetime | str) -> list[dict]:
    """Reiner Vergleich zweier Stände.

    Neue oder veränderte sichtbare Resultate erhalten den Beobachtungszeitpunkt, unveränderte behalten ihren
    bisherigen Zeitpunkt. Nicht mehr sichtbare Resultate entfallen. Ein Resultat einer neu freigegebenen Rotation
    ist im vorherigen Stand nicht enthalten und gilt daher als neu.
    """
    stamp = utc_text(observed_at)
    before = {r["ergebnis_id"]: r for r in (previous or [])}
    merged = []
    for result in current:
        old = before.get(result["ergebnis_id"])
        unchanged = old is not None and canonical_json(strip_volatile(old)) == canonical_json(strip_volatile(result))
        merged.append({**strip_volatile(result), "geaendert_am": old["geaendert_am"] if unchanged else stamp})
    return sort_live(merged)


# ── Gesamtstand ──────────────────────────────────────────────────────────────────────────────────────

def _validate_event(sources: list[ProgramSource], issues: Issues) -> tuple[str, str]:
    if not sources:
        issues.add(Code.STRUKTUR_UNGUELTIG, "Keine Arbeitsmappe angegeben.")
        issues.raise_if_any()
    slugs = {s.public.get("event_id") for s in sources}
    titles = {s.public.get("event_title") for s in sources}
    if len(slugs) != 1:
        issues.add(Code.EVENT_WIDERSPRUCH, "Public!B2 (Event-Slug) unterscheidet sich zwischen den Arbeitsmappen.")
    if len(titles) != 1:
        issues.add(Code.EVENT_WIDERSPRUCH, "Public!B3 (Titel) unterscheidet sich zwischen den Arbeitsmappen.")
    programs = Counter(s.public.get("program") for s in sources)
    names = Counter(public_program_name(s) for s in sources)
    for s in sources:
        sub = s.public.get("subdivision")
        if sub is not None and (not isinstance(sub, str) or not _SUBDIVISION.match(clean_text(sub))):
            issues.add(Code.SUBDIVISION_UNGUELTIG, f"{s.source.filename}: Public!B9 (subdivision) muss ein Text mit "
                                                   f"höchstens 40 Zeichen sein.")
        elif programs[s.public.get("program")] > 1 and not clean_text(sub):
            issues.add(Code.PROGRAMM_DOPPELT, f"Programm {s.public.get('program')} kommt mehrfach vor: jede Mappe "
                                              f"braucht dann eine eigene Subdivision (Public!B9).")
    for name, count in names.items():
        if count > 1:
            issues.add(Code.PROGRAMM_DOPPELT, f"Programm bzw. Subdivision {name} kommt mehrfach vor.")
    issues.raise_if_any()
    return next(iter(slugs)), next(iter(titles))


def _validate_subdivisions(programs: list[ProgramResult], issues: Issues) -> set[str]:
    """Subdivisionen eines Programms: jede AK steht in genau einer (sonst wäre die Rangliste der AK geteilt).

    Gibt die Namen der betroffenen Subdivisionen zurück."""
    betroffen: set[str] = set()
    by_definition: dict[str, list[ProgramResult]] = defaultdict(list)
    for p in programs:
        by_definition[p.definition.name].append(p)
    for definition, parts in sorted(by_definition.items()):
        seen: dict[str, str] = {}
        for p in parts:
            for ak in sorted({x.ak for x in p.participants}):
                if ak in seen and seen[ak] != p.name:
                    issues.add(Code.SUBDIVISION_UNGUELTIG, f"{definition}: {ak} kommt in den Subdivisionen {seen[ak]} "
                                                           f"und {p.name} vor; eine AK muss ganz in einer Subdivision "
                                                           f"stehen.")
                    betroffen.update({seen[ak], p.name})
                seen.setdefault(ak, p.name)
    return betroffen


def _bewerten(sources: list[ProgramSource], slug: str, issues: Issues,
              teilweise: bool) -> tuple[list[ProgramResult], list[dict]]:
    """Alle Programme prüfen. Streng: jeder Fehler bricht ab. Teilweise: ein fehlerhaftes Programm wird blockiert
    (es behält seinen bisherigen öffentlichen Stand), die übrigen werden publiziert; sind alle fehlerhaft, Abbruch.
    Steht in einer fehlerhaften Mappe Public!B6 gültig auf FALSCH, gilt das Programm als gesperrt (nicht blockiert):
    seine Inhalte werden nicht weiter gezeigt."""
    if not teilweise:
        evaluated = [evaluate_program(src, slug, issues) for src in sources]
        issues.raise_if_any()
        _validate_subdivisions(evaluated, issues)
        issues.raise_if_any()
        return evaluated, []
    gueltig, blockiert, alle = [], [], Issues()
    for src in sources:
        eigene = Issues()
        program = evaluate_program(src, slug, eigene)
        alle.items.extend(eigene.items)
        if eigene or program is None:
            if src.public.get("public_active") is not False:
                blockiert.append((src, eigene.items))
        else:
            gueltig.append(program)
    konflikte = Issues()
    betroffen = _validate_subdivisions(gueltig, konflikte)
    alle.items.extend(konflikte.items)
    for program in [p for p in gueltig if p.name in betroffen]:
        gueltig.remove(program)
        blockiert.append((program.source, [i for i in konflikte.items if program.name in i.message]))
    if not gueltig and alle:
        alle.raise_if_any()
    return gueltig, [{"programm": public_program_name(src), "codes": sorted({i.code for i in items}),
                      "meldungen": [f"[{i.code}] {i.message}" for i in items]} for src, items in blockiert]


def build_state(sources: list[ProgramSource], *, observed_at: datetime | str,
                previous: Optional[dict] = None, teilweise: bool = False) -> dict:
    """Validiert alle Programme und erzeugt den kanonischen Publikationsstand – oder bricht vollständig ab.

    `teilweise=True` (Datenbank-Publisher): fehlerhafte Programme stehen unter `blockiert` und fehlen im Stand;
    Fehler auf Ebene Testtag (Slug, Titel, doppelte Programme) brechen weiterhin alles ab."""
    issues = Issues()
    slug, title = _validate_event(sources, issues)
    evaluated, blockiert = _bewerten(sources, slug, issues, teilweise)
    order = {name: i for i, name in enumerate(PROGRAM_ORDER)}
    programs = sorted(evaluated, key=lambda p: (order[p.definition.name],
                                                min((x.ak_kontr for x in p.participants), default=999), p.name))

    previous_results = (previous or {}).get("einzelwertungen")
    current = [r for p in programs if p.active for r in p.results]
    zeitplan, startlisten, einteilungen, ranglisten = [], {}, {}, {}
    for p in programs:
        if not p.active:
            continue
        zeitplan.append({"programm": p.name, "zeilen": p.source.zeitplan})
        startlisten[p.name] = _startliste(p)
        einteilungen[p.name] = _einteilung(p)
        ranglisten[p.name] = build_rankings(p)
    return {
        "schema_version": SCHEMA_VERSION,
        "event": {"slug": slug, "titel": title},
        "beobachtet_am": utc_text(observed_at),
        "programme": [{
            "programm": p.name,
            "aktiv": p.active,
            "rotationen": p.definition.rotationen,
            "rotation_freigegeben": p.rotation_max,
            "kraftmodus": p.modes[BEREICH_KRAFT],
            "beweglichkeitsmodus": p.modes[BEREICH_BEWEGLICHKEIT],
            "quelle": {"datei": p.source.source.filename, "sha256": p.source.source.sha256,
                       "bytes": p.source.source.size_bytes},
        } for p in programs],
        "zeitplan": zeitplan,
        "startlisten": startlisten,
        "einteilungen": einteilungen,
        "einzelwertungen": merge_live(previous_results, current, observed_at),
        "ranglisten": ranglisten,
        "diagnose": {p.name: p.diagnose for p in programs},
        "blockiert": blockiert,
    }


def _einteilung(program: ProgramResult) -> dict:
    """Ansicht „Gerät“: je Rotation und Station (auch Beweglichkeit) die Teilnehmerschlüssel in Startposition."""
    personen = {p.excel_id: p for p in program.participants}
    geraete = [s.name for s in program.source.stations]
    riegen = []
    for rotation in range(1, program.definition.rotationen + 1):
        for geraet in geraete:
            liste = sorted((pos, personen[i].sort_key(), personen[i].key)
                           for pos, i in program.einteilung.get((rotation, geraet), []) if i in personen)
            riegen.append({"rotation": rotation, "geraet": geraet, "teilnehmer": [key for _p, _s, key in liste]})
    return {"rotationen": program.definition.rotationen, "geraete": geraete, "riegen": riegen}


def _startliste(program: ProgramResult) -> list[dict]:
    by_station = defaultdict(list)
    for p in program.participants:
        if p.startposition is not None:
            by_station[p.startgeraet].append(p)
    groups = []
    for station in program.source.startliste_sections:
        people = sorted(by_station.get(station, []), key=lambda p: p.startposition)
        if not people:
            continue
        groups.append({"startgeraet": station, "teilnehmer": [{
            "position": p.startposition, **p.public(), "jahrgang": p.jahrgang, "ak": p.ak,
            "programm": program.name, "startgeraet": p.startgeraet,
        } for p in people]})
    return groups


# ── Publikationszeilen ───────────────────────────────────────────────────────────────────────────────

def content_checksum(inhalt: dict) -> str:
    """SHA-256 über den kanonischen Inhalt ohne flüchtige Felder (Beobachtungs-/Änderungszeitpunkte)."""
    return canonical_sha256(strip_volatile(inhalt))


def _row(bereich: str, programm: str, ak: str, art: str, gruppen: list, hinweis: Optional[str] = None,
         struktur: Optional[dict] = None, einteilung: Optional[dict] = None) -> dict:
    inhalt = {"schema_version": SCHEMA_VERSION, "art": art, "gruppen": gruppen}
    if hinweis:
        inhalt["hinweis"] = hinweis
    if struktur is not None:
        inhalt["struktur"] = struktur
    if einteilung is not None:
        inhalt["einteilung"] = einteilung
    return {"bereich": bereich, "programm": programm, "ak": ak, "inhalt": inhalt,
            "pruefsumme": content_checksum(inhalt)}


def _rotation_hint(program: dict) -> str:
    if program["rotation_freigegeben"] >= program["rotationen"]:
        return "Alle Rotationen freigegeben."
    return (f"Zwischenstand: Resultate bis Rotation {program['rotation_freigegeben']} von "
            f"{program['rotationen']} freigegeben.")


def publication_rows(state: dict) -> list[dict]:
    """Künftige Zeilen für `veroeffentlichung` (ohne Datenbank-ID, ohne `stand_am`)."""
    rows = []
    if state["zeitplan"]:
        rows.append(_row("zeitplan", "", "", "zeitplan", state["zeitplan"]))
    for program in state["programme"]:
        if not program["aktiv"]:
            continue
        name = program["programm"]
        hint = _rotation_hint(program)
        # Ansicht „Gerät“: Einteilung aus der Startliste und die geprüfte Freigabegrenze (Public!B5).
        rows.append(_row("startliste", name, "", "startliste", state["startlisten"][name], einteilung={
            **state["einteilungen"][name], "freigegeben": program["rotation_freigegeben"]}))
        for group in state["ranglisten"][name]:
            ak = group["ak"]
            live = [r for r in state["einzelwertungen"] if r["programm"] == name and r["ak"] == ak]
            rows.append(_row("resultate_live", name, ak, "resultate_live", [{"ak": ak, "resultate": live}], hint))
            rows.append(_row("resultate_rangliste", name, ak, "resultate_rangliste", [
                {"liste": liste, "eintraege": group[liste]} for liste in ("technik", "athletik", "gesamt")
            ], hint, group["struktur"]))
    return rows
