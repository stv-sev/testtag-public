"""P1F: lokale Konfiguration aus `.env` im Projektstamm – nur Python-Standardbibliothek.

Format (eine Zuweisung pro Zeile, `#` am Zeilenanfang = Kommentar):

    SUPABASE_URL=https://<projekt>.supabase.co
    TESTTAG_WORKBOOK_AK5_AK9PLUS="C:\\Pfad mit Leerzeichen\\AK5.xlsx"

Mehrere Arbeitsmappen (z. B. Subdivisionen eines Programms): statt der beiden Einzelpfade
`TESTTAG_WORKBOOKS="C:\\…\\AK5-AK7.xlsx;C:\\…\\AK8-AK9plus.xlsx;C:\\…\\AK9-AK12.xlsx"` (durch `;` getrennt).

- Nur bekannte Variablen (`KNOWN`); unbekannte, doppelte oder syntaktisch ungültige Einträge brechen ab.
- Werte ohne Anführungszeichen dürfen keine Leerzeichen, Anführungszeichen oder `#` enthalten; in `"…"` oder
  `'…'` wird alles wörtlich übernommen (auch `\\` in Windows-Pfaden). Keine Variablenersetzung, keine
  Befehlsausführung, keine mehrzeiligen Werte.
- Gesetzte Prozessvariablen haben Vorrang vor `.env`.
- Fehlermeldungen nennen Zeilennummer und Variablenname, nie einen Wert.

`python -B -m testtag_public.local_config --init` legt `.env` einmalig aus der Prozessumgebung an (nur wenn sie
fehlt und Git sie nachweislich ausschliesst) und gibt keine Werte aus.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

from .excel_probe import ProbeError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ".env"
ENV_URL = "SUPABASE_URL"
ENV_PUBLISHABLE_KEY = "SUPABASE_PUBLISHABLE_KEY"
ENV_SECRET_KEY = "SUPABASE_SECRET_KEY"
ENV_WORKBOOK_AK5 = "TESTTAG_WORKBOOK_AK5_AK9PLUS"
ENV_WORKBOOK_AK9 = "TESTTAG_WORKBOOK_AK9_AK12"
ENV_WORKBOOKS = "TESTTAG_WORKBOOKS"  # beliebig viele Mappen, durch ";" getrennt (statt der beiden Einzelpfade)
ENV_POLL = "TESTTAG_POLL_SECONDS"
SUPABASE_VARS = (ENV_URL, ENV_PUBLISHABLE_KEY, ENV_SECRET_KEY)
KNOWN = (*SUPABASE_VARS, ENV_WORKBOOK_AK5, ENV_WORKBOOK_AK9, ENV_WORKBOOKS, ENV_POLL)
POLL_DEFAULT, POLL_MIN, POLL_MAX = 1, 1, 60
MAX_FILE_BYTES = 64 * 1024

_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*?)[ \t]*$")


class LocalConfigError(ProbeError):
    """Lokale Konfiguration ungültig. Meldungen enthalten nie Werte."""

    exit_code = 5


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            raise LocalConfigError(f".env Zeile {number}: erwartet NAME=WERT.")
        name, value = match.group(1), match.group(2)
        if name not in KNOWN:
            raise LocalConfigError(f".env Zeile {number}: unbekannte Variable {name}.")
        if name in values:
            raise LocalConfigError(f".env Zeile {number}: {name} ist doppelt.")
        if value[:1] in ('"', "'"):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote or quote in value[1:-1]:
                raise LocalConfigError(f".env Zeile {number}: {name} hat ungültige Anführungszeichen "
                                       "(mehrzeilige Werte werden nicht unterstützt).")
            value = value[1:-1]
        elif any(ch in value for ch in "\"'# \t"):
            raise LocalConfigError(f".env Zeile {number}: {name} enthält Leerzeichen, Anführungszeichen oder '#' – "
                                   "Wert in Anführungszeichen setzen.")
        if any(ord(ch) < 32 for ch in value):
            raise LocalConfigError(f".env Zeile {number}: {name} enthält Steuerzeichen.")
        values[name] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise LocalConfigError(".env ist unerwartet gross.")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        raise LocalConfigError(".env ist nicht UTF-8-kodiert.") from None
    except OSError as exc:
        raise LocalConfigError(f".env ist nicht lesbar ({type(exc).__name__}).") from None
    return parse_env(text)


def load_env(environ: Mapping[str, str], root: Optional[Path] = None) -> dict[str, str]:
    """Bekannte Variablen: Prozessumgebung (falls gesetzt) vor `.env`. Andere Variablen werden nicht übernommen."""
    file_values = read_env_file(Path(root or PROJECT_ROOT) / ENV_FILE)
    merged = {}
    for name in KNOWN:
        value = environ.get(name)
        if value is not None and value.strip():
            merged[name] = value
        elif name in file_values and file_values[name].strip():
            merged[name] = file_values[name]
    return merged


def poll_seconds(env: Mapping[str, str]) -> int:
    raw = (env.get(ENV_POLL) or "").strip()
    if not raw:
        return POLL_DEFAULT
    if not re.fullmatch(r"\d{1,2}", raw) or not POLL_MIN <= int(raw) <= POLL_MAX:
        raise LocalConfigError(f"{ENV_POLL} muss eine ganze Zahl von {POLL_MIN} bis {POLL_MAX} sein.")
    return int(raw)


def workbook_paths(env: Mapping[str, str]) -> tuple[Optional[Path], Optional[Path]]:
    def path(name: str) -> Optional[Path]:
        value = (env.get(name) or "").strip()
        return Path(value) if value else None
    return path(ENV_WORKBOOK_AK5), path(ENV_WORKBOOK_AK9)


def workbook_sources(env: Mapping[str, str]) -> list[tuple[str, Optional[Path], Optional[str]]]:
    """Konfigurierte Arbeitsmappen als (Kennung, Pfad, erwartetes Programm).

    `TESTTAG_WORKBOOKS` (beliebig viele, Programm bzw. Subdivision aus Public!B4/B9) oder – wie bisher – die beiden
    Einzelpfade mit festem Programm. Beides zugleich ist mehrdeutig und wird abgewiesen."""
    liste = [Path(p.strip()) for p in (env.get(ENV_WORKBOOKS) or "").split(";") if p.strip()]
    ak5, ak9 = workbook_paths(env)
    if liste:
        if ak5 is not None or ak9 is not None:
            raise LocalConfigError(f"{ENV_WORKBOOKS} und {ENV_WORKBOOK_AK5}/{ENV_WORKBOOK_AK9} nicht gemeinsam "
                                   "setzen (eine der beiden Angaben entfernen).")
        return [(f"Mappe {i}", p, None) for i, p in enumerate(liste, start=1)]
    return [("AK5-AK9+", ak5, "AK5-AK9+"), ("AK9-AK12", ak9, "AK9-AK12")]


# ── Einmalige Anlage von .env ────────────────────────────────────────────────────────────────────────────

def git_ignores(root: Path, name: str = ENV_FILE, run: Callable = subprocess.run) -> bool:
    try:
        result = run(["git", "check-ignore", "-q", "--", name], cwd=str(root), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _restrict_to_current_user(path: Path, run: Callable = subprocess.run) -> bool:
    """Windows: Vererbung entfernen, nur der aktuelle Benutzer erhält Vollzugriff. Andere Systeme: 0600."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False
    user = os.environ.get("USERNAME")
    if not user:
        return False
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    try:
        result = run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"], capture_output=True,
                     timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_env_file(environ: Mapping[str, str], root: Optional[Path] = None, *, run: Callable = subprocess.run) -> str:
    """Legt `.env` an, falls sie fehlt und die Supabase-Variablen gesetzt sind. Liefert einen Status ohne Werte."""
    root = Path(root or PROJECT_ROOT)
    target = root / ENV_FILE
    if target.exists():
        return "vorhanden"
    if not all((environ.get(name) or "").strip() for name in SUPABASE_VARS):
        return "fehlt"
    if not git_ignores(root, ENV_FILE, run):
        raise LocalConfigError(".env wird von Git nicht nachweislich ausgeschlossen; nichts angelegt.")
    lines = ["# Lokale Konfiguration – nie versionieren, nie weitergeben.", ""]
    names = list(SUPABASE_VARS)
    for name in (ENV_WORKBOOK_AK5, ENV_WORKBOOK_AK9):
        value = (environ.get(name) or "").strip()
        if value and Path(value).is_file():
            names.append(name)
    for name in names:
        value = environ[name].strip()
        if '"' in value or any(ord(ch) < 32 for ch in value):
            raise LocalConfigError(f"{name} enthält Zeichen, die in .env nicht abgelegt werden können; nichts angelegt.")
        lines.append(f'{name}="{value}"')
    if ENV_WORKBOOK_AK5 not in names:
        lines.append(f'# {ENV_WORKBOOK_AK5}="<absoluter lokaler Pfad>"')
    if ENV_WORKBOOK_AK9 not in names:
        lines.append(f'# {ENV_WORKBOOK_AK9}="<absoluter lokaler Pfad>"')
    lines.append(f"{ENV_POLL}={POLL_DEFAULT}")
    content = "\n".join(lines) + "\n"
    parse_env(content)  # Selbstprüfung vor dem Schreiben
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return "angelegt" if _restrict_to_current_user(target, run) else "angelegt (Berechtigungen nicht eingeschränkt)"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -B -m testtag_public.local_config",
                                     description="Lokale .env prüfen oder einmalig aus der Prozessumgebung anlegen.")
    parser.add_argument("--init", action="store_true", help=".env aus der Prozessumgebung anlegen, falls sie fehlt")
    args = parser.parse_args(argv)
    try:
        if args.init:
            print(f".env: {ensure_env_file(os.environ)}")
        env = load_env(os.environ)
        for name in KNOWN:
            print(f"{name:<30} {'gesetzt' if name in env else 'fehlt'}")
        poll_seconds(env)
        return 0
    except LocalConfigError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
