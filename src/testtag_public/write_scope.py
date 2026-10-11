"""P1G: Schreibschutz auf Transportebene – Schreibanfragen nur für den ausdrücklich bestätigten Testtag.

`SlugScope` umhüllt den Transport von Publisher, Archivierung und Dauerpublisher. Lesende Anfragen werden
durchgereicht; aus Antworten von `testtage` lernt die Sperre die UUID des bestätigten Slugs. Schreibanfragen
werden VOR dem Senden geprüft und sonst abgewiesen (`TransportError`, nichts gesendet):

- `POST testtage`: nur mit genau diesem Slug;
- `PATCH testtage`: nur über `id=eq.<UUID des bestätigten Slugs>`;
- `POST veroeffentlichung` / `POST archiv_snapshots`: jede Zeile mit dieser Testtag-UUID;
- alles andere (DELETE, PATCH auf Publikations- oder Archivzeilen, fremde Tabellen, `Authorization`): abgewiesen.

Damit kann ein Lauf mit `--confirm-slug X` keinen anderen Testtag verändern – auch nicht bei einem Fehler im
übrigen Code.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional

from . import supabase_database as db

WRITE_TABLES = ("veroeffentlichung", "archiv_snapshots")


class SlugScope:
    def __init__(self, inner: Optional[db.Transport], slug: str):
        self.inner = inner or db.urllib_transport
        self.slug = slug
        self.ids: set[str] = set()
        self.blocked = 0

    def _block(self, reason: str):
        self.blocked += 1
        raise db.TransportError(f"Schreibschutz: {reason} – Anfrage nicht gesendet")

    def _learn(self, payload: bytes) -> None:
        try:
            rows = json.loads(payload)
        except (ValueError, TypeError):
            return
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("slug") == self.slug and row.get("id"):
                self.ids.add(str(row["id"]).lower())

    def __call__(self, method, url, headers, body, timeout):
        if any(str(k).lower() == "authorization" for k in headers):
            self._block("Authorization-Header")
        parts = urllib.parse.urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if method == "GET":
            status, payload = self.inner(method, url, headers, body, timeout)
            if table == "testtage" and status == 200:
                self._learn(payload)
            return status, payload
        filters = [(k, v) for k, v in params if k not in ("select", "on_conflict")]
        try:
            data = json.loads(body) if body else None
        except (ValueError, TypeError):
            self._block("Anfrage nicht auswertbar")
        if method == "POST" and table == "testtage":
            allowed = not filters and isinstance(data, dict) and data.get("slug") == self.slug
        elif method == "PATCH" and table == "testtage":
            allowed = len(filters) == 1 and filters[0][0] == "id" and filters[0][1].startswith("eq.") \
                and filters[0][1][3:].lower() in self.ids
        elif method == "POST" and table in WRITE_TABLES:
            rows = data if isinstance(data, list) else [data]
            allowed = not filters and bool(rows) and all(
                isinstance(r, dict) and str(r.get("oeffentliche_testtag_id", "")).lower() in self.ids for r in rows)
        else:
            allowed = False
        if not allowed:
            self._block(f"{method} {table} ausserhalb des bestätigten Testtags")
        status, payload = self.inner(method, url, headers, body, timeout)
        if method == "POST" and table == "testtage" and status in (200, 201):
            self._learn(payload)
        return status, payload
