# Prototyp: statische Live-Ansicht (Branch `prototyp-live-statisch`)

Ziel: 50 gleichzeitige Zuschauer der Live-Ansicht zuverlässig bedienen, ohne Streamlit-Prozess je Zuschauer.
Rangliste, Startliste, Zeitplan und Archiv bleiben in Streamlit (Links auf `testtag.streamlit.app/?t=<slug>`).
`main`, die Streamlit-App, der Publisher, die Excel-Mappen, das Schema und die RLS-Regeln sind unverändert.

- **Seite:** `docs/` (index.html, live.css, live.js, config.js, logo.png), keine Abhängigkeiten, kein Build.
- **Hosting:** GitHub Pages aus diesem Branch, Ordner `/docs` → <https://stv-sev.github.io/testtag-public/?t=<slug>>.
  Kostenlos für öffentliche Repositories; einmalig eingeschaltet unter Settings → Pages. Dateien werden 10 min
  zwischengespeichert (`Cache-Control: max-age=600`).
- **Umfang:** live.js 458 Zeilen (396 ohne Kommentare/Leerzeilen), live.css 67, index.html 40, config.js 8.
  Übertragen beim ersten Aufruf rund 53 KB unkomprimiert (davon Logo 21 KB), danach aus dem Browsercache.

## Zugriff und Sicherheit

- Im Browser nur der Publishable Key (`config.js`); live.js weist alles ab, was nicht mit `sb_publishable_`
  beginnt. Er geht als Abfrageparameter `apikey` mit – so bleibt jede Anfrage ein einfacher CORS-GET ohne
  Vorabanfrage (OPTIONS). Kein `Authorization`-Kopf, keine Schreibanfrage, kein Browserspeicher.
- RLS unverändert: `anon` liest nur Testtage mit `ist_oeffentlich` und deren Zeilen.
- Liefert die Änderungsabfrage keinen Testtag (unbekannt oder verborgen), verwirft die Seite sofort alle
  geladenen Zeilen und leert die Anzeige (Funktionstest: kein Name mehr im DOM).
- Jede Zeile wird wie in `quelle.py` geprüft: eindeutige Schlüssel, `schema_version`, Prüfsumme über den
  kanonischen Inhalt ohne `geaendert_am` (Portierung von `publication_model.content_checksum`; identisch für
  21/21 Zeilen, `pruefsumme_test.mjs`), Tombstones ausgeblendet. Ein Fehler ersetzt nie einen gültigen Stand.

## Datenfluss

| Schritt | Wann | Anfrage | Übertragen je Zuschauer |
|---|---|---|---|
| Änderungsabfrage | alle 5 s (±10 % gestreut), nur solange sichtbar | `testtage` id, Status, stand_am | ≈ 610 B (160 B Inhalt + Kopf) |
| Kopfzeilen | nur bei neuem stand_am/Status, nach Fehler, alle 5 min | `veroeffentlichung` Live: programm, ak, pruefsumme, stand_am, `inhalt->aktiv`, `inhalt->>hinweis` | ≈ 1,2 KB |
| Zeilen | nur geänderte Zeilen der gewählten Ansicht | `…&pruefsumme=in.(…)` mit Inhalt | ≈ 15 KB je geänderte AK |
| Erstaufruf | einmal | alle drei | 108 KB (Alle) bzw. 12 KB (eine AK) |

gzip handelt der Browser selbst aus (Supabase antwortet `Content-Encoding: gzip`). Filterwechsel lädt nur noch
fehlende Zeilen. Bei Fehlern: neue Versuche nach 5, 10, 20, höchstens 30 s, sofort bei `online` oder wenn die
Seite wieder sichtbar wird. Nach 12 s ohne erfolgreiche Prüfung erscheint „Aktualisierung vorübergehend nicht
möglich – letzter gültiger Stand wird angezeigt (Stand HH:MM:SS)“, ohne Netz zusätzlich „Keine Internetverbindung“.

Darstellung wie `app.py`: Karten neueste Änderung zuerst (geaendert_am absteigend, dann Ergebnis-ID), Technik
mit S und T, Athletik mit Punkten, höchstens 300 Karten, Filter Programm/AK (auch in der Adresse: `&p=…&ak=…`),
Rotationsfreigabe je Programm aus dem Publisher-Hinweis, Datenstand und Prüfzeit, Fusszeile.

## Messung (13.10.2026, Seite von GitHub Pages)

Vollständig gefüllter synthetischer Testtag (`systemtest-last-…`, 4 301 Live-Resultate, 2,0 MB), 50 Zuschauer in
Headless-Chromium (je eigener Browserkontext), Publikation alle 30 s (`publikationstakt.py`), je 6 min, 11 ausgewertete
Publikationen = 550 Paare. Verzögerung = Supabase-Bestätigung (PATCH `stand_am`) bis gezeichnete Liste.
Danach Testtag wieder verborgen.

| | Lauf 1 | Lauf 2 | Lauf 3 |
|---|---|---|---|
| Median / 95. P. / Max | 2,8 / 8,3 / 18,4 s | 2,5 / 5,0 / 5,7 s | 2,8 / 5,2 / 5,8 s |
| Median je Sitzung (min–max) | 1,2–4,3 s | 0,8–4,4 s | 0,8–4,2 s |
| 95. P. je Sitzung (min–max) | 3,7–18,4 s | 3,5–5,7 s | 3,8–5,8 s |
| ausgefallene Aktualisierungen | 1 von 550 | 0 von 550 | 0 von 550 |
| falscher Wert angezeigt | 0 | 0 | 0 |
| Verbindungsabbrüche | 64 Anfragen (58 Zeitüberschreitung, 6 Netz), alle 50 Sitzungen in einem Fenster 80–120 s, danach selbst erholt | 0 | 0 |
| Supabase-Requests / Daten (6 min, 50 Zuschauer) | 4 607 / 17,3 MB | 4 879 / 17,4 MB | 4 877 / 17,4 MB |
| Erstanzeige Median / Max | 0,7 / 2,5 s | 0,6 / 1,3 s | 0,6 / 1,3 s |
| Supabase-Antwortzeit Median / 95. P. | – | 41–51 / 88–134 ms | – |

Lauf 1: Gleichzeitig verspätete sich auch der separate Publisher-Prozess (eine Publikation nach 50 statt 30 s), und
der Lastgenerator hatte mit 83 MB freiem RAM seinen Tiefstwert. Die Störung lag daher sehr wahrscheinlich beim
Messlaptop bzw. dessen Netz, nicht bei Supabase; sicher belegt ist das nicht. Die Seite zeigte während des
Unterbruchs den letzten Stand mit Hinweis und holte die Publikationen danach nach (1 Ausfall).

**Vergleich Streamlit (5-s-Takt, gleiche Messmethode, 12.10.2026):** 10 Zuschauer 6,2 / 8,2 s, 25 Zuschauer
9,0 / 10,6 s, 50 Zuschauer 19,5–21,4 s Median, 95. P. 23,9–30,0 s, 0–294 von 500 Aktualisierungen ausgefallen.

## Datenverkehr

Je Zuschauer (stationär, alle drei Läufe übereinstimmend): ≈ 950 Requests/h und 2,4 MB/h
(Änderungsabfrage 706 × 610 B = 0,43 MB/h; je Publikation 16,5 KB → 120 Publikationen/h = 2,0 MB/h).

| Szenario (50 Zuschauer, je 5 Erstaufrufe) | Requests | Supabase-Egress |
|---|---|---|
| 8 h, Publikation alle 30 s | 378 000 | 0,99 GB |
| 10 h, Publikation alle 30 s | 473 000 | 1,23 GB |
| 8 h, Publikation alle 10 s | 570 000 | 2,57 GB |

Supabase Free: 5 GB Egress (ungecacht) pro Monat, keine Request-Grenze für die Data API. Gezählt sind hier die
übertragenen Bytes inklusive Antwortkopf, gzip-komprimiert. Ob Supabase komprimiert oder unkomprimiert zählt, ist
nicht dokumentiert – unkomprimiert wären die Zeilen rund 15-mal grösser und der Rahmen schon an einem Tag
überschritten. Zum Vergleich die Streamlit-App (geschätzt aus den dokumentierten 138 KB je Publikation und einer kleinen Abfrage
je 3 s): ≈ 0,15 GB je 8-h-Tag, da der Server die Daten für alle Zuschauer einmal lädt.

## Grenzen

- Nur Live-Ansicht; die übrigen Bereiche führen in die Streamlit-App.
- Traffic wächst linear mit Zuschauern und Publikationshäufigkeit (der Dauerpublisher kann deutlich öfter als alle
  30 s publizieren). Ohne Datenbankänderung (z. B. Realtime) ist das Polling die Untergrenze.
- Der Publishable Key und die Projekt-URL stehen öffentlich im Repository (für Browser vorgesehen; Schutz über RLS).
- Lastgenerator ein Laptop: 50 Chromium-Kontexte brauchten 55–60 % CPU und rund 4 GB RAM.

## Dateien

- `funktionstest.mjs` – Anzeige, Filter, Offline/Erholung, verborgener Testtag (Playwright, nur lesend).
- `lasttest.mjs` – N Zuschauer, Protokoll je Anzeige/Request. `auswerten.py` – Kennzahlen.
- `publikationstakt.py` – synthetische Publikation alle 30 s (nur `systemtest-last-…`, Schreibschutz).
- `pruefsumme_test.mjs` – Prüfsummen-Portierung gegen echte Zeilen.
