"""P1F: unveränderliche Archivierung eines Testtags in `archiv_snapshots`.

Ablauf (`archive`):

1. Beide Arbeitsmappen validieren (vor jedem Netzwerkzugriff); mindestens ein Programm muss freigegeben sein.
2. Testtag und einen allfälligen bestehenden Snapshot mit dem Secret Key lesen.
3. Nur wenn noch kein Snapshot besteht und der Testtag nicht archiviert ist: letzte Änderungen normal
   publizieren (P1C, inkl. öffentlichem Rücklesen).
4. Aktive verwaltete Zeilen lesen, Prüfsummen kontrollieren, kanonischen Archivinhalt bilden.
5. Snapshot einmalig einfügen – oder einen bestehenden Snapshot exakt bestätigen (idempotent). Ein abweichender
   Snapshot bricht mit `ARCHIV_ABWEICHUNG` ab. Es gibt kein UPDATE und kein DELETE.
6. Snapshot mit dem Secret Key zurücklesen und prüfen.
7. Erst danach `testtage.status = 'archiviert'`.
8. Öffentlich (Publishable Key) zurücklesen und prüfen, sofern der Testtag öffentlich ist.

Ein Abbruch zwischen 5 und 7 wird beim nächsten Lauf fortgesetzt (Snapshot bestätigen, Status abschliessen).
Nach `archiviert` lehnt der Publisher jede weitere Publikation ab (`TESTTAG_ARCHIVIERT`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import database_publisher as dp
from . import supabase_database as db
from .publication_model import SCHEMA_VERSION, build_state, content_checksum, parse_utc, utc_text

ARCHIV_SCHEMA_VERSION = "archiv-1"
TABLE_ARCHIV = "archiv_snapshots"
ARCHIV_FIELDS = ("oeffentliche_testtag_id", "schema_version", "pruefsumme", "inhalt")
ARCHIV_TESTTAG_FIELDS = ("id", "slug", "titel", "datum", "ort")
ENTRY_FIELDS = frozenset({"bereich", "programm", "ak", "pruefsumme", "stand_am", "inhalt"})


class ArchivCode:
    ARCHIV_LEER = "ARCHIV_LEER"
    ARCHIV_TESTTAG_FEHLT = "ARCHIV_TESTTAG_FEHLT"
    ARCHIV_ABWEICHUNG = "ARCHIV_ABWEICHUNG"
    ARCHIV_UNGUELTIG = "ARCHIV_UNGUELTIG"
    ARCHIV_INKONSISTENT = "ARCHIV_INKONSISTENT"


class ArchiveFormatError(ValueError):
    """Archivinhalt ungültig; `code` ist stabil, die Meldung enthält keine Inhalte."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ── Reine Logik (auch von quelle.py verwendet) ───────────────────────────────────────────────────────────

def archive_checksum(inhalt: Any) -> str:
    """SHA-256 über den vollständigen kanonischen Archivinhalt (inklusive aller Zeitpunkte)."""
    return db.canonical_sha256(inhalt)


def build_archive(testtag: dict, rows: list[dict]) -> dict:
    """Kanonischer Archivinhalt: Testtag-Metadaten und alle aktiven verwalteten Zeilen (ohne Probe/Tombstones)."""
    entries = []
    for row in rows:
        inhalt = dp._inhalt(row)
        if row.get("bereich") not in dp.MANAGED_BEREICHE or dp.is_tombstone(inhalt):
            continue
        entries.append({"bereich": row["bereich"], "programm": row["programm"], "ak": row["ak"],
                        "pruefsumme": row["pruefsumme"], "stand_am": utc_text(parse_utc(str(row["stand_am"]))),
                        "inhalt": inhalt})
    entries.sort(key=lambda e: (e["bereich"], e["programm"], e["ak"]))
    meta = {k: testtag.get(k) for k in ARCHIV_TESTTAG_FIELDS}
    meta["id"] = str(meta["id"]).lower()
    return {"schema_version": ARCHIV_SCHEMA_VERSION, "testtag": meta, "veroeffentlichung": entries}


def verify_archive(inhalt: Any, testtag_id: str) -> list[dict]:
    """Strenge Prüfung eines Archivinhalts; liefert die Einträge. Wirft `ArchiveFormatError`."""
    if not isinstance(inhalt, dict) or inhalt.get("schema_version") != ARCHIV_SCHEMA_VERSION:
        raise ArchiveFormatError("SCHEMA")
    meta = inhalt.get("testtag")
    if set(inhalt) != {"schema_version", "testtag", "veroeffentlichung"} or not isinstance(meta, dict) \
            or str(meta.get("id", "")).lower() != str(testtag_id).lower():
        raise ArchiveFormatError("TESTTAG")
    entries = inhalt.get("veroeffentlichung")
    if not isinstance(entries, list) or not entries:
        raise ArchiveFormatError("FORMAT")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise ArchiveFormatError("FORMAT")
        key = (entry["bereich"], entry["programm"], entry["ak"])
        if key in seen:
            raise ArchiveFormatError("DOPPELT")
        seen.add(key)
        if entry["bereich"] not in dp.MANAGED_BEREICHE:
            raise ArchiveFormatError("BEREICH")
        body = entry["inhalt"]
        if not isinstance(body, dict) or body.get("schema_version") != SCHEMA_VERSION:
            raise ArchiveFormatError("SCHEMA")
        if dp.is_tombstone(body):
            raise ArchiveFormatError("TOMBSTONE")
        if not isinstance(entry["pruefsumme"], str) or content_checksum(body) != entry["pruefsumme"]:
            raise ArchiveFormatError("PRUEFSUMME")
        if not isinstance(body.get("gruppen"), list):
            raise ArchiveFormatError("FORMAT")
    return entries


def verify_snapshot_row(row: dict, testtag_id: str) -> dict:
    """Gespeicherte Snapshot-Zeile: Testtag-ID, Schema, Prüfsumme über den kanonischen Inhalt, Archivformat."""
    inhalt = row.get("inhalt")
    if isinstance(inhalt, str):
        try:
            inhalt = json.loads(inhalt)
        except ValueError:
            raise ArchiveFormatError("JSON") from None
    if str(row.get("oeffentliche_testtag_id", "")).lower() != str(testtag_id).lower():
        raise ArchiveFormatError("TESTTAG")
    if row.get("schema_version") != ARCHIV_SCHEMA_VERSION:
        raise ArchiveFormatError("SCHEMA")
    if not isinstance(row.get("pruefsumme"), str) or archive_checksum(inhalt) != row["pruefsumme"]:
        raise ArchiveFormatError("PRUEFSUMME")
    verify_archive(inhalt, testtag_id)
    return inhalt


# ── Data API ─────────────────────────────────────────────────────────────────────────────────────────────

def _error(code: str, message: str, exit_code: int = 7) -> dp.PublisherError:
    return dp.PublisherError(code, message, exit_code)


def _fetch_testtag(config, slug: str, transport, http: list) -> Optional[dict]:
    fields = (*dp.TESTTAG_FIELDS, "datum", "ort")
    url = db.table_url(config, db.TABLE_TESTTAGE, [("select", ",".join(fields)), ("slug", f"eq.{slug}")])
    status, payload = dp._request(config, transport, "GET", url, config.secret_key, None, "Testtag lesen", http)
    if status != 200:
        raise dp._read_error(dp.PublisherCode.DB_LESEN, dp._failure("Testtag lesen", status, payload))
    rows = dp._json_rows(payload, "Testtag lesen")
    if len(rows) > 1:
        raise dp._read_error(dp.PublisherCode.TESTTAG_MEHRDEUTIG, "Mehrere Testtage mit demselben Slug.")
    if rows:
        rows[0]["id"] = db._validate_uuid(rows[0].get("id"))
    return rows[0] if rows else None


def fetch_snapshot(config, testtag_id: str, transport, http: list, *, public: bool) -> Optional[dict]:
    url = db.table_url(config, TABLE_ARCHIV, [("select", ",".join(ARCHIV_FIELDS)),
                                              ("oeffentliche_testtag_id", f"eq.{testtag_id}")])
    action = "Archiv öffentlich lesen" if public else "Archiv lesen"
    key = config.publishable_key if public else config.secret_key
    status, payload = dp._request(config, transport, "GET", url, key, None, action, http)
    if status != 200:
        raise dp._read_error(dp.PublisherCode.DB_LESEN, dp._failure(action, status, payload))
    rows = dp._json_rows(payload, action)
    if len(rows) > 1:
        raise _error(ArchivCode.ARCHIV_INKONSISTENT, "Mehr als ein Snapshot für denselben Testtag.")
    return rows[0] if rows else None


def insert_snapshot(config, testtag_id: str, inhalt: dict, summe: str, transport, http: list) -> int:
    """Einmaliges INSERT (kein Upsert). Liefert den HTTP-Status; 409 = Snapshot existiert bereits."""
    url = db.table_url(config, TABLE_ARCHIV, [("select", ",".join(ARCHIV_FIELDS))])
    body = db.canonical_json({"oeffentliche_testtag_id": testtag_id, "schema_version": ARCHIV_SCHEMA_VERSION,
                              "pruefsumme": summe, "inhalt": inhalt})
    status, payload = dp._request(config, transport, "POST", url, config.secret_key, body, "Archiv einfügen", http,
                                  prefer="return=representation", write=True)
    if status == 409:
        return status
    if status not in (200, 201):
        raise dp._write_error(dp.PublisherCode.DB_SCHREIBEN, dp._failure("Archiv einfügen", status, payload))
    rows = dp._json_rows(payload, "Archiv einfügen")
    if len(rows) != 1 or rows[0].get("pruefsumme") != summe:
        raise dp._write_error(dp.PublisherCode.DB_SCHREIBEN, "Archiv einfügen: Antwort bestätigt den Snapshot nicht.")
    return status


def _same_snapshot(row: dict, testtag_id: str, inhalt: dict, summe: str) -> bool:
    try:
        stored = verify_snapshot_row(row, testtag_id)
    except ArchiveFormatError:
        return False
    return row.get("pruefsumme") == summe and db.json_equal(stored, inhalt)


# ── Gesamtablauf ─────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class ArchivOutcome:
    status: str  # archiviert | bereits_archiviert | fortgesetzt
    slug: str
    snapshot_neu: bool = False
    pruefsumme: str = ""
    eintraege: int = 0
    schreibanfragen: int = 0
    oeffentlich: bool = False
    publikation: Optional[dp.Outcome] = None
    http: list[tuple[str, int]] = field(default_factory=list)
    state: Optional[dict] = None


def archive(config: db.DatabaseConfig, sources: list, *, observed_at: datetime | str, transport=None) -> ArchivOutcome:
    stamp = utc_text(observed_at)
    state = build_state(sources, observed_at=stamp)  # 1. Validierung ohne Netzwerk
    slug = state["event"]["slug"]
    if not any(p["aktiv"] for p in state["programme"]):
        raise _error(ArchivCode.ARCHIV_LEER, "Kein Programm freigegeben – ein leerer Stand wird nicht archiviert.", 4)
    http: list[tuple[str, int]] = []
    writes = 0

    tag = _fetch_testtag(config, slug, transport, http)  # 2.
    if tag is None:
        raise _error(ArchivCode.ARCHIV_TESTTAG_FEHLT, "Testtag existiert nicht; zuerst publizieren.")
    tid = tag["id"]
    existing = fetch_snapshot(config, tid, transport, http, public=False)
    already = tag.get("status") == "archiviert"
    if already and existing is None:
        raise _error(ArchivCode.ARCHIV_INKONSISTENT, "Testtag ist archiviert, aber ohne Snapshot; nichts geschrieben.")

    published = None
    if existing is None and not already:  # 3. letzte Änderungen normal publizieren
        published = dp.publish(config, sources, observed_at=stamp, transport=transport)
        http.extend(published.http)
        writes += published.schreibanfragen
        tag = _fetch_testtag(config, slug, transport, http)

    rows = dp.fetch_rows(config, tid, transport, http, public=False)  # 4.
    dp.check_existing(rows)
    for row in rows:
        if row.get("pruefsumme") != content_checksum(dp._inhalt(row)):
            raise _error(ArchivCode.ARCHIV_UNGUELTIG, f"Zeile {row.get('bereich')}/{row.get('programm') or '-'}/"
                                                      f"{row.get('ak') or '-'}: Prüfsumme stimmt nicht; nichts archiviert.")
    inhalt = build_archive(tag, rows)
    try:
        verify_archive(inhalt, tid)
    except ArchiveFormatError as exc:
        raise _error(ArchivCode.ARCHIV_UNGUELTIG, f"Archivinhalt ungültig ({exc.code}); nichts archiviert.") from None
    summe = archive_checksum(inhalt)

    created = False
    if existing is None:  # 5.
        writes += 1
        if insert_snapshot(config, tid, inhalt, summe, transport, http) == 409:
            existing = fetch_snapshot(config, tid, transport, http, public=False)
        else:
            created = True
    if existing is not None and not _same_snapshot(existing, tid, inhalt, summe):
        raise _error(ArchivCode.ARCHIV_ABWEICHUNG, "Bestehender Snapshot weicht vom aktuellen Stand ab; "
                                                   "nichts überschrieben, Status unverändert.")

    stored = fetch_snapshot(config, tid, transport, http, public=False)  # 6.
    if stored is None or not _same_snapshot(stored, tid, inhalt, summe):
        raise _error(ArchivCode.ARCHIV_ABWEICHUNG, "Zurückgelesener Snapshot weicht ab; Status unverändert.")

    if not already:  # 7.
        writes += 1
        tag = dp.patch_testtag(config, tid, {"status": "archiviert"}, transport, http, "Testtag archivieren")

    public = tag.get("ist_oeffentlich") is True  # 8.
    visible = dp.fetch_testtag(config, slug, transport, http, public=True)
    public_snapshot = fetch_snapshot(config, tid, transport, http, public=True)
    if public:
        if visible is None or visible.get("status") != "archiviert" or public_snapshot is None \
                or not _same_snapshot(public_snapshot, tid, inhalt, summe):
            raise _error(ArchivCode.ARCHIV_ABWEICHUNG, "Öffentlich gelesener Snapshot fehlt oder weicht ab.")
    elif visible is not None or public_snapshot is not None:
        raise _error(ArchivCode.ARCHIV_ABWEICHUNG, "Nicht öffentlicher Snapshot ist mit dem Publishable Key lesbar.")

    status = "archiviert" if created else ("bereits_archiviert" if already else "fortgesetzt")
    return ArchivOutcome(status, slug, snapshot_neu=created, pruefsumme=summe,
                         eintraege=len(inhalt["veroeffentlichung"]), schreibanfragen=writes, oeffentlich=public,
                         publikation=published, http=http, state=state)


def hide(config: db.DatabaseConfig, slug: str, *, transport=None) -> tuple[bool, list, int]:
    """Testtag nicht mehr öffentlich (auch archiviert). Liefert (vorher_öffentlich, http, Schreibanfragen)."""
    http: list[tuple[str, int]] = []
    tag = dp.fetch_testtag(config, slug, transport, http)
    if tag is None:
        raise _error(ArchivCode.ARCHIV_TESTTAG_FEHLT, "Testtag existiert nicht.")
    writes = 0
    was_public = tag.get("ist_oeffentlich") is True
    if tag.get("ist_oeffentlich") is not False:
        writes += 1
        dp.patch_testtag(config, tag["id"], {"ist_oeffentlich": False}, transport, http, "Testtag verbergen")
    if dp.fetch_testtag(config, slug, transport, http, public=True) is not None \
            or dp.fetch_rows(config, tag["id"], transport, http, public=True) \
            or fetch_snapshot(config, tag["id"], transport, http, public=True) is not None:
        raise _error(dp.PublisherCode.RUECKLESEN_ABWEICHUNG, "Testtag ist mit dem Publishable Key noch lesbar.")
    return was_public, http, writes
