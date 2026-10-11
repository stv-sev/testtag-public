"""P1B-Prüflauf: liest die Arbeitsmappen eines Testtags nur lesend und zeigt ausschliesslich anonyme Kennzahlen.

    python -B -m testtag_public.publication_check --workbook "<AK5-AK9plus>.xlsx" --workbook "<AK9-AK12>.xlsx"

Ausgegeben werden nur Event-Slug, Programme, Anzahlen, Inhaltsprüfsummen und der Validierungsstatus –
keine Namen, Vereine, Jahrgänge, Einzelwertungen, Teilnehmerschlüssel, Inhalte oder absoluten Pfade.
Es wird keine Datei geschrieben und keine Netzwerkverbindung aufgebaut.

Exitcodes: 0 gültig · 1 unerwartet · 2 Aufruf · 3 Excelquelle · 4 Validierung (mit Fehlercode).
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TextIO

from .excel_probe import ProbeError, ValidationError
from .excel_publication import load_publication_state
from .publication_model import PublicationError, publication_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B -m testtag_public.publication_check",
        description="P1B-Prüflauf: Testtag-Arbeitsmappen nur lesend prüfen und anonyme Kennzahlen anzeigen.",
    )
    parser.add_argument("--workbook", action="append", required=True,
                        help="Pfad zu einer lokal synchronisierten .xlsx-Arbeitsmappe (mehrfach angeben)")
    parser.add_argument("--stability-seconds", type=float, default=2.0,
                        help="Wartezeit zwischen den beiden Stabilitätsprüfungen je Quelldatei (Standard: 2)")
    return parser


def summary_lines(state: dict, rows: list[dict]) -> list[str]:
    lines = [f"Event-Slug:        {state['event']['slug']}"]
    for program in state["programme"]:
        name = program["programm"]
        diag = state["diagnose"][name]
        visible = Counter(r["bereich"] for r in state["einzelwertungen"] if r["programm"] == name)
        lines.append(f"Programm:          {name} (öffentlich: {'ja' if program['aktiv'] else 'nein'}, "
                     f"Rotation {program['rotation_freigegeben']}/{program['rotationen']}, "
                     f"Kraft {program['kraftmodus']}, Beweglichkeit {program['beweglichkeitsmodus']})")
        lines.append(f"  Quelle SHA-256:  {program['quelle']['sha256']}")
        per_ak = ", ".join(f"{ak}: {n}" for ak, n in diag["teilnehmer_pro_ak"].items())
        lines.append(f"  Teilnehmer:      {diag['teilnehmer']} ({per_ak})")
        lines.append(f"  Sichtbar:        Technik {visible['technik']}, Beweglichkeit {visible['beweglichkeit']}, "
                     f"Kraft {visible['kraft']}")
        lines.append(f"  Zeitplanzeilen:  {diag['zeitplan_zeilen']}")
        lines.append(f"  Startliste:      {diag['startliste_eintraege']} Einträge")
    lines.append(f"Publikationszeilen: {len(rows)}")
    for row in rows:
        lines.append(f"  {row['bereich']:<20} {row['programm'] or '-':<9} {row['ak'] or '-':<6} {row['pruefsumme']}")
    return lines


def run(
    argv: Optional[list[str]] = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Callable[[], datetime]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    now = now or (lambda: datetime.now(timezone.utc))
    args = build_parser().parse_args(argv)
    try:
        if args.stability_seconds < 0:
            raise ValidationError("--stability-seconds darf nicht negativ sein.")
        state = load_publication_state([Path(p) for p in args.workbook], observed_at=now(),
                                       wait_seconds=args.stability_seconds, sleep=sleep)
        rows = publication_rows(state)
        for line in summary_lines(state, rows):
            print(line, file=stdout)
        print("Validierung:       OK", file=stdout)
        return 0
    except PublicationError as exc:
        print(f"Validierung:       FEHLER ({', '.join(exc.codes)})", file=stderr)
        print(str(exc), file=stderr)
        return exc.exit_code
    except ProbeError as exc:
        print(f"FEHLER: {exc}", file=stderr)
        return exc.exit_code
    except Exception as exc:  # unerwartet: keine Details, die Personendaten enthalten könnten
        print(f"FEHLER: unerwarteter Fehler ({type(exc).__name__}).", file=stderr)
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
