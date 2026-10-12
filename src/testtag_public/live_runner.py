"""P1F: lokaler Dauerpublisher für genau die beiden konfigurierten Arbeitsmappen.

    python -B -m testtag_public.live_runner --confirm-slug <Event-Slug>   # Dauerbetrieb (Ctrl+C beendet)
    python -B -m testtag_public.live_runner --check                       # einmal prüfen, ohne Netzwerk

Ablauf je Takt (`TESTTAG_POLL_SECONDS`, Standard 1 s, zulässig 1–60):

1. Grösse und Änderungszeit beider Dateien erfassen (günstiger Hinweis, kein Lesen des Inhalts).
2. Entspricht der kombinierte Stand dem zuletzt erfolgreich publizierten: nichts tun.
3. Sonst warten, bis jede Datei mindestens `STABIL_SEKUNDEN` (2 s) und in mindestens zwei aufeinanderfolgenden
   Takten unverändert ist.
4. Nur geänderte Arbeitsmappen über die sicheren temporären Kopien lesen; eine unveränderte Mappe (gleiche Grösse
   und Änderungszeit wie beim letzten erfolgreichen Lesen) wird nicht erneut gelesen. Beide gemeinsam validieren.
5. Identischer Inhalt (SHA-256) wie zuletzt publiziert: keine Anfrage (z. B. Speichern ohne Änderung).
   Zusätzlicher Auslöser bei unveränderter Datei (`excel_live`): Ist die Mappe in Excel Desktop geöffnet, wird
   ihr Stand alle `OFFEN_SEKUNDEN` geprüft. Ändert er sich (z. B. eine Excel-Online-Eingabe über Co-Authoring,
   ohne neuen Speicherstand der Datei) und bleibt er zwei Prüfungen lang gleich bei abgeschlossener Berechnung,
   wird eine Kopie (`SaveCopyAs`) wie eine Datei gelesen, validiert und publiziert.
6. Sonst P1C-Publikation (`database_publisher.publish`): nur geänderte Zeilen, öffentliches Rücklesen.
7. Erst nach Erfolg gilt der kombinierte Stand als verarbeitet.

Fehler: Datei- und Netzwerk-/Datenbankfehler werden mit wachsendem Abstand (höchstens 60 s) wiederholt;
Validierungsfehler erst nach der nächsten Dateiänderung. Der alte öffentliche Stand bleibt jeweils erhalten.
Ein archivierter Testtag wird nie mehr beschrieben.

Eine lokale Sperre (`out/live-<kennung>.lock`, nur PID und Startzeit) verhindert zwei Dauerpublisher für
dieselben Arbeitsmappen. Das Betriebsprotokoll (`out/live_runner.log`, rotierend) und die Konsole enthalten nur
Zeitpunkte, Programmkennungen, Zustände, Fehlerkategorien und Anzahlen – keine Namen, Resultate, IDs,
vollständigen Pfade oder Schlüssel.

Exitcodes: 0 Ende · 1 unerwartet · 2 Aufruf · 3 Excelquelle · 4 Validierung · 5 Konfiguration · 10 Sperre aktiv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import logging.handlers
import math
import os
import sys
import threading
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TextIO

from . import database_publisher as dp
from . import excel_live
from . import local_config
from . import supabase_database as db
from .excel_probe import ProbeError, SourceError, _snapshot
from .excel_publication import read_program_source
from .publication_model import Issue, PublicationError, build_state, publication_rows
from .publisher import SafeReport, _check_program
from .write_scope import SlugScope

PROGRAMME = ("AK5-AK9+", "AK9-AK12")
OPTIONEN = {"AK5-AK9+": local_config.ENV_WORKBOOK_AK5, "AK9-AK12": local_config.ENV_WORKBOOK_AK9}
LOG_NAME = "live_runner.log"
LOG_BYTES = 256 * 1024
LOG_BACKUPS = 3
MAX_BACKOFF = 60
STABIL_SEKUNDEN = 2  # Mindestdauer ohne Änderung von Grösse/Änderungszeit vor dem Lesen
OFFEN_SEKUNDEN = 2  # Abstand der Prüfungen einer in Excel geöffneten Mappe
LOCK_EXIT = 10
SLUG_ABWEICHUNG = "SLUG_ABWEICHUNG"


class InstanceLockError(ProbeError):
    exit_code = LOCK_EXIT


# ── Betriebsprotokoll ────────────────────────────────────────────────────────────────────────────────────

class BetriebsLog:
    """Konsole und rotierende Datei mit denselben gefilterten Zeilen."""

    def __init__(self, stream: Optional[TextIO], report: SafeReport, path: Optional[Path] = None):
        self.stream, self.report = stream, report
        self.handler = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS,
                                                                encoding="utf-8", delay=True)
            self.handler.setFormatter(logging.Formatter("%(message)s"))

    def __call__(self, text: str) -> None:
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {self.report.clean(text)}"
        if self.handler is not None:
            self.handler.emit(logging.LogRecord("live", logging.INFO, "", 0, line, None, None))
        if self.stream is not None:
            try:
                print(line, file=self.stream, flush=True)
            except UnicodeEncodeError:
                print(line.encode("ascii", "replace").decode("ascii"), file=self.stream, flush=True)

    def close(self) -> None:
        if self.handler is not None:
            self.handler.close()


# ── Einzelinstanz ────────────────────────────────────────────────────────────────────────────────────────

def _process_created(pid: int) -> Optional[float]:
    """Windows: Startzeit eines Prozesses (Epoch-Sekunden) oder None, wenn er nicht (mehr) läuft."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0 if ctypes.get_last_error() == 5 else None  # Zugriff verweigert: Prozess existiert
    try:
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return 0.0
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600
    finally:
        kernel.CloseHandle(handle)


def process_alive(pid: int, started: float) -> bool:
    """Läuft der Prozess, der die Sperre angelegt hat, noch? Ein wiederverwendeter PID gilt als veraltet."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        created = _process_created(pid)
        return created is not None and created <= started + 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class InstanceLock:
    def __init__(self, path: Path, *, pid: Optional[int] = None, alive: Optional[Callable[[int, float], bool]] = None,
                 clock: Callable[[], float] = time.time):
        self.path, self.pid, self.clock = path, pid or os.getpid(), clock
        self.alive = alive or process_alive
        self.held = False

    def _read(self) -> Optional[dict]:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return info if isinstance(info, dict) and isinstance(info.get("pid"), int) else None
        except (OSError, ValueError):
            return None

    def acquire(self) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        replaced = False
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self._read()
                if info is not None and info["pid"] != self.pid and \
                        self.alive(info["pid"], float(info.get("gestartet_epoch", 0))):
                    raise InstanceLockError(f"Ein Dauerpublisher für diese Arbeitsmappen läuft bereits "
                                            f"(PID {info['pid']}). Nichts gestartet.") from None
                try:  # veraltet (Absturz) oder unlesbar: kontrolliert beiseitelegen und neu anlegen
                    os.replace(self.path, self.path.with_name(self.path.name + ".veraltet"))
                    replaced = True
                except FileNotFoundError:
                    pass
                continue
            started = self.clock()
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": self.pid, "gestartet_epoch": started,
                           "gestartet": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")},
                          handle)
            self.held = True
            return "veraltete Sperre ersetzt" if replaced else "angelegt"
        raise InstanceLockError("Sperre konnte nicht angelegt werden.")

    def release(self) -> None:
        if self.held:
            info = self._read()
            if info is not None and info["pid"] == self.pid:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            self.held = False


def lock_path(out_dir: Path, paths: dict[str, Path]) -> Path:
    """Kennung aus den normalisierten Pfaden (Hash, keine Pfade im Dateinamen)."""
    key = "\n".join(os.path.normcase(os.path.abspath(str(paths[p]))) for p in PROGRAMME)
    return out_dir / f"live-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}.lock"


# ── Dauerbetrieb ─────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Verarbeitet:
    stats: tuple
    shas: tuple


class Runner:
    def __init__(self, config: db.DatabaseConfig, paths: dict[str, Path], *, confirm_slug: str, poll: int,
                 log: Callable[[str], None], report: Optional[SafeReport] = None, transport=None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic, read=read_program_source, publish=dp.publish,
                 offen: Optional[dict] = None):
        self.config, self.paths, self.slug, self.poll = config, paths, confirm_slug, poll
        self.log, self.report, self.transport = log, report, transport
        self.now, self.monotonic, self.read, self.publish = now, monotonic, read, publish
        self.last_stat: dict[str, object] = {p: None for p in PROGRAMME}
        self.stable = {p: 0 for p in PROGRAMME}
        self.stable_ticks = max(2, math.ceil(STABIL_SEKUNDEN / poll) + 1)  # Takt 1 s: 3 Beobachtungen über 2 s
        self.gelesen: dict[str, tuple] = {}  # Programm -> (Dateistand, zuletzt erfolgreich gelesene Quelle)
        self.verarbeitet: Optional[Verarbeitet] = None
        self.failures = 0
        self.retry_at: Optional[float] = None
        self.failed_stats: Optional[tuple] = None
        self.blocked: Optional[tuple] = None
        self.archiviert = False
        self.publikationen = 0
        self._notiert: set = set()
        self.offen = offen or {}  # Programm -> excel_live.OffeneMappe (nur Windows mit Excel Desktop)
        self.offen_basis: dict[str, str] = {}  # zuletzt verarbeiteter Fingerabdruck der geöffneten Mappe
        self.offen_kandidat: dict[str, str] = {}
        self.offen_naechste = 0.0

    # Hilfen
    def _note(self, key, text: str) -> None:
        """Wiederholte Zustände nur einmal protokollieren."""
        if key not in self._notiert:
            self._notiert.add(key)
            self.log(text)

    def _clean(self, text: str) -> str:
        for program, path in self.paths.items():
            text = text.replace(path.name, program)
        return text

    def _retry(self, kategorie: str, text: str, combined: tuple) -> str:
        self.failures += 1
        delay = min(self.poll * 2 ** (self.failures - 1), MAX_BACKOFF)
        self.retry_at = self.monotonic() + delay
        self.failed_stats = combined
        self.log(f"{kategorie}: {self._clean(text)} – alter Stand bleibt, Wiederholung geplant in {delay} s")
        return "wiederholung_geplant"

    def _done(self, combined: tuple, shas: tuple) -> None:
        self.verarbeitet = Verarbeitet(combined, shas)
        self.failures, self.retry_at, self.failed_stats, self.blocked = 0, None, None, None
        self._notiert.clear()

    # Ein Takt
    def tick(self) -> str:
        stats = {}
        for program in PROGRAMME:
            try:
                stats[program] = _snapshot(self.paths[program])
            except SourceError as exc:
                stats[program] = ("fehler", str(exc))
            self.stable[program] = self.stable[program] + 1 if stats[program] == self.last_stat[program] else 1
        self.last_stat = dict(stats)
        combined = tuple(stats[p] for p in PROGRAMME)
        if self.verarbeitet is not None and combined == self.verarbeitet.stats:
            return self._offen_pruefen(combined)
        for program in PROGRAMME:
            if stats[program][0] == "fehler":
                self._note((combined, program, "fehler"),
                           f"{program}: Datei nicht verfügbar ({self._clean(stats[program][1])}) – neuer Versuch")
                return "datei_fehler"
        unstable = [p for p in PROGRAMME if self.stable[p] < self.stable_ticks]
        if unstable:
            for program in unstable:
                self._note((combined, program, "aenderung"), f"{program}: Änderung erkannt – Datei noch in "
                                                             "Bearbeitung, warte auf stabilen Speicherstand")
            return "in_bearbeitung"
        if self.blocked == combined:
            return "wartet_auf_aenderung"
        if self.retry_at is not None and combined == self.failed_stats and self.monotonic() < self.retry_at:
            return "wartet_auf_wiederholung"
        return self._process(combined)

    def _process(self, combined: tuple) -> str:
        changed = [p for i, p in enumerate(PROGRAMME)
                   if self.verarbeitet is None or self.verarbeitet.stats[i] != combined[i]]
        lesen = [p for i, p in enumerate(PROGRAMME) if self.gelesen.get(p, (None,))[0] != combined[i]]
        self.log(f"Datei stabil: {', '.join(changed)} – gelesen wird {', '.join(lesen)}"
                 + ("" if len(lesen) == len(PROGRAMME) else "; unveränderte Arbeitsmappe nicht erneut gelesen"))
        try:
            sources = [self.read(self.paths[p], wait_seconds=0, sleep=lambda _s: None) if p in lesen
                       else self.gelesen[p][1] for p in PROGRAMME]
            for source, program in zip(sources, PROGRAMME):
                _check_program(source, program, OPTIONEN[program])
            if {s.public.get("event_id") for s in sources} != {self.slug}:
                raise PublicationError([Issue(SLUG_ABWEICHUNG, "Public!B2 entspricht nicht dem bestätigten Slug.")])
        except SourceError as exc:
            return self._retry("Dateifehler", str(exc), combined)
        except PublicationError as exc:
            return self._invalid(exc, combined)
        self.gelesen = {p: (combined[i], sources[i]) for i, p in enumerate(PROGRAMME)}
        self.offen_basis.clear()  # geöffneten Stand danach einmal abgleichen (Kopie, ohne Anfrage wenn gleich)
        return self._veroeffentlichen(sources, combined)

    def _veroeffentlichen(self, sources: list, combined: tuple, wann: str = "nach der nächsten Dateiänderung") -> str:
        shas = tuple(s.source.sha256 for s in sources)
        if self.verarbeitet is not None and shas == self.verarbeitet.shas:
            self._done(combined, shas)
            self.log("Inhalt unverändert (nur Speicherzeitpunkt) – keine Supabase-Anfrage")
            return "inhalt_unveraendert"
        if self.archiviert:
            self._done(combined, shas)
            self.log("Archivstatus: Testtag archiviert – Änderung wird nicht publiziert")
            return "archiviert"
        try:
            outcome = self.publish(self.config, sources, observed_at=self.now(), transport=self.transport)
        except PublicationError as exc:
            return self._invalid(exc, combined, wann)
        except dp.PublisherError as exc:
            if exc.code == dp.PublisherCode.TESTTAG_ARCHIVIERT:
                self.archiviert = True
                self._done(combined, shas)
                self.log("Archivstatus: Testtag archiviert – keine weitere Publikation, nichts geschrieben")
                return "archiviert"
            return self._retry("Netzwerk-/Datenbankfehler", str(exc), combined)
        except SourceError as exc:
            return self._retry("Dateifehler", str(exc), combined)
        except ProbeError as exc:
            return self._retry("Fehler", str(exc), combined)
        if self.report is not None:
            self.report.protect_state(outcome.state)
        self._done(combined, shas)
        self.publikationen += 1
        if outcome.status == "publiziert":
            self.log(f"Publikation bestätigt: {outcome.geschrieben} Zeilen geändert (davon {outcome.tombstones} "
                     f"Tombstones), {outcome.unveraendert} unverändert, öffentlich "
                     f"{'ja' if outcome.oeffentlich else 'nein'}")
        elif outcome.status == "unveraendert":
            self.log(f"Abgleich bestätigt: {outcome.zeilen_gesamt} Zeilen unverändert – keine Schreibanfrage")
        else:
            self.log("Kein Programm freigegeben und kein Testtag vorhanden – nichts publiziert")
        return outcome.status

    # Geöffnete Excel-Mappe (zusätzlicher Auslöser bei unveränderter Datei)
    def _offen_pruefen(self, combined: tuple) -> str:
        if not self.offen or self.archiviert or self.monotonic() < self.offen_naechste:
            return "keine_aenderung"
        if self.retry_at is not None and self.monotonic() < self.retry_at:
            return "wartet_auf_wiederholung"
        self.offen_naechste = self.monotonic() + OFFEN_SEKUNDEN
        stabil = {}
        for program, mappe in self.offen.items():
            zustand = mappe.zustand()
            fp = zustand.fingerabdruck
            if zustand.grund == excel_live.BERECHNUNG_MANUELL:
                self._note(("manuell", program), f"{program}: Excel-Berechnung ist manuell – der geöffnete Stand "
                                                 "wird nicht verwendet")
            if fp is None or fp == self.offen_basis.get(program):
                self.offen_kandidat.pop(program, None)
                continue
            if self.offen_kandidat.get(program) != fp:  # erst bei gleicher nächster Prüfung
                self.offen_kandidat[program] = fp
                continue
            stabil[program] = fp
        if not stabil:
            return "offen_in_bearbeitung" if self.offen_kandidat else "keine_aenderung"
        return self._offen_lesen(combined, stabil)

    def _offen_lesen(self, combined: tuple, stabil: dict) -> str:
        with tempfile.TemporaryDirectory(prefix="testtag-offen-") as tmp:
            kopien = {}
            for program, fp in stabil.items():
                ziel = Path(tmp) / program / self.paths[program].name  # gleicher Dateiname für Meldungen
                ziel.parent.mkdir()
                if not self.offen[program].kopie(ziel, fp):
                    self.offen_kandidat.pop(program, None)  # Stand ändert sich noch: nächste Prüfungen abwarten
                    return "offen_in_bearbeitung"
                kopien[program] = ziel
            try:
                neu = {p: self.read(path, wait_seconds=0, sleep=lambda _s: None) for p, path in kopien.items()}
            except SourceError as exc:
                self.log(f"Excel-Stand nicht lesbar ({self._clean(str(exc))}) – alter Stand bleibt")
                return "offen_in_bearbeitung"
            except PublicationError as exc:
                self.offen_basis.update(stabil)  # diesen Stand nicht erneut prüfen
                return self._invalid(exc, combined, "nach der nächsten Änderung")
        bisher = {p: self.gelesen[p][1] for p in PROGRAMME if p in self.gelesen}
        geaendert = [p for p, src in neu.items() if p not in bisher or _inhalt(src) != _inhalt(bisher[p])]
        if not geaendert:
            self.offen_basis.update(stabil)
            for p in stabil:
                self.offen_kandidat.pop(p, None)
            return "keine_aenderung"
        self.log(f"Excel-Stand geändert: {', '.join(geaendert)} – geöffnete Arbeitsmappe ohne neuen Speicherstand "
                 "wird über eine Kopie gelesen")
        try:
            sources = [neu.get(p) or self.gelesen[p][1] for p in PROGRAMME]
            for source, program in zip(sources, PROGRAMME):
                _check_program(source, program, OPTIONEN[program])
            if {s.public.get("event_id") for s in sources} != {self.slug}:
                raise PublicationError([Issue(SLUG_ABWEICHUNG, "Public!B2 entspricht nicht dem bestätigten Slug.")])
        except PublicationError as exc:
            self.offen_basis.update(stabil)
            return self._invalid(exc, combined, "nach der nächsten Änderung")
        status = self._veroeffentlichen(sources, combined, "nach der nächsten Änderung")
        if status != "wiederholung_geplant":  # Netzwerkfehler: gleicher Stand wird erneut versucht
            self.offen_basis.update(stabil)
            for p in stabil:
                self.offen_kandidat.pop(p, None)
        if status not in ("wiederholung_geplant", "validierung_fehler"):
            for i, p in enumerate(PROGRAMME):  # gilt bis zur nächsten Dateiänderung als aktueller Inhalt
                self.gelesen[p] = (combined[i], sources[i])
        return status

    def _invalid(self, exc: PublicationError, combined: tuple, wann: str = "nach der nächsten Dateiänderung") -> str:
        self.blocked = combined
        self.log(f"Validierung fehlgeschlagen ({', '.join(exc.codes)}) – alter Stand bleibt, neuer Versuch {wann}")
        return "validierung_fehler"

    def run(self, *, stop: Optional[threading.Event] = None, sleep: Callable[[float], None] = time.sleep,
            max_ticks: Optional[int] = None) -> None:
        ticks = 0
        while not (stop is not None and stop.is_set()):
            try:
                self.tick()
            except Exception as exc:  # Dauerbetrieb nie wegen eines Einzelfehlers beenden; nur den Typ melden
                self.failures += 1
                self.retry_at = self.monotonic() + min(self.poll * 2 ** (self.failures - 1), MAX_BACKOFF)
                self.log(f"Unerwarteter Fehler ({type(exc).__name__}) – neuer Versuch im nächsten Takt")
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return
            if stop is not None:
                stop.wait(self.poll)
            else:
                sleep(self.poll)


def _inhalt(source) -> str:
    """Fachlicher Inhalt einer gelesenen Mappe ohne Dateimerkmale (Name, Bytes, SHA-256 der Datei)."""
    daten = {k: v for k, v in asdict(source).items() if k != "source"}
    return hashlib.sha256(repr(daten).encode("utf-8")).hexdigest()


# ── Kommandozeile ────────────────────────────────────────────────────────────────────────────────────────

def _paths(env) -> dict[str, Path]:
    ak5, ak9 = local_config.workbook_paths(env)
    missing = [OPTIONEN[p] for p, v in zip(PROGRAMME, (ak5, ak9)) if v is None]
    if missing:
        raise local_config.LocalConfigError("Arbeitsmappen nicht konfiguriert: " + ", ".join(missing))
    return {"AK5-AK9+": ak5, "AK9-AK12": ak9}


def _check(paths: dict[str, Path], report: SafeReport) -> int:
    sources = []
    for program in PROGRAMME:
        source = read_program_source(paths[program], wait_seconds=2)
        _check_program(source, program, OPTIONEN[program])
        sources.append(source)
    state = build_state(sources, observed_at=datetime.now(timezone.utc))
    report.protect_state(state)
    report.out(f"Event-Slug:          {state['event']['slug']}")
    for p in state["programme"]:
        report.out(f"Programm:            {p['programm']} (Freigabe {'ja' if p['aktiv'] else 'nein'}, "
                   f"Rotation {p['rotation_freigegeben']}/{p['rotationen']})")
    report.out(f"Publikationszeilen:  {len(publication_rows(state))}")
    report.out("Status:              Prüfung erfolgreich – nichts geschrieben, kein Netzwerk")
    return 0


def main(argv: Optional[list[str]] = None, *, environ=None, out_dir: Optional[Path] = None,
         stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None, transport=None,
         stop: Optional[threading.Event] = None) -> int:
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    environ = os.environ if environ is None else environ
    out_dir = out_dir or local_config.PROJECT_ROOT / "out"
    parser = argparse.ArgumentParser(prog="python -B -m testtag_public.live_runner",
                                     description="P1F: Dauerpublisher für die beiden konfigurierten Arbeitsmappen.")
    parser.add_argument("--confirm-slug", help="Exakter Event-Slug (Public!B2) als Bestätigung für den Dauerbetrieb")
    parser.add_argument("--check", action="store_true", help="Beide Arbeitsmappen einmal prüfen, ohne Netzwerk")
    report = SafeReport(stdout, stderr, (environ.get(db.ENV_SECRET_KEY), environ.get(db.ENV_PUBLISHABLE_KEY)))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    log = None
    lock = None
    try:
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            report.err("FEHLER: openpyxl fehlt in dieser Python-Umgebung (python -m pip install -r requirements.txt).")
            return 1
        env = local_config.load_env(environ)
        report.secrets += tuple(s for s in (env.get(db.ENV_SECRET_KEY), env.get(db.ENV_PUBLISHABLE_KEY)) if s)
        poll = local_config.poll_seconds(env)
        paths = _paths(env)
        if args.check:
            return _check(paths, report)
        if not args.confirm_slug:
            report.err("FEHLER: --confirm-slug <Event-Slug> fehlt; nichts gestartet.")
            return 2
        config = db.load_config(env)
        lock = InstanceLock(lock_path(out_dir, paths))
        lock_state = lock.acquire()
        log = BetriebsLog(stdout, report, out_dir / LOG_NAME)
        log(f"Start: Dauerpublisher für {args.confirm_slug} (Programme {', '.join(PROGRAMME)}, Takt {poll} s, "
            f"Sperre {lock_state}). Beenden mit Ctrl+C.")
        offen = None
        if os.name == "nt" and excel_live.verfuegbar():
            offen = {p: excel_live.OffeneMappe(paths[p]) for p in PROGRAMME}
        else:
            log("Hinweis: Beobachtung geöffneter Excel-Mappen nicht verfügbar (nur Windows mit pywin32) – "
                "es zählt nur der Speicherstand der Dateien")
        runner = Runner(config, paths, confirm_slug=args.confirm_slug, poll=poll, log=log, report=report,
                        transport=SlugScope(transport, args.confirm_slug), offen=offen)
        try:
            runner.run(stop=stop)
            log("Ende: Dauerpublisher beendet")
        except KeyboardInterrupt:
            log("Ende: mit Ctrl+C beendet")
        return 0
    except PublicationError as exc:
        report.err(f"Validierung:         FEHLER ({', '.join(exc.codes)})")
        return exc.exit_code
    except ProbeError as exc:
        report.err(f"FEHLER: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        report.err(f"FEHLER: unerwarteter Fehler ({type(exc).__name__}).")
        return 1
    finally:
        if lock is not None:
            lock.release()
        if log is not None:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
