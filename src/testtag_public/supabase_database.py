"""Minimaler Zugriff auf die Supabase Data API (PostgREST) – nur Python-Standardbibliothek.

- Konfiguration ausschliesslich aus Umgebungsvariablen: SUPABASE_URL, SUPABASE_SECRET_KEY,
  SUPABASE_PUBLISHABLE_KEY.
- Nur die neuen Supabase-Schlüssel: Secret Key `sb_secret_…` (schreiben, lokal) und Publishable Key
  `sb_publishable_…` (lesen). Ältere JWT-Schlüssel (anon/service_role) werden nicht unterstützt.
- Schlüssel werden ausschliesslich im Header `apikey` gesendet – nie als `Authorization: Bearer`.
- Schlüssel erscheinen nie in Ausgaben, `repr()` oder Fehlermeldungen.
- Upserts laufen ausschliesslich über die vorgesehenen eindeutigen Schlüssel (`on_conflict`);
  Lese- und Schreibfilter adressieren immer den vollständigen Primärschlüssel.

Jede Netzwerkfunktion nimmt einen austauschbaren `transport`; Unit-Tests erzeugen so keinen Netzverkehr.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from .excel_probe import EVENT_ID_PATTERN, ProbeError

ENV_URL = "SUPABASE_URL"
ENV_SECRET_KEY = "SUPABASE_SECRET_KEY"
ENV_PUBLISHABLE_KEY = "SUPABASE_PUBLISHABLE_KEY"
SECRET_PREFIX = "sb_secret_"
PUBLISHABLE_PREFIX = "sb_publishable_"
TIMEOUT_SECONDS = 20.0
MAX_ERROR_DETAIL = 200

TABLE_TESTTAGE = "testtage"
TABLE_VEROEFFENTLICHUNG = "veroeffentlichung"
TESTTAGE_CONFLICT = ("slug",)
VEROEFFENTLICHUNG_CONFLICT = ("oeffentliche_testtag_id", "bereich", "programm", "ak")
VEROEFFENTLICHUNG_FIELDS = ("oeffentliche_testtag_id", "bereich", "programm", "ak", "pruefsumme", "stand_am", "inhalt")
BEREICH_PROBE = "probe"
PERMISSION_DENIED = "42501"

# transport(method, url, headers, body, timeout) -> (status, body)
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], "tuple[int, bytes]"]


class DatabaseConfigError(ProbeError):
    exit_code = 5


class DatabaseWriteError(ProbeError):
    exit_code = 6


class DatabaseReadError(ProbeError):
    """Rücklesen fehlgeschlagen oder Inhalt/Prüfsumme stimmen nicht überein."""

    exit_code = 7


class SecurityCheckError(ProbeError):
    """Der Publishable Key konnte schreiben – oder die Ablehnung ist nicht eindeutig nachgewiesen."""

    exit_code = 9


class TransportError(Exception):
    """Verbindungsfehler ohne HTTP-Antwort."""


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    secret_key: str = field(repr=False)
    publishable_key: str = field(repr=False)

    def __repr__(self) -> str:  # Schlüssel nie darstellen
        return f"DatabaseConfig(url={self.url!r}, secret_key=<redacted>, publishable_key=<redacted>)"

    __str__ = __repr__

    @property
    def secrets(self) -> tuple[str, str]:
        return (self.secret_key, self.publishable_key)


# ── Sicherheit: Schwärzen ─────────────────────────────────────────────────────────────────────────────

_KEY_PATTERN = re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_\-]+")


def redact(text: str, *secrets: Optional[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return _KEY_PATTERN.sub("<redacted>", text)


# ── Konfiguration ────────────────────────────────────────────────────────────────────────────────────

def load_config(env: Mapping[str, str]) -> DatabaseConfig:
    names = (ENV_URL, ENV_SECRET_KEY, ENV_PUBLISHABLE_KEY)
    missing = [name for name in names if not (env.get(name) or "").strip()]
    if missing:
        raise DatabaseConfigError(
            "Supabase-Konfiguration unvollständig. Fehlende Umgebungsvariablen: " + ", ".join(missing)
        )
    url = env[ENV_URL].strip().rstrip("/")
    secret_key = env[ENV_SECRET_KEY].strip()
    publishable_key = env[ENV_PUBLISHABLE_KEY].strip()

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query \
            or parsed.username or parsed.password:
        raise DatabaseConfigError(
            f"{ENV_URL} muss eine https-Projekt-URL ohne Pfad und ohne Zugangsdaten sein "
            "(z. B. https://<projekt-ref>.supabase.co)."
        )
    if not secret_key.startswith(SECRET_PREFIX) or len(secret_key) <= len(SECRET_PREFIX):
        raise DatabaseConfigError(f"{ENV_SECRET_KEY} muss ein Secret Key sein (beginnt mit {SECRET_PREFIX}).")
    if not publishable_key.startswith(PUBLISHABLE_PREFIX) or len(publishable_key) <= len(PUBLISHABLE_PREFIX):
        raise DatabaseConfigError(
            f"{ENV_PUBLISHABLE_KEY} muss ein Publishable Key sein (beginnt mit {PUBLISHABLE_PREFIX})."
        )
    for key in (secret_key, publishable_key):
        if any(ch.isspace() or ord(ch) < 32 for ch in key):
            raise DatabaseConfigError("Ein Supabase-Schlüssel enthält unzulässige Zeichen.")
    return DatabaseConfig(url=url, secret_key=secret_key, publishable_key=publishable_key)


# ── Kanonisches JSON ─────────────────────────────────────────────────────────────────────────────────

def canonical_json(value: Any) -> bytes:
    """Kanonische Form: sortierte Schlüssel, keine Leerzeichen, UTF-8, keine NaN/Infinity."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def json_equal(a: Any, b: Any) -> bool:
    """Semantischer Vergleich mit strenger Typprüfung (True ≠ 1, 1 ≠ 1.0, Reihenfolge von Objektfeldern egal)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────────────

def urllib_transport(method, url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        finally:
            exc.close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TransportError(f"{type(exc).__name__}: {reason}") from None


def _headers(api_key: str, *, prefer: Optional[str] = None, body: bool = False) -> dict:
    """Neue Supabase-Schlüssel nur im Header `apikey` – bewusst ohne `Authorization`."""
    headers = {"apikey": api_key, "Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if prefer:
        headers["Prefer"] = prefer
    return headers


def table_url(config: DatabaseConfig, table: str, params: list[tuple[str, str]]) -> str:
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe=",")
    return f"{config.url}/rest/v1/{urllib.parse.quote(table, safe='')}" + (f"?{query}" if query else "")


def _eq(value: str) -> str:
    return f"eq.{value}"


def _pk_filter(testtag_id: str, programm: str, ak: str = "") -> list[tuple[str, str]]:
    return [
        ("oeffentliche_testtag_id", _eq(testtag_id)),
        ("bereich", _eq(BEREICH_PROBE)),
        ("programm", _eq(programm)),
        ("ak", _eq(ak)),
    ]


def _detail(body: bytes, config: DatabaseConfig) -> str:
    text = body[:MAX_ERROR_DETAIL].decode("utf-8", errors="replace").replace("\n", " ")
    return redact(text, *config.secrets)


def _error_code(body: bytes) -> Optional[str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return str(data.get("code")) if isinstance(data, dict) and data.get("code") is not None else None


def _send(config: DatabaseConfig, transport, method, url, headers, body, error_cls, action):
    transport = transport or urllib_transport
    try:
        return transport(method, url, headers, body, TIMEOUT_SECONDS)
    except TransportError as exc:
        raise error_cls(f"{action} fehlgeschlagen (Verbindung): " + redact(str(exc), *config.secrets)) from None
    except Exception as exc:
        raise error_cls(f"{action} fehlgeschlagen ({type(exc).__name__}).") from None


def _validate_uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ProbeError("Ungültige Testtag-UUID.") from None


def _utc_text(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ── 1. Testtag anlegen/aktualisieren ─────────────────────────────────────────────────────────────────

def upsert_testtag(
    config: DatabaseConfig, *, slug: str, titel: str, ist_oeffentlich: bool, stand_am: datetime,
    transport: Transport | None = None,
) -> str:
    """Legt den Testtag über `slug` an oder aktualisiert ihn und liefert seine UUID.

    `status`, `datum` und `ort` werden bewusst nicht gesendet: beim Anlegen gelten die Vorgaben, beim
    Aktualisieren bleiben bestehende Werte erhalten."""
    if not EVENT_ID_PATTERN.match(slug or ""):
        raise DatabaseWriteError("Ungültiger Slug für den Testtag.")
    url = table_url(config, TABLE_TESTTAGE, [("on_conflict", ",".join(TESTTAGE_CONFLICT)), ("select", "id")])
    payload = {"slug": slug, "titel": titel, "ist_oeffentlich": bool(ist_oeffentlich), "stand_am": _utc_text(stand_am)}
    headers = _headers(config.secret_key, prefer="resolution=merge-duplicates,return=representation", body=True)
    status, body = _send(config, transport, "POST", url, headers, canonical_json(payload),
                         DatabaseWriteError, "Testtag schreiben")
    if status not in (200, 201):
        raise DatabaseWriteError(f"Testtag schreiben fehlgeschlagen: HTTP {status} – {_detail(body, config)}")
    try:
        rows = json.loads(body.decode("utf-8"))
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        return _validate_uuid(rows[0]["id"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, ProbeError):
        raise DatabaseWriteError("Testtag schreiben: unerwartete Antwort (keine eindeutige UUID).") from None


# ── 2. Probe-Datensatz einfügen/aktualisieren ────────────────────────────────────────────────────────

def probe_row(testtag_id: str, programm: str, inhalt: dict, stand_am: datetime) -> dict:
    return {
        "oeffentliche_testtag_id": _validate_uuid(testtag_id),
        "bereich": BEREICH_PROBE,
        "programm": programm,
        "ak": "",
        "pruefsumme": canonical_sha256(inhalt),
        "stand_am": _utc_text(stand_am),
        "inhalt": inhalt,
    }


def upsert_probe(config: DatabaseConfig, row: dict, transport: Transport | None = None) -> None:
    """Upsert in `veroeffentlichung` über den vollständigen Primärschlüssel."""
    url = table_url(config, TABLE_VEROEFFENTLICHUNG, [("on_conflict", ",".join(VEROEFFENTLICHUNG_CONFLICT))])
    headers = _headers(config.secret_key, prefer="resolution=merge-duplicates,return=minimal", body=True)
    status, body = _send(config, transport, "POST", url, headers, canonical_json(row),
                         DatabaseWriteError, "Probe-Datensatz schreiben")
    if status not in (200, 201, 204):
        raise DatabaseWriteError(f"Probe-Datensatz schreiben fehlgeschlagen: HTTP {status} – {_detail(body, config)}")


# ── 3. Mit Publishable Key zurücklesen und prüfen ────────────────────────────────────────────────────

def read_probe_public(
    config: DatabaseConfig, testtag_id: str, programm: str, transport: Transport | None = None
) -> dict:
    """Liest genau den Probe-Datensatz (vollständiger Primärschlüssel) mit dem Publishable Key."""
    params = [("select", ",".join(VEROEFFENTLICHUNG_FIELDS))] + _pk_filter(_validate_uuid(testtag_id), programm)
    url = table_url(config, TABLE_VEROEFFENTLICHUNG, params)
    status, body = _send(config, transport, "GET", url, _headers(config.publishable_key), None,
                         DatabaseReadError, "Rücklesen")
    if status != 200:
        raise DatabaseReadError(f"Rücklesen mit Publishable Key fehlgeschlagen: HTTP {status} – {_detail(body, config)}")
    try:
        rows = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise DatabaseReadError("Rücklesen: Antwort ist kein gültiges JSON.") from None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        count = len(rows) if isinstance(rows, list) else "?"
        raise DatabaseReadError(
            f"Rücklesen mit Publishable Key: {count} statt genau 1 Datensatz (Testtag öffentlich? RLS korrekt?)."
        )
    return rows[0]


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_probe(returned: dict, expected: dict) -> str:
    """Vergleicht den zurückgelesenen Datensatz semantisch; liefert die bestätigte Prüfsumme."""
    inhalt = returned.get("inhalt")
    if isinstance(inhalt, str):  # je nach Rückgabeform der Schnittstelle
        try:
            inhalt = json.loads(inhalt)
        except ValueError:
            raise DatabaseReadError("Rücklesen: `inhalt` ist kein gültiges JSON.") from None
    problems = []
    for key in ("bereich", "programm", "ak"):
        if returned.get(key) != expected[key]:
            problems.append(key)
    if str(returned.get("oeffentliche_testtag_id", "")).lower() != expected["oeffentliche_testtag_id"]:
        problems.append("oeffentliche_testtag_id")
    if _parse_ts(returned.get("stand_am")) != _parse_ts(expected["stand_am"]):
        problems.append("stand_am")
    if not json_equal(inhalt, expected["inhalt"]):
        problems.append("inhalt")
    recomputed = canonical_sha256(inhalt) if inhalt is not None else ""
    if returned.get("pruefsumme") != expected["pruefsumme"]:
        problems.append("pruefsumme (gespeichert)")
    if recomputed != expected["pruefsumme"]:
        problems.append("pruefsumme (neu berechnet)")
    if problems:
        raise DatabaseReadError("Zurückgelesener Datensatz weicht ab: " + ", ".join(problems))
    return recomputed


# ── 4. Schreibversuche mit Publishable Key (müssen abgelehnt werden) ─────────────────────────────────

def _write_attempts(config: DatabaseConfig, testtag_id: str, programm: str, pruefsumme: str):
    """Schreibversuche, die selbst bei fehlerhaften Rechten keine Daten verändern:

    1. INSERT in `veroeffentlichung` mit einer nicht existierenden Testtag-UUID (Fremdschlüssel würde scheitern);
    2. INSERT in `testtage` mit einem Slug, der die CHECK-Regel verletzt;
    3. UPDATE des Probe-Datensatzes über den vollständigen Primärschlüssel mit unverändertem Wert.
    """
    insert_headers = _headers(config.publishable_key, prefer="return=minimal", body=True)
    yield ("INSERT veroeffentlichung", "POST", table_url(config, TABLE_VEROEFFENTLICHUNG, []), insert_headers,
           canonical_json({"oeffentliche_testtag_id": str(uuid.uuid4()), "bereich": BEREICH_PROBE,
                           "programm": "__schreibtest__", "ak": "", "pruefsumme": "0" * 64,
                           "stand_am": "1970-01-01T00:00:00Z", "inhalt": {"schreibtest": True}}))
    yield ("INSERT testtage", "POST", table_url(config, TABLE_TESTTAGE, []), insert_headers,
           canonical_json({"slug": "__ungueltiger schreibtest__", "titel": "Schreibtest"}))
    yield ("UPDATE veroeffentlichung", "PATCH",
           table_url(config, TABLE_VEROEFFENTLICHUNG, _pk_filter(testtag_id, programm)),
           insert_headers, canonical_json({"pruefsumme": pruefsumme}))


def assert_publishable_write_rejected(
    config: DatabaseConfig, testtag_id: str, programm: str, pruefsumme: str, transport: Transport | None = None
) -> list[str]:
    """Erfolg nur, wenn jeder Versuch mit HTTP 401/403 und Postgres-Code 42501 (permission denied) endet."""
    testtag_id = _validate_uuid(testtag_id)
    confirmed = []
    for label, method, url, headers, body in _write_attempts(config, testtag_id, programm, pruefsumme):
        status, response = _send(config, transport, method, url, headers, body, SecurityCheckError,
                                 f"Schreibtest {label}")
        code = _error_code(response)
        if status in (401, 403) and code == PERMISSION_DENIED:
            confirmed.append(f"{label}: abgelehnt (HTTP {status}, {code})")
            continue
        if 200 <= status < 300:
            raise SecurityCheckError(
                f"SICHERHEIT: Schreibtest {label} mit Publishable Key wurde nicht abgelehnt (HTTP {status}). "
                "Tabellenrechte/RLS prüfen (supabase/schema.sql erneut ausführen)."
            )
        raise SecurityCheckError(
            f"Schreibtest {label}: Ablehnung nicht eindeutig nachgewiesen (HTTP {status}, Code {code or '–'}). "
            "Erwartet: HTTP 401/403 mit Code 42501."
        )
    return confirmed
