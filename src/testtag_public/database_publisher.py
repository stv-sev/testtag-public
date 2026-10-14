"""P1C: einmaliger Datenbank-Publisher für den P1B-Publikationsstand (Supabase Data API).

Ablauf einer echten Publikation (`publish`):

1. Excel-Stand validieren (P1B `build_state`) – vor jedem Netzwerkzugriff.
2. Testtag (über den Slug) und die verwalteten Publikationszeilen mit dem Secret Key lesen.
3. Vorherigen Live-Stand aus `resultate_live` rekonstruieren und mit `merge_live` zusammenführen.
4. Gewünschten Gesamtstand berechnen; nicht mehr gewünschte verwaltete Zeilen werden zu Tombstones. Ein
   fehlerhaftes Programm wird blockiert: seine bisherigen Zeilen (und sein Block im Zeitplan) bleiben unverändert,
   die übrigen Programme werden publiziert (`build_state(teilweise=True)`).
5. Nur neue oder geänderte Zeilen bestimmen (Primärschlüssel, Prüfsumme und kanonischer Inhalt).
6. Alle Änderungen in genau einem Batch-Upsert schreiben (eine PostgREST-Anfrage, ein SQL-Statement).
7. Mit dem Publishable Key öffentlich zurücklesen und vollständig prüfen.
8. Erst danach `testtage.stand_am` aktualisieren (bei vollständiger Sperre zugleich `ist_oeffentlich = false`).
9. Öffentlichen Gesamtstand erneut prüfen.

Ist nichts geändert, wird keine Schreibanfrage gesendet. Verwaltet werden nur die Bereiche `zeitplan`,
`startliste`, `resultate_live` und `resultate_rangliste`; `probe` bleibt unberührt.

HTTP läuft ausschliesslich über `supabase_database` (Header `apikey`, nie `Authorization`). Fehlermeldungen
enthalten nur HTTP-Status und Postgres-Code, nie Teile der Serverantwort, Schlüssel oder Personendaten.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import supabase_database as db
from .excel_probe import ProbeError
import re

from .publication_model import (
    SCHEMA_VERSION, ProgramSource, _row, build_state, canonical_json, content_checksum, parse_utc, publication_rows,
    utc_text,
)

MANAGED_BEREICHE = ("zeitplan", "startliste", "resultate_live", "resultate_rangliste")
TESTTAG_FIELDS = ("id", "slug", "titel", "status", "ist_oeffentlich", "stand_am")
ROW_FIELDS = db.VEROEFFENTLICHUNG_FIELDS
PK_FIELDS = db.VEROEFFENTLICHUNG_CONFLICT
TOMBSTONE_KEYS = frozenset({"schema_version", "art", "aktiv", "gruppen"})


class PublisherCode:
    SCHEMA_INKOMPATIBEL = "SCHEMA_INKOMPATIBEL"
    TESTTAG_ARCHIVIERT = "TESTTAG_ARCHIVIERT"
    TESTTAG_MEHRDEUTIG = "TESTTAG_MEHRDEUTIG"
    DB_LESEN = "DB_LESEN"
    DB_SCHREIBEN = "DB_SCHREIBEN"
    BATCH_UNVOLLSTAENDIG = "BATCH_UNVOLLSTAENDIG"
    RUECKLESEN_ABWEICHUNG = "RUECKLESEN_ABWEICHUNG"


class PublisherError(ProbeError):
    """Datenbankfehler mit stabilem Code; die Meldung enthält keine Schlüssel, Inhalte oder Personendaten."""

    def __init__(self, code: str, message: str, exit_code: int):
        self.code = code
        self.exit_code = exit_code
        super().__init__(f"[{code}] {message}")


def _read_error(code: str, message: str) -> PublisherError:
    return PublisherError(code, message, db.DatabaseReadError.exit_code)


def _write_error(code: str, message: str) -> PublisherError:
    return PublisherError(code, message, db.DatabaseWriteError.exit_code)


# ── Reine Planung ────────────────────────────────────────────────────────────────────────────────────

Key = tuple[str, str, str]  # (bereich, programm, ak)


def row_key(row: dict) -> Key:
    return (row["bereich"], row["programm"], row["ak"])


def tombstone_inhalt(bereich: str) -> dict:
    """Inhaltsleerer Ersatz für eine nicht mehr gewünschte verwaltete Zeile (kein DELETE-Recht)."""
    return {"schema_version": SCHEMA_VERSION, "art": bereich, "aktiv": False, "gruppen": []}


def is_tombstone(inhalt: Any) -> bool:
    return isinstance(inhalt, dict) and inhalt.get("aktiv") is False


def _inhalt(row: dict) -> Any:
    value = row.get("inhalt")
    if isinstance(value, str):  # je nach Rückgabeform der Schnittstelle
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def check_existing(rows: list[dict]) -> None:
    """Vorhandene verwaltete Zeilen müssen die aktuelle schema_version besitzen – sonst kein Überschreiben."""
    for row in rows:
        inhalt = _inhalt(row)
        if not isinstance(inhalt, dict) or inhalt.get("schema_version") != SCHEMA_VERSION:
            raise _read_error(PublisherCode.SCHEMA_INKOMPATIBEL,
                              f"Vorhandene Zeile {row.get('bereich')}/{row.get('programm') or '-'}/"
                              f"{row.get('ak') or '-'} hat eine unbekannte schema_version; nichts geschrieben.")


def previous_live(rows: list[dict]) -> list[dict]:
    """Bisherige Einzelwertungen (mit `geaendert_am`) aus den vorhandenen `resultate_live`-Zeilen."""
    results = []
    for row in rows:
        inhalt = _inhalt(row)
        if row.get("bereich") != "resultate_live" or is_tombstone(inhalt):
            continue
        try:
            for group in inhalt["gruppen"]:
                for result in group["resultate"]:
                    if not isinstance(result.get("ergebnis_id"), str):
                        raise ValueError
                    parse_utc(result["geaendert_am"])
                    results.append(result)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise _read_error(PublisherCode.SCHEMA_INKOMPATIBEL,
                              f"Vorhandene Live-Zeile {row.get('programm')}/{row.get('ak')} ist nicht lesbar; "
                              "nichts geschrieben.") from None
    return results


@dataclass
class Plan:
    final: dict[Key, tuple[dict, str]]  # gewünschter Endzustand aller verwalteten Schlüssel (Inhalt, Prüfsumme)
    changed: list[Key]
    tombstones: list[Key]
    unchanged: list[Key]


def _same(existing: Optional[dict], inhalt: dict, pruefsumme: str) -> bool:
    if existing is None or existing.get("pruefsumme") != pruefsumme:
        return False
    current = _inhalt(existing)
    return current is not None and canonical_json(current) == canonical_json(inhalt)


def _programm_rang(programm: str) -> tuple:
    zahl = re.search(r"\d+", programm or "")
    return (int(zahl.group()) if zahl else 999, programm or "")


def blockierte(state: dict) -> set[str]:
    return {b["programm"] for b in state.get("blockiert", [])}


def plan_changes(state: dict, existing: list[dict]) -> Plan:
    desired = {row_key(r): (r["inhalt"], r["pruefsumme"]) for r in publication_rows(state)}
    by_key = {row_key(r): r for r in existing}
    gesperrt = blockierte(state)
    # Blockierte Programme behalten ihren bisherigen Block im gemeinsamen Zeitplan.
    alt = _inhalt(by_key[("zeitplan", "", "")]) if ("zeitplan", "", "") in by_key else None
    behalten = [g for g in (alt or {}).get("gruppen", []) if isinstance(g, dict) and g.get("programm") in gesperrt] \
        if not is_tombstone(alt) else []
    if behalten:
        gruppen = [g for g in state["zeitplan"]] + behalten
        row = _row("zeitplan", "", "", "zeitplan", sorted(gruppen, key=lambda g: _programm_rang(g.get("programm"))))
        desired[("zeitplan", "", "")] = (row["inhalt"], row["pruefsumme"])
    final = dict(desired)
    tombstones = []
    for key in sorted(by_key):
        if key[1] in gesperrt and key not in desired:  # blockiertes Programm: bisherige Zeile bleibt unverändert
            final[key] = (_inhalt(by_key[key]), by_key[key]["pruefsumme"])
            continue
        if key not in desired:
            inhalt = tombstone_inhalt(key[0])
            final[key] = (inhalt, content_checksum(inhalt))
            tombstones.append(key)
    changed = sorted(k for k, (inhalt, summe) in final.items() if not _same(by_key.get(k), inhalt, summe))
    unchanged = sorted(k for k in final if k not in changed)
    return Plan(final, changed, [k for k in tombstones if k in changed], unchanged)


# ── Data API (über supabase_database) ────────────────────────────────────────────────────────────────

def _request(config: db.DatabaseConfig, transport, method: str, url: str, key: str, body: Optional[bytes],
             action: str, http: list, *, prefer: Optional[str] = None, write: bool = False) -> tuple[int, bytes]:
    headers = db._headers(key, prefer=prefer, body=body is not None)
    try:
        status, payload = db._send(config, transport, method, url, headers, body, db.DatabaseWriteError, action)
    except ProbeError as exc:
        make = _write_error if write else _read_error
        raise make(PublisherCode.DB_SCHREIBEN if write else PublisherCode.DB_LESEN,
                   db.redact(str(exc), *config.secrets)) from None
    http.append((action, status))
    return status, payload


def _failure(action: str, status: int, payload: bytes) -> str:
    code = db._error_code(payload)
    return f"{action} fehlgeschlagen: HTTP {status}" + (f", Code {code}" if code else "")


def _json_rows(payload: bytes, action: str) -> list[dict]:
    try:
        rows = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _read_error(PublisherCode.DB_LESEN, f"{action}: Antwort ist kein gültiges JSON.") from None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise _read_error(PublisherCode.DB_LESEN, f"{action}: unerwartete Antwortform.")
    return rows


def _rows_filter(testtag_id: str) -> list[tuple[str, str]]:
    return [("oeffentliche_testtag_id", f"eq.{testtag_id}"), ("bereich", f"in.({','.join(MANAGED_BEREICHE)})")]


def fetch_testtag(config, slug: str, transport, http: list, *, public: bool = False) -> Optional[dict]:
    url = db.table_url(config, db.TABLE_TESTTAGE, [("select", ",".join(TESTTAG_FIELDS)), ("slug", f"eq.{slug}")])
    action = "Testtag öffentlich lesen" if public else "Testtag lesen"
    key = config.publishable_key if public else config.secret_key
    status, payload = _request(config, transport, "GET", url, key, None, action, http)
    if status != 200:
        raise _read_error(PublisherCode.DB_LESEN, _failure(action, status, payload))
    rows = _json_rows(payload, action)
    if len(rows) > 1:
        raise _read_error(PublisherCode.TESTTAG_MEHRDEUTIG, "Mehrere Testtage mit demselben Slug.")
    if rows:
        rows[0]["id"] = db._validate_uuid(rows[0].get("id"))
    return rows[0] if rows else None


def fetch_rows(config, testtag_id: str, transport, http: list, *, public: bool) -> list[dict]:
    url = db.table_url(config, db.TABLE_VEROEFFENTLICHUNG, [("select", ",".join(ROW_FIELDS))] + _rows_filter(testtag_id))
    action = "Publikation öffentlich lesen" if public else "Publikation lesen"
    key = config.publishable_key if public else config.secret_key
    status, payload = _request(config, transport, "GET", url, key, None, action, http)
    if status != 200:
        raise _read_error(PublisherCode.DB_LESEN, _failure(action, status, payload))
    rows = _json_rows(payload, action)
    for row in rows:
        if row.get("bereich") not in MANAGED_BEREICHE or \
                str(row.get("oeffentliche_testtag_id", "")).lower() != testtag_id:
            raise _read_error(PublisherCode.DB_LESEN, f"{action}: Antwort enthält fremde Zeilen.")
    return rows


def insert_testtag(config, slug: str, titel: str, transport, http: list) -> dict:
    """Neuanlage: nicht öffentlich und `vorbereitet`, bis der erste Batch vollständig geprüft ist."""
    url = db.table_url(config, db.TABLE_TESTTAGE, [("select", ",".join(TESTTAG_FIELDS))])
    body = canonical_json({"slug": slug, "titel": titel, "status": "vorbereitet", "ist_oeffentlich": False})
    status, payload = _request(config, transport, "POST", url, config.secret_key, body, "Testtag anlegen", http,
                               prefer="return=representation", write=True)
    if status not in (200, 201):
        raise _write_error(PublisherCode.DB_SCHREIBEN, _failure("Testtag anlegen", status, payload))
    rows = _json_rows(payload, "Testtag anlegen")
    if len(rows) != 1:
        raise _write_error(PublisherCode.DB_SCHREIBEN, "Testtag anlegen: keine eindeutige Antwort.")
    rows[0]["id"] = db._validate_uuid(rows[0].get("id"))
    return rows[0]


def patch_testtag(config, testtag_id: str, fields: dict, transport, http: list, action: str) -> dict:
    url = db.table_url(config, db.TABLE_TESTTAGE, [("select", ",".join(TESTTAG_FIELDS)), ("id", f"eq.{testtag_id}")])
    status, payload = _request(config, transport, "PATCH", url, config.secret_key, canonical_json(fields), action,
                               http, prefer="return=representation", write=True)
    if status != 200:
        raise _write_error(PublisherCode.DB_SCHREIBEN, _failure(action, status, payload))
    rows = _json_rows(payload, action)
    if len(rows) != 1 or any(rows[0].get(k) != v for k, v in fields.items() if k != "stand_am"):
        raise _write_error(PublisherCode.DB_SCHREIBEN, f"{action}: Antwort bestätigt die Änderung nicht.")
    return rows[0]


def upsert_batch(config, rows: list[dict], transport, http: list) -> None:
    """Alle geänderten Zeilen in genau einer Anfrage (JSON-Array, Upsert über den vollständigen Primärschlüssel)."""
    url = db.table_url(config, db.TABLE_VEROEFFENTLICHUNG,
                       [("on_conflict", ",".join(PK_FIELDS)), ("select", ",".join((*PK_FIELDS, "pruefsumme")))])
    status, payload = _request(config, transport, "POST", url, config.secret_key, canonical_json(rows),
                               "Batch-Upsert", http, prefer="resolution=merge-duplicates,return=representation",
                               write=True)
    if status not in (200, 201):
        raise _write_error(PublisherCode.DB_SCHREIBEN, _failure("Batch-Upsert", status, payload))
    try:
        returned = _json_rows(payload, "Batch-Upsert")
    except PublisherError:
        raise _write_error(PublisherCode.BATCH_UNVOLLSTAENDIG, "Batch-Upsert: Antwort nicht auswertbar.") from None
    expected = {(r["oeffentliche_testtag_id"], *row_key(r)): r["pruefsumme"] for r in rows}
    got = {}
    for r in returned:
        try:
            got[(str(r["oeffentliche_testtag_id"]).lower(), *row_key(r))] = r.get("pruefsumme")
        except KeyError:
            raise _write_error(PublisherCode.BATCH_UNVOLLSTAENDIG, "Batch-Upsert: Antwortzeile ohne Schlüssel.") from None
    if len(returned) != len(rows) or got != expected:
        missing = len(set(expected) - set(got))
        extra = len(set(got) - set(expected))
        raise _write_error(PublisherCode.BATCH_UNVOLLSTAENDIG,
                           f"Batch-Upsert bestätigt {len(returned)} statt {len(rows)} Zeilen "
                           f"(fehlend {missing}, unerwartet {extra}, Prüfsummen abweichend "
                           f"{sum(1 for k in set(got) & set(expected) if got[k] != expected[k])}).")


# ── Prüfung des öffentlich gelesenen Stands ──────────────────────────────────────────────────────────

def verify_rows(returned: list[dict], expected: dict[Key, dict], state: dict) -> None:
    """Zeilenanzahl, Schlüssel, Prüfsumme, kanonischer Inhalt, `stand_am`, Tombstones, Rotation, Sperre."""
    problems: list[str] = []
    by_key = {}
    for row in returned:
        key = row_key(row)
        if key in by_key:
            problems.append(f"{'/'.join(key)}: doppelt")
        by_key[key] = row
    if len(returned) != len(expected) or set(by_key) != set(expected):
        problems.append(f"{len(returned)} statt {len(expected)} Zeilen oder abweichende Schlüssel")
    limits = {p["programm"]: p["rotation_freigegeben"] for p in state["programme"]}
    active = {p["programm"] for p in state["programme"] if p["aktiv"]}
    gesperrt = blockierte(state)  # behaltene Zeilen blockierter Programme: bei ihrer Publikation geprüft
    for key, exp in expected.items():
        row = by_key.get(key)
        if row is None:
            continue
        inhalt = _inhalt(row)
        label = "/".join(k or "-" for k in key)
        if row.get("pruefsumme") != exp["pruefsumme"] or inhalt is None or content_checksum(inhalt) != exp["pruefsumme"]:
            problems.append(f"{label}: Prüfsumme")
        if not db.json_equal(inhalt, exp["inhalt"]):
            problems.append(f"{label}: Inhalt")
        stamp = row.get("stand_am")
        try:
            if parse_utc(str(stamp)) != parse_utc(exp["stand_am"]):
                problems.append(f"{label}: stand_am")
        except ValueError:
            problems.append(f"{label}: stand_am")
        if is_tombstone(inhalt):
            if set(inhalt) != TOMBSTONE_KEYS or inhalt.get("gruppen") != []:
                problems.append(f"{label}: Tombstone enthält Daten")
            continue
        if key[1] in gesperrt:
            continue
        if key[1] and key[1] not in active:
            problems.append(f"{label}: Inhalt eines gesperrten Programms")
        if key[0] == "resultate_live" and isinstance(inhalt, dict):
            for group in inhalt.get("gruppen", []):
                for result in group.get("resultate", []):
                    rotation = result.get("rotation")
                    if rotation is not None and rotation > limits.get(key[1], -1):
                        problems.append(f"{label}: nicht freigegebene Rotation")
                        break
    if problems:
        raise _read_error(PublisherCode.RUECKLESEN_ABWEICHUNG,
                          "Öffentlich gelesener Stand weicht ab: " + "; ".join(problems[:10]))


# ── Gesamtablauf ─────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    status: str  # publiziert | unveraendert | nichts_zu_publizieren
    slug: str
    testtag_neu: bool = False
    oeffentlich: bool = False
    zeilen_gesamt: int = 0
    geschrieben: int = 0
    tombstones: int = 0
    unveraendert: int = 0
    schreibanfragen: int = 0
    pruefsummen: list[tuple[str, str, str, str]] = field(default_factory=list)  # (bereich, programm, ak, summe)
    http: list[tuple[str, int]] = field(default_factory=list)
    state: Optional[dict] = None
    blockiert: list[dict] = field(default_factory=list)  # fehlerhafte Programme (Name, Codes, Meldungen)


def publish(config: db.DatabaseConfig, sources: list[ProgramSource], *, observed_at: datetime | str,
            transport=None) -> Outcome:
    stamp = utc_text(observed_at)
    # 1. Vollständige Validierung ohne Netzwerk.
    offline = build_state(sources, observed_at=stamp, teilweise=True)
    slug, titel = offline["event"]["slug"], offline["event"]["titel"]
    # blockierte Programme zeigen ihren bisherigen Stand weiter: zählen nicht als Sperre
    any_active = any(p["aktiv"] for p in offline["programme"]) or bool(offline["blockiert"])
    http: list[tuple[str, int]] = []
    writes = 0

    # 2. Bestehenden Stand lesen.
    testtag = fetch_testtag(config, slug, transport, http)
    if testtag and testtag.get("status") == "archiviert":
        raise PublisherError(PublisherCode.TESTTAG_ARCHIVIERT, "Testtag ist archiviert; nichts geschrieben.", 4)
    existing = fetch_rows(config, testtag["id"], transport, http, public=False) if testtag else []
    check_existing(existing)
    if testtag is None and not any_active:
        return Outcome("nichts_zu_publizieren", slug, http=http, state=offline, blockiert=offline["blockiert"])

    # 3./4./5. Live-Zeitpunkte zusammenführen, Endzustand und Änderungen bestimmen.
    state = build_state(sources, observed_at=stamp, previous={"einzelwertungen": previous_live(existing)},
                        teilweise=True)
    plan = plan_changes(state, existing)
    existing_by_key = {row_key(r): r for r in existing}
    meta: dict[str, Any] = {}
    if testtag:
        if testtag.get("titel") != titel:
            meta["titel"] = titel
        if any_active and testtag.get("status") != "live":
            meta["status"] = "live"
        if any_active and testtag.get("ist_oeffentlich") is not True:
            meta["ist_oeffentlich"] = True
    lock = bool(testtag) and not any_active and testtag.get("ist_oeffentlich") is not False

    def outcome(status: str, created: bool, public: bool) -> Outcome:
        return Outcome(status, slug, testtag_neu=created, oeffentlich=public, zeilen_gesamt=len(plan.final),
                       geschrieben=len(plan.changed) if status == "publiziert" else 0,
                       tombstones=len(plan.tombstones) if status == "publiziert" else 0,
                       unveraendert=len(plan.unchanged) if status == "publiziert" else len(plan.final),
                       schreibanfragen=writes,
                       pruefsummen=[(k[0], k[1], k[2], s) for k, (_i, s) in sorted(plan.final.items())],
                       http=http, state=state, blockiert=state["blockiert"])

    expected = {}
    for key, (inhalt, summe) in plan.final.items():
        old = existing_by_key.get(key)
        expected[key] = {"inhalt": inhalt, "pruefsumme": summe,
                         "stand_am": stamp if key in plan.changed or old is None else old["stand_am"]}

    if testtag and not plan.changed and not meta and not lock:
        public = testtag.get("ist_oeffentlich") is True
        _verify_public(config, testtag, expected, state, public, transport, http)
        return outcome("unveraendert", False, public)

    created = False
    if testtag is None:
        testtag = insert_testtag(config, slug, titel, transport, http)
        writes += 1
        created = True
        meta = {"titel": titel, "status": "live", "ist_oeffentlich": True}
    testtag_id = testtag["id"]

    # 6. Ein Batch für alle Änderungen.
    if plan.changed:
        payload = [{"oeffentliche_testtag_id": testtag_id, "bereich": k[0], "programm": k[1], "ak": k[2],
                    "pruefsumme": plan.final[k][1], "stand_am": stamp, "inhalt": plan.final[k][0]}
                   for k in plan.changed]
        try:
            writes += 1
            upsert_batch(config, payload, transport, http)
        except PublisherError as exc:
            note = (" Der Testtag wurde neu angelegt, ist aber nicht öffentlich (Status vorbereitet)."
                    if created else "")
            raise PublisherError(exc.code, f"{str(exc).split('] ', 1)[-1]} Der neue Stand wurde NICHT vollständig "
                                           f"publiziert; testtage.stand_am unverändert.{note}", exc.exit_code) from None

    # Sichtbarkeit/Metadaten (vor dem Rücklesen, damit der Publishable Key lesen kann).
    if meta:
        writes += 1
        testtag = patch_testtag(config, testtag_id, meta, transport, http, "Testtag freigeben")
    public = testtag.get("ist_oeffentlich") is True

    # 7. Öffentlich zurücklesen.
    _verify_public(config, testtag, expected, state, public, transport, http)

    # 8. Erst jetzt stand_am (bei vollständiger Sperre zugleich nicht mehr öffentlich).
    final_fields: dict[str, Any] = {"stand_am": stamp}
    if not any_active:
        final_fields["ist_oeffentlich"] = False
    writes += 1
    testtag = patch_testtag(config, testtag_id, final_fields, transport, http, "Testtag Stand setzen")
    public = testtag.get("ist_oeffentlich") is True

    # 9. Öffentlichen Gesamtstand erneut prüfen.
    _verify_public(config, testtag, expected, state, public, transport, http)
    return outcome("publiziert", created, public)


def _verify_public(config, testtag: dict, expected: dict, state: dict, public: bool, transport, http: list) -> None:
    """Öffentlicher Nachweis ausschliesslich mit dem Publishable Key."""
    visible_tag = fetch_testtag(config, testtag["slug"], transport, http, public=True)
    rows = fetch_rows(config, testtag["id"], transport, http, public=True)
    if public:
        if visible_tag is None or visible_tag["id"] != testtag["id"]:
            raise _read_error(PublisherCode.RUECKLESEN_ABWEICHUNG, "Testtag ist öffentlich nicht lesbar.")
        verify_rows(rows, expected, state)
        return
    if visible_tag is not None or rows:
        raise _read_error(PublisherCode.RUECKLESEN_ABWEICHUNG,
                          "Nicht öffentlicher Testtag ist mit dem Publishable Key dennoch lesbar.")
    # Nicht öffentlich: der Inhalt (z. B. Tombstones) wird zusätzlich intern geprüft.
    verify_rows(fetch_rows(config, testtag["id"], transport, http, public=False), expected, state)
