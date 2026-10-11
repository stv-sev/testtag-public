"""P0-Kommandozeile: Excel -> Diagnose-JSON [-> Supabase Postgres (Data API) -> Rücklesen mit Publishable Key].

    python -B -m testtag_public.probe --workbook "C:\\Pfad\\Datei.xlsx" --output "out\\probe.json" [--publish-probe]

Exitcodes: 0 Erfolg · 2 Aufruf · 3 Excelquelle · 4 Validierung · 5 Konfiguration · 6 Schreiben · 7 Rücklesen/Vergleich
· 8 Ausgabedatei · 9 Sicherheitsprüfung (Publishable Key konnte schreiben oder Ablehnung nicht nachgewiesen).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional, TextIO

from . import excel_probe, supabase_database as db
from .excel_probe import ProbeError, ValidationError, sha256_bytes


class OutputError(ProbeError):
    exit_code = 8


def write_atomic(path: Path, data: bytes) -> None:
    """Schreibt erst eine temporäre Datei daneben und ersetzt dann atomar – nie halb geschriebene Ausgabe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise OutputError(f"Ausgabedatei konnte nicht geschrieben werden ({type(exc).__name__}): {path.name}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B -m testtag_public.probe",
        description="P0-Diagnose: liest eine lokal synchronisierte Excel-Arbeitsmappe nur lesend und erzeugt ein bereinigtes JSON.",
    )
    parser.add_argument("--workbook", required=True, help="Pfad zur lokal synchronisierten .xlsx-Datei")
    parser.add_argument("--output", required=True, help="Pfad der lokalen JSON-Ausgabe")
    parser.add_argument(
        "--publish-probe", action="store_true",
        help="Diagnose als Probe-Datensatz in Supabase Postgres schreiben, mit Publishable Key zurücklesen "
             "und nachweisen, dass der Publishable Key nicht schreiben kann",
    )
    parser.add_argument(
        "--stability-seconds", type=float, default=2.0,
        help="Wartezeit zwischen den beiden Stabilitätsprüfungen der Quelldatei (Standard: 2)",
    )
    return parser


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
    env = os.environ if env is None else env
    now = now or (lambda: datetime.now(timezone.utc))
    args = build_parser().parse_args(argv)
    workbook = Path(args.workbook)
    output = Path(args.output)
    secrets = tuple((env.get(name) or "").strip() or None for name in (db.ENV_SECRET_KEY, db.ENV_PUBLISHABLE_KEY))

    def out(line: str) -> None:
        print(db.redact(line, *secrets), file=stdout)

    try:
        if args.stability_seconds < 0:
            raise ProbeError("--stability-seconds darf nicht negativ sein.")
        try:
            if output.resolve() == workbook.resolve():
                raise OutputError("Ausgabe darf nicht die Arbeitsmappe sein.")
        except OSError:
            pass
        if output.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
            raise OutputError("Ausgabe muss eine JSON-Datei sein.")

        # Konfiguration vor dem Lesen prüfen, damit ein Konfigurationsfehler früh und eindeutig ist.
        config = db.load_config(env) if args.publish_probe else None

        if excel_probe.lock_file_present(workbook):
            out("Hinweis: Excel-Sperrdatei vorhanden (Arbeitsmappe ist geöffnet). Gelesen wird der zuletzt gespeicherte Stand.")
        report = excel_probe.probe_workbook(workbook, wait_seconds=args.stability_seconds, sleep=sleep)
        data = excel_probe.serialize_report(report)
        noten = report["noteneingabe"]
        out(f"Quelle:            {report['source']['filename']} ({report['source']['size_bytes']} Bytes)")
        out(f"Quelle SHA-256:    {report['source']['sha256']}")
        out(f"Event/Programm:    {report['event']['event_id']} / {report['event']['program']}")
        out(f"Noteneingabe:      Dimension {noten['dimension']} (deklariert {noten['dimension_declared'] or '–'})")
        out(f"Formelzellen:      {noten['formula_cells']} (Resultat gespeichert: {noten['cached_nonempty_cells']}, leer: {noten['cached_empty_cells']})")
        out(f"Resultat-Hash:     {noten['cached_values_sha256']}")
        out(f"Zeitplan-Zeilen:   {report['zeitplan']['data_row_count']}")
        out(f"JSON SHA-256:      {sha256_bytes(data)}")

        if config is None:
            write_atomic(output, data)
            out(f"Ausgabe:           {output}")
            out("OK (nur lokal)")
            return 0

        event = report["event"]
        if not event["public_active"]:
            raise ValidationError(
                "Public!B6 (public_active) ist FALSCH: Der Probe-Datensatz wäre mit dem Publishable Key nicht "
                "lesbar. Für den P0-Nachweis muss public_active WAHR sein."
            )
        stand_am = now().astimezone(timezone.utc).replace(microsecond=0)
        testtag_id = db.upsert_testtag(
            config, slug=event["event_id"], titel=event["event_title"], ist_oeffentlich=event["public_active"],
            stand_am=stand_am, transport=transport,
        )
        out(f"Testtag-UUID:      {testtag_id}")
        row = db.probe_row(testtag_id, event["program"], report, stand_am)
        db.upsert_probe(config, row, transport)
        out(f"Probe geschrieben: bereich=probe, programm={row['programm']}, Prüfsumme {row['pruefsumme']}")
        returned = db.read_probe_public(config, testtag_id, event["program"], transport)
        confirmed = db.verify_probe(returned, row)
        out(f"Rücklesen:         Publishable Key, Inhalt semantisch gleich, Prüfsumme {confirmed}")
        for line in db.assert_publishable_write_rejected(config, testtag_id, event["program"], row["pruefsumme"], transport):
            out(f"Schreibschutz:     {line}")
        # Lokale Ausgabe erst nach allen bestandenen Prüfungen ersetzen.
        write_atomic(output, data)
        out(f"Ausgabe:           {output}")
        out("OK (geschrieben, zurückgelesen, Schreibschutz bestätigt)")
        return 0
    except ProbeError as exc:
        print("FEHLER: " + db.redact(str(exc), *secrets), file=stderr)
        return exc.exit_code
    except Exception as exc:  # unerwartet: keine Details, die Schlüssel enthalten könnten
        print(f"FEHLER: unerwarteter Fehler ({type(exc).__name__}).", file=stderr)
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
