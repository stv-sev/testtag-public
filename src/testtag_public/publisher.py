"""P1C-Kommandozeile: Publikationsstand aus beiden Arbeitsmappen erzeugen und optional nach Supabase schreiben.

    Probelauf (Standard, kein Netzwerk, keine Umgebungsvariablen):
    python -B -m testtag_public.publisher --workbook-ak5 "<AK5-AK9plus>.xlsx" --workbook-ak9 "<AK9-AK12>.xlsx"

    Echte Publikation (nur mit exakter Slug-Bestätigung):
    python -B -m testtag_public.publisher --workbook-ak5 … --workbook-ak9 … \
        --publish --confirm-slug aargau-lenzburg-2026-10-09

    Unveränderliche Archivierung (P1F, beide Bestätigungen nötig) bzw. Testtag verbergen:
    python -B -m testtag_public.publisher --archive --confirm-archive --confirm-slug <slug>
    python -B -m testtag_public.publisher --hide --confirm-slug <slug>

Konfiguration: Die lokale `.env` im Projektstamm wird automatisch geladen (Prozessvariablen haben Vorrang).
Ohne `--workbook-ak5/--workbook-ak9` gelten TESTTAG_WORKBOOK_AK5_AK9PLUS und TESTTAG_WORKBOOK_AK9_AK12.

Mehr als zwei Arbeitsmappen (Subdivisionen eines Programms, Public!B9): `--workbook <Mappe>` mehrfach angeben bzw.
TESTTAG_WORKBOOKS="<Mappe>;<Mappe>;…". Programm bzw. Subdivision steht dann in der Mappe selbst.

Ausgaben laufen ausschliesslich über `SafeReport`: Event-Slug, Programme, AK-Bezeichnungen, Anzahlen,
Bereiche, gekürzte Prüfsummen, HTTP-Status und stabile Fehlercodes – keine Namen, Vereine, Jahrgänge,
Teilnehmerschlüssel, Einzelwertungen, Inhalte, Request-Bodies, Schlüssel oder vollständigen Pfade.

Exitcodes: 0 Erfolg · 1 unerwartet · 2 Aufruf/Bestätigung · 3 Excelquelle · 4 Validierung · 5 Konfiguration
· 6 Schreiben · 7 Lesen/Rücklesen/Vergleich.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, TextIO

from . import local_config
from . import supabase_database as db
from .excel_probe import ProbeError
from .excel_publication import read_program_source
from .publication_model import Issue, PublicationError, build_state, publication_rows

USAGE_EXIT = 2
PROGRAMM_ZUORDNUNG = "PROGRAMM_ZUORDNUNG"
SHORT_HASH = 12
_PATHISH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|mnt|c)/)[^\s'\"]*")


class UsageError(ProbeError):
    exit_code = USAGE_EXIT


class SafeReport:
    """Zentrale, gefilterte Ausgabe. Schwärzt Schlüssel, Pfade und alle bekannten Personenangaben."""

    def __init__(self, stdout: TextIO, stderr: TextIO, secrets: Iterable[Optional[str]] = ()):
        self.stdout, self.stderr = stdout, stderr
        self.secrets = tuple(s for s in secrets if s)
        self.sensitive: list[str] = []

    def protect_state(self, state: Optional[dict]) -> None:
        """Alle Personenangaben und Teilnehmerschlüssel des Stands werden aus jeder Ausgabe entfernt."""
        if not state:
            return
        values = set()
        for groups in state.get("startlisten", {}).values():
            for group in groups:
                for p in group["teilnehmer"]:
                    values.update({p["name"], p["vorname"], p["verein"], p["teilnehmer"], p["teilnehmer"][:16]})
        for r in state.get("einzelwertungen", []):
            values.update({r["name"], r["vorname"], r["verein"], r["teilnehmer"], r["teilnehmer"][:16],
                           r["ergebnis_id"]})
        self.sensitive = sorted((v for v in values if isinstance(v, str) and len(v) >= 3), key=len, reverse=True)

    def clean(self, text: str) -> str:
        text = db.redact(str(text), *self.secrets)
        for value in self.sensitive:
            text = text.replace(value, "<entfernt>")
        return _PATHISH.sub("<pfad>", text)

    def out(self, text: str = "") -> None:
        print(self.clean(text), file=self.stdout)

    def err(self, text: str) -> None:
        print(self.clean(text), file=self.stderr)


def short(checksum: str) -> str:
    return checksum[:SHORT_HASH]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B -m testtag_public.publisher",
        description="P1C: Publikationsstand erzeugen (Standard: Probelauf ohne Netzwerk) und optional publizieren.",
    )
    parser.add_argument("--workbook-ak5", help="Arbeitsmappe des Programms AK5-AK9+ "
                                               f"(Standard: {local_config.ENV_WORKBOOK_AK5})")
    parser.add_argument("--workbook-ak9", help="Arbeitsmappe des Programms AK9-AK12 "
                                               f"(Standard: {local_config.ENV_WORKBOOK_AK9})")
    parser.add_argument("--workbook", action="append", metavar="MAPPE",
                        help="Arbeitsmappe (mehrfach; statt --workbook-ak5/--workbook-ak9, z. B. für Subdivisionen; "
                             f"Standard: {local_config.ENV_WORKBOOKS})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true",
                      help="Wirklich nach Supabase schreiben (verlangt --confirm-slug und Konfiguration)")
    mode.add_argument("--archive", action="store_true",
                      help="Testtag unveränderlich archivieren (verlangt --confirm-archive und --confirm-slug)")
    mode.add_argument("--hide", action="store_true", help="Testtag nicht mehr öffentlich anzeigen (--confirm-slug)")
    parser.add_argument("--confirm-archive", action="store_true", help="Bestätigung für --archive")
    parser.add_argument("--confirm-slug", help="Exakter Event-Slug als Bestätigung für --publish/--archive/--hide")
    parser.add_argument("--stability-seconds", type=float, default=2.0,
                        help="Wartezeit zwischen den beiden Stabilitätsprüfungen je Quelldatei (Standard: 2)")
    return parser


def _summary(report: SafeReport, state: dict, rows: list[dict]) -> None:
    report.out(f"Event-Slug:          {state['event']['slug']}")
    for program in state["programme"]:
        name = program["programm"]
        visible = Counter(r["bereich"] for r in state["einzelwertungen"] if r["programm"] == name)
        groups = len(state["ranglisten"].get(name, []))
        report.out(f"Programm:            {name} (Freigabe {'ja' if program['aktiv'] else 'nein'}, "
                   f"Rotation {program['rotation_freigegeben']}/{program['rotationen']})")
        report.out(f"  Sichtbar:          Technik {visible['technik']}, Beweglichkeit {visible['beweglichkeit']}, "
                   f"Kraft {visible['kraft']}")
        report.out(f"  Ranglistengruppen: {groups}")
    report.out(f"Publikationszeilen:  {len(rows)}")
    for row in rows:
        report.out(f"  {row['bereich']:<20} {row['programm'] or '-':<9} {row['ak'] or '-':<6} {short(row['pruefsumme'])}")


MAX_BLOCKIERT_MELDUNGEN = 10


def _blockiert(report: SafeReport, blockiert: list[dict]) -> None:
    """Fehlerhafte Programme: Name, Codes und die ersten Meldungen (Zelle, Test – ohne Namen)."""
    for b in blockiert:
        report.err(f"Blockiert:           {b['programm']} ({', '.join(b['codes'])}) – alter öffentlicher Stand bleibt")
        for text in b["meldungen"][:MAX_BLOCKIERT_MELDUNGEN]:
            report.err(f"  {text}")
        if len(b["meldungen"]) > MAX_BLOCKIERT_MELDUNGEN:
            report.err(f"  … und {len(b['meldungen']) - MAX_BLOCKIERT_MELDUNGEN} weitere Meldungen")


def _check_program(source, expected: str, option: str) -> None:
    if source.public.get("program") != expected:
        raise PublicationError([Issue(PROGRAMM_ZUORDNUNG, f"{option} muss das Programm {expected} enthalten "
                                                          f"(Public!B4); nichts geschrieben.")])


def _quellen(args, env) -> list[tuple[str, Path, Optional[str]]]:
    """Arbeitsmappen als (Option, Pfad, erwartetes Programm); Aufrufoptionen vor `.env`."""
    if args.workbook:
        if args.workbook_ak5 or args.workbook_ak9:
            raise UsageError("--workbook nicht zusammen mit --workbook-ak5/--workbook-ak9 angeben.")
        return [("--workbook", Path(p), None) for p in args.workbook]
    if args.workbook_ak5 or args.workbook_ak9:
        default_ak5, default_ak9 = local_config.workbook_paths(env)
        quellen = [("--workbook-ak5", args.workbook_ak5 or default_ak5, "AK5-AK9+"),
                   ("--workbook-ak9", args.workbook_ak9 or default_ak9, "AK9-AK12")]
    else:
        quellen = [(f"--workbook-ak5" if p == "AK5-AK9+" else "--workbook-ak9" if p == "AK9-AK12" else "--workbook",
                    path, p) for _k, path, p in local_config.workbook_sources(env)]
    if not all(path for _o, path, _p in quellen):
        raise UsageError("Arbeitsmappen fehlen: --workbook-ak5/--workbook-ak9 (bzw. --workbook) angeben oder "
                         f"{local_config.ENV_WORKBOOK_AK5}/{local_config.ENV_WORKBOOK_AK9} "
                         f"(bzw. {local_config.ENV_WORKBOOKS}) konfigurieren.")
    return [(o, Path(path), p) for o, path, p in quellen]


def run(
    argv: Optional[list[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport=None,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    now = now or (lambda: datetime.now(timezone.utc))
    process = os.environ if env is None else env
    report = SafeReport(stdout, stderr, (process.get(db.ENV_SECRET_KEY), process.get(db.ENV_PUBLISHABLE_KEY)))
    try:
        if env is None:  # echter Aufruf: lokale .env laden (Prozessvariablen haben Vorrang)
            env = local_config.load_env(os.environ)
            report.secrets += tuple(s for s in (env.get(db.ENV_SECRET_KEY), env.get(db.ENV_PUBLISHABLE_KEY)) if s)
        try:
            args = build_parser().parse_args(argv)
        except SystemExit as exc:
            return USAGE_EXIT if exc.code else 0
        if args.stability_seconds < 0:
            raise UsageError("--stability-seconds darf nicht negativ sein.")
        writing = args.publish or args.archive or args.hide
        if args.confirm_slug is not None and not writing:
            raise UsageError("--confirm-slug ist nur zusammen mit --publish, --archive oder --hide zulässig.")
        if writing and not args.confirm_slug:
            raise UsageError("--publish/--archive/--hide verlangt --confirm-slug <Event-Slug>.")
        if args.confirm_archive != args.archive:
            raise UsageError("--archive und --confirm-archive sind nur gemeinsam zulässig; nichts archiviert.")
        quellen = _quellen(args, env)
        # Konfiguration vor dem Lesen prüfen; im Probelauf werden keine Variablen benötigt.
        config = db.load_config(env) if writing else None

        sources = []
        for option, path, program in quellen:
            source = read_program_source(Path(path), wait_seconds=args.stability_seconds, sleep=sleep)
            if program is not None:
                _check_program(source, program, option)
            sources.append(source)
        observed = now()
        state = build_state(sources, observed_at=observed, teilweise=True)
        report.protect_state(state)
        rows = publication_rows(state)

        if not writing:
            _summary(report, state, rows)
            if state["blockiert"]:  # wie beim Publizieren: fehlerhafte Programme behalten ihren alten Stand
                _blockiert(report, state["blockiert"])
                report.out("Status:              Probelauf – nichts geschrieben; ein Programm ist blockiert")
                return PublicationError.exit_code
            report.out("Status:              Probelauf – nichts geschrieben")
            return 0

        if args.confirm_slug != state["event"]["slug"]:
            raise UsageError("--confirm-slug stimmt nicht exakt mit Public!B2 überein; nichts geschrieben.")
        from .write_scope import SlugScope  # Schreibanfragen nur für den bestätigten Testtag

        transport = SlugScope(transport, args.confirm_slug)
        if args.archive:
            return _run_archive(report, config, sources, observed, transport)
        if args.hide:
            return _run_hide(report, config, state["event"]["slug"], transport)
        from .database_publisher import publish  # erst hier: Probelauf lädt keinen Datenbankablauf

        outcome = publish(config, sources, observed_at=observed, transport=transport)
        report.protect_state(outcome.state)
        _summary(report, outcome.state, publication_rows(outcome.state))
        for action, status in outcome.http:
            report.out(f"  HTTP {status}  {action}")
        report.out(f"Zeilen:              {outcome.zeilen_gesamt} verwaltet, {outcome.geschrieben} geschrieben "
                   f"(davon {outcome.tombstones} Tombstones), {outcome.unveraendert} unverändert")
        report.out(f"Schreibanfragen:     {outcome.schreibanfragen}")
        report.out(f"Testtag öffentlich:  {'ja' if outcome.oeffentlich else 'nein'}"
                   f"{' (neu angelegt)' if outcome.testtag_neu else ''}")
        labels = {"publiziert": "publiziert und öffentlich geprüft", "unveraendert": "unverändert – nichts geschrieben",
                  "nichts_zu_publizieren": "kein Programm freigegeben – nichts geschrieben"}
        _blockiert(report, outcome.blockiert)
        teil = f" (teilweise – blockiert: {', '.join(b['programm'] for b in outcome.blockiert)})" \
            if outcome.blockiert else ""
        report.out(f"Status:              {labels[outcome.status]}{teil}")
        return 0
    except PublicationError as exc:
        report.err(f"Validierung:         FEHLER ({', '.join(exc.codes)}) – nichts geschrieben")
        report.err(str(exc))
        return exc.exit_code
    except ProbeError as exc:
        code = getattr(exc, "code", None)
        report.err(f"FEHLER{f' [{code}]' if code and f'[{code}]' not in str(exc) else ''}: {exc}")
        return exc.exit_code
    except Exception as exc:  # unerwartet: nur der Typ, nie der Text (könnte Inhalte enthalten)
        report.err(f"FEHLER: unerwarteter Fehler ({type(exc).__name__}).")
        return 1


def _run_archive(report: SafeReport, config, sources, observed, transport) -> int:
    from .archive import archive

    outcome = archive(config, sources, observed_at=observed, transport=transport)
    report.protect_state(outcome.state)
    for action, status in outcome.http:
        report.out(f"  HTTP {status}  {action}")
    if outcome.publikation is not None:
        report.out(f"Letzte Publikation:  {outcome.publikation.status}, "
                   f"{outcome.publikation.geschrieben} Zeilen geschrieben")
    labels = {"archiviert": "archiviert – Snapshot neu eingefügt und geprüft",
              "fortgesetzt": "archiviert – bestehender Snapshot bestätigt, Status abgeschlossen",
              "bereits_archiviert": "bereits archiviert – identischer Snapshot, nichts geschrieben"}
    report.out(f"Snapshot:            {outcome.eintraege} Einträge, Prüfsumme {short(outcome.pruefsumme)}")
    report.out(f"Schreibanfragen:     {outcome.schreibanfragen}")
    report.out(f"Testtag öffentlich:  {'ja' if outcome.oeffentlich else 'nein'}")
    report.out(f"Status:              {labels[outcome.status]}")
    return 0


def _run_hide(report: SafeReport, config, slug: str, transport) -> int:
    from .archive import hide

    was_public, http, writes = hide(config, slug, transport=transport)
    for action, status in http:
        report.out(f"  HTTP {status}  {action}")
    report.out(f"Schreibanfragen:     {writes}")
    report.out(f"Status:              nicht öffentlich{' (war öffentlich)' if was_public else ' (unverändert)'}")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
