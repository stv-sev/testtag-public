# Umstellung auf die statische Seite – Vorbereitung (Branch `umstellung-live-statisch`)

## Nachtrag 13.10.2026 abends: alle vier Ansichten und Übersicht statisch

Die statische Seite zeigt jetzt **Live, Ranglisten, Startliste und Zeitplan** eines laufenden Testtags sowie ohne `?t=`
die **Übersicht** (Live, Demnächst, Archiv). Nur das Archiv bleibt in Streamlit. Nichts ist produktiv umgeschaltet.

- **Startliste/Zeitplan:** Portierung von `app._startliste`/`startliste_tabelle` und `app._zeitplan` (gleiche
  Gruppierung und CSS). Startliste mit Programmfilter („Alle“ = Streamlit-Ansicht), Zeitplan ohne Filter. Laden beim
  Öffnen, Änderungsabfrage alle 30 s (statt 5 s), neue Zeilen nur bei geänderter Prüfsumme; gleiche Prüfung,
  Unterbruch-Hinweis (jetzt relativ zum Takt), Verbergen und Rückfall wie bei Live.
- **Übersicht:** nur, was die öffentlichen Leserechte liefern (verborgene fehlen); Live/Demnächst →
  `?t=<slug>` dieser Seite, Archiv → `https://testtag.streamlit.app/?t=<slug>`; alle 60 s aktualisiert.
  „← Alle Testtage“ führt auf diese Übersicht – auch aus Streamlit, sobald `live_url` gesetzt ist.
- **Schalter neu:** `docs/config.js` `ansichten: ["rangliste", "startliste", "zeitplan"]` (fehlende → Streamlit) statt
  `rangliste`; Streamlit-Secret `statische_bereiche = ["rangliste", "startliste", "zeitplan"]` statt
  `statische_rangliste`. Mit allen dreien zeigt Streamlit für laufende Testtage nur noch Links (keine Daten).
- **Tests:** pytest 326 bestanden; Browsertest gegen nachgebildete Data API alle Prüfungen bestanden, **107 von 107
  Ansichten identisch mit Streamlit** (87 Ranglisten, 20 Startliste/Zeitplan); Navigation (alle vier Ansichten,
  direkte Links, alte Streamlit-Links je Ansicht, 390/1280 px) und Rückfall (alle vier Ansichten) mit echtem
  Supabase bestanden – **vor** dem Einbau der Übersicht; die Übersicht ist nur gegen die nachgebildete API und in
  pytest geprüft (Navigationstest mit echten Daten noch offen).

**Messung 50 Zuschauer, statisch** (synthetischer Testtag, Publikation alle 30 s, Seite lokal ausgeliefert):

| | D: Klick Live → Startliste → Zeitplan | D2: Wiederholung | E/F: Link direkt öffnen | Streamlit (gemessen) |
|---|---|---|---|---|
| Startliste sichtbar, Median / Max | 0,43 / 0,60 s | 0,35 / 0,52 s | 0,34 / 0,59 s | 114,6 / 115,8 s |
| Zeitplan sichtbar, Median / Max | 0,12 / 0,19 s | 0,15 / 0,32 s | 0,31 / 0,92 s | 83,5 / 84,7 s |
| erreicht, Fehler | 50/50, 0 | 50/50, 0 | 50/50, 0 | 50/50 |
| Daten beim Wechsel (50 Zuschauer) | Startliste 377 KB, Zeitplan 72 KB | 384 / 86 KB | 503 / 180 KB (inkl. Erstaufruf) | – |
| Anfragen gesamt / Daten (5 min) | 1 402 / 7,8 MB | 1 455 / 8,3 MB | 553 / 550 Anfragen (2,5 min) | – |

Stationär auf Startliste/Zeitplan je Zuschauer: ≈ 210 Anfragen/h und 0,24 MB/h (Live/Rangliste ≈ 2,8 MB/h).
Supabase-Antwortzeit Median 44–52 ms.

**Umschaltung (zusätzlich zu den Schritten unten):** Secret `statische_bereiche = ["rangliste", "startliste", "zeitplan"]`
statt `statische_rangliste`. Verteilter Link kann dann auch die Übersicht sein: `https://stv-sev.github.io/testtag-public/`.
**Rückfall je Ansicht:** Bereich aus `statische_bereiche` entfernen, dann aus `ansichten` in `config.js`; alles:
`live_url` und `statische_bereiche` löschen, dann `rueckfall: true` (leitet auch `&ansicht=startliste|zeitplan` und die
Übersicht nach Streamlit um).

---


Stand 13.10.2026. Nichts ist produktiv umgeschaltet; `main`, die beworbene Adresse `https://testtag.streamlit.app/`,
die Pages-Quelle (`prototyp-live-statisch`), die Streamlit-Secrets, Publisher, Excel, Supabase-Schema, RLS und
produktive Daten sind unverändert.

## Aufteilung

| Ansicht | Wo | Adresse |
|---|---|---|
| Live | statische Seite | `https://stv-sev.github.io/testtag-public/?t=<slug>` (`&p=…&ak=…`) |
| Ranglisten | statische Seite | `…/?t=<slug>&ansicht=rangliste` (`&p=…&ak=…&art=technik\|athletik`) |
| Startliste, Zeitplan | Streamlit | `https://testtag.streamlit.app/?t=<slug>&ansicht=startliste\|zeitplan` |
| Übersicht, Archiv | Streamlit | `https://testtag.streamlit.app/` bzw. `?t=<slug>` eines archivierten Testtags |

## Navigation

| Von | Nach | Weg |
|---|---|---|
| Statisch Live ⇄ Ranglisten | gleicher Testtag | Pillen; Wechsel **ohne Neuladen** der Seite, Programm/AK bleiben, Zurück-Taste führt zur vorherigen Ansicht |
| Statische Seite | Startliste / Zeitplan | `https://testtag.streamlit.app/?t=<slug>&ansicht=startliste` (`zeitplan`) |
| Statische Seite | Übersicht | „← Alle Testtage“ → `https://testtag.streamlit.app/` |
| Streamlit-Testtag | Live / Ranglisten | Pillen „⚡ Live“, „🏅 Ranglisten“ → `<live_url>?t=<slug>` bzw. `…&ansicht=rangliste` (gleicher Tab) |
| Streamlit-Übersicht | Live | Pille „⚡ Live-Resultate“ je Testtag (nicht im Archiv) |
| Bestehender Link `?t=<slug>` (Streamlit) | – | Hinweis „Live-Resultate öffnen“, mit `&ansicht=rangliste` „Ranglisten öffnen“ – ohne Datenabruf |

- **Auswahl beim Wechsel:** Ranglisten haben kein „Alle“ (wie Streamlit). Live „Alle“ → Rangliste zeigt das erste
  Programm und dessen erste AK; die Live-Auswahl „Alle“ bleibt beim Zurückwechseln erhalten. Eine in der Rangliste
  gewählte AK gilt danach auch für Live. Streamlit kennt weiterhin keine Filter in der Adresse; nach Startliste und
  Zeitplan wird deshalb nur der Testtag übergeben.
- **Archiv:** archivierte Testtage bleiben vollständig in Streamlit; die statische Seite zeigt nur den Link ins
  Archiv (aus den Ranglisten mit `&ansicht=rangliste`).
- **Unbekannt/verborgen:** beide Seiten zeigen „Dieser Testtag ist nicht verfügbar.“ ohne Navigation und ohne Daten;
  die statische Seite verwirft bereits geladene Daten sofort.

## Statische Rangliste

- **Darstellung:** Portierung von `app.rang_karte` und `quelle.turner_details` (`docs/rangliste.js`): Programm, AK,
  Ebenen Gesamt/Technik/Athletik, kompakte Zeile (Rang, Turner, Verein, Total), Details erst nach Antippen;
  Gesamt mit Gerätetotalen, Total Technik/Athletik und Gesamt; Technik je Gerät aufklappbar mit Linien S/T und
  „(Alternative)“ beim Sprung; Athletik je Test, Total Beweglichkeit/Kraft/Athletik. Gleiche CSS-Regeln wie `app.py`.
- **Keine eigene Rangberechnung:** Reihenfolge, Ränge, Punkte und Totale stammen aus der Publikation. Wie in
  Streamlit werden nur Gerätetotale als Summe der veröffentlichten Linienpunkte gebildet; passt die Aufschlüsselung
  nicht exakt zu den Totalen, steht „Die Aufschlüsselung ist zurzeit nicht verfügbar.“ Fehlende Werte „–“,
  echte Nullwertung 0, Medaillenfarben nur mit Resultat, ältere Publikationen ohne `struktur` mit der aus den
  Resultaten abgeleiteten Gliederung.
- **Daten und Prüfung:** Kopfzeilen jetzt für Live- und Ranglistenzeilen (eine Anfrage). Für eine Rangliste werden
  die Ranglistenzeile der AK und deren Live-Zeile (Einzelwertungen) geladen, nur bei geänderter Prüfsumme; jede
  Zeile wird wie bisher geprüft (Schema, Prüfsumme über den kanonischen Inhalt). Ein Fehler ersetzt nie den letzten
  gültigen Stand; nach 12 s erscheint der Hinweis „Aktualisierung vorübergehend nicht möglich – letzter gültiger
  Stand wird angezeigt (Stand …)“.
- **Bei Aktualisierungen** bleiben geöffnete Details (Turner, Gerät) offen.
- **Behoben:** Fiel der 5-s-Takt genau in ein gezieltes Nachladen (Ansichts- oder Filterwechsel), plante danach
  niemand den nächsten Takt – die Seite blieb auf dem Stand stehen (im Lasttest 1 von 50 Sitzungen; betraf auch
  den bisherigen Live-Prototyp beim Filterwechsel). Jetzt wird der Takt sofort nachgeholt (Regressionstest).

## Schalter

| Ort | Schalter | Wirkung |
|---|---|---|
| `docs/config.js` | `rangliste: true` | Ranglisten auf der statischen Seite; `false`: Pille und `&ansicht=rangliste` führen in die Streamlit-Rangliste (ohne Datenabruf) |
| `docs/config.js` | `rueckfall: true` | jeder Aufruf geht vor dem ersten Datenabruf nach Streamlit (`&ansicht=live` bzw. `rangliste`) |
| Streamlit-Secrets | `live_url` | Live auf der statischen Seite (ohne: alles wie heute in Streamlit) |
| Streamlit-Secrets | `statische_bereiche = ["rangliste", "startliste", "zeitplan"]` | zusätzlich Ranglisten auf der statischen Seite (nur zusammen mit `live_url`) |

## Tests

- `python -m pytest tests` – 324 Tests (neu: Schalter `statische_rangliste`).
- `rangliste_daten.py` + `rangliste_test.mjs` (Chromium gegen nachgebildete Data API, nur synthetische Stände):
  **87 von 87 Ranglisten identisch mit der Streamlit-Darstellung** (Text aus `app.rang_karte`), 5 Stände: Rotationsgrenze
  (nur Rotation 1–2), alle Rotationen, Korrektur (Rang- und Totaländerung, erstes Resultat einer leeren Person),
  ältere Publikation ohne `struktur` mit widersprüchlichem Total, vollständiger Testtag (108 Personen); beide
  Programme, alle AK, alle drei Ebenen, Gleichstände, Nullwertung, fehlende Resultate, Sprung-Alternativen.
  Dazu: Wechsel ohne Neuladen und mit nur einer Anfrage, Adresse, Zurück/Vorwärts, Korrektur während der Anzeige mit
  offenen Details, offline, Prüfsummenfehler (verfälschter Inhalt nie angezeigt), verborgen (keine Namen im DOM),
  Archivlink, 390 px und 1280 px ohne waagrechte Scrollbar, `rangliste:false`, `rueckfall:true`, Takt während eines
  langsamen Wechsels – alle bestanden.
- `pruefsumme_test.mjs` – 73/73 synthetische Zeilen (davon 29 Ranglisten) identisch.
- `navigation_test.mjs` – lokal, Streamlit dieses Branchs mit `live_url` und `statische_rangliste`, synthetischer
  Testtag in Supabase, 390 × 844 und 1280 × 900: alle Wege der Tabelle oben – bestanden.
- `rueckfall_test.mjs` – `rueckfall: true`, Streamlit ohne beide Secrets: Umleitung ohne Supabase-Abruf auf Live
  bzw. Ranglisten in Streamlit – bestanden.
- `funktionstest.mjs` – Live unverändert (Filter, offline, verborgen) – bestanden.

## Messung: 50 Zuschauer wechseln zur Rangliste

Synthetischer Testtag (108 Personen, 4 301 Live-Resultate), Publikation alle 30 s (`publikationstakt.py`),
50 Headless-Chromium-Sitzungen in Smartphone-Breite auf einem Laptop, Seite dieses Branchs lokal ausgeliefert
(statische Dateien; Supabase echt). `wechsel_statisch.mjs`, `wechsel_statisch_auswerten.py`.

| | A: Klick Live → Ranglisten | C: wie A (mit Korrektur) | B: Link direkt öffnen |
|---|---|---|---|
| Rangliste sichtbar: Median / 95. P. / Max | 1,8 / 2,2 / 2,2 s | 1,2 / 1,7 / 1,8 s | 0,4 / 0,5 / 0,5 s |
| erreicht | 50/50 | 50/50 | 50/50 |
| Aktualisierung nach Publikation: Median / 95. P. / Max | 2,9 / 5,0 / 5,6 s | 2,9 / 10,2 / 11,2 s | 2,5 / 5,3 / 9,3 s |
| ausgefallene Aktualisierungen | 12 von 600 (alle Sitzung 23, Takt-Fehler, behoben) | 3 von 600 (Störung, s. u.) | 0 von 550 |
| Supabase-Anfragen / Daten (6–7 min, 50 Zuschauer) | 5 373 / 21,3 MB | 5 269 / 21,5 MB | 4 567 / 15,7 MB |
| Supabase-Antwortzeit Median / 95. P. | 46 / 94 ms | 47 / 135 ms | 49 / 124 ms |

- **Vergleich Streamlit:** derselbe Wechsel dauerte dort 42,1 bzw. 42,4 s (Median), danach Aktualisierung
  ≈ 18 s. Statisch: rund 1–2 s nach dem Klick, Aktualisierung ≈ 3 s – gleich wie Live.
- Der Wechsel lädt je Zuschauer genau eine Zeile (Rangliste der AK, ≈ 7 KB komprimiert); die Live-Zeile der AK ist
  meist schon geladen. Die 1–2 s bestehen überwiegend aus dem gleichzeitigen Zeichnen in 50 Browsern auf einem Laptop.
- **Störungen:** In B (bei 149–163 s) und C (bei ≈ 340 s) liefen in allen 50 Sitzungen gleichzeitig Anfragen in die
  Zeitüberschreitung (53–59), und gleichzeitig dauerte eine Publikation 17–21 s statt 6 s – Ursache im Netz des
  Messlaptops oder bei Supabase, nicht in der Seite. Alle Sitzungen zeigten den letzten Stand weiter und holten
  selbst auf; in C übersprangen 3 Sitzungen dabei eine Publikation und zeigten direkt die nächste. Nie wurde ein
  falscher oder älterer Wert angezeigt.
- **Lauf A** deckte den Takt-Fehler auf (eine Sitzung blieb nach dem Wechsel stehen); C ist die Wiederholung mit
  Korrektur.

## Startliste und Zeitplan in Streamlit (gemessen)

Produktive Streamlit-App (Code von `main`), synthetischer Testtag, 50 Sitzungen öffnen innerhalb von 2,5 s den Testtag
und wechseln zur Startliste bzw. zum Zeitplan, Publikation alle 30 s, je 4 min (`streamlit_wechsel.py … startliste`
bzw. `zeitplan`), 13.10.2026 ab 16:22:

| | Startliste | Zeitplan | zum Vergleich Rangliste (früher am Tag) |
|---|---|---|---|
| sichtbar nach Seitenaufruf, Median / Max | 114,6 / 115,8 s | 83,5 / 84,7 s | 42,1–42,4 / 43,9–46,3 s |
| Seitenaufruf HTTP (inkl. Weiterleitungen), Median | 4,1 s | 4,5 s | 3,5–4,0 s |
| Laufdauer je 5-s-Takt, Median / Max | 45,3 / 54,2 s | 28,8 / 55,4 s | 14,1–14,9 / 32,6–34,1 s |
| erreicht / Trennungen | 50/50 / 0 | 50/50 / 0 | – |

- **Ja, das bleibt ein relevantes Risiko.** Streamlit führt für einen Live-Testtag auch Startliste und Zeitplan
  alle 5 s für jede Sitzung neu aus; bei 50 Sitzungen staut sich das in einem Prozess. Die Startliste (alle
  108 Personen in Tabellen) ist dabei teurer als eine Rangliste einer AK.
- Einschränkungen: Die App war an diesem Nachmittag langsamer als am Morgen (schon 2 Sitzungen brauchten 7–12 s je
  Lauf); die Werte sind daher eher pessimistisch, die Grössenordnung aber eindeutig. Der erste Lauf jeder Sitzung
  zeichnete auf `main` noch Live (4 301 Resultate); mit `live_url` entfällt das, der 5-s-Takt bleibt.
- Wahrscheinlichkeit: Startliste und Zeitplan ändern sich selten und werden kaum von allen gleichzeitig geöffnet –
  am ehesten vor Beginn (Zeitplan, Startgruppen). Mit Live und Ranglisten statisch bleiben in Streamlit nur diese
  Besuche; einzelne Zuschauer sind unproblematisch.
- Mögliche Abhilfen (nicht umgesetzt, Entscheid offen): Startliste und Zeitplan ohne 5-s-Takt anzeigen (Inhalt ändert
  sich selten; kleine Änderung in `app.py`) oder ebenfalls statisch machen (gleiche Technik wie Ranglisten).
- Eine erste Startliste/Zeitplan-Messung ist verworfen: Die Erkennung zählte den Stilblock mit, und ein
  abgebrochener Lauf überlappte die Wiederholung.

## Datenverkehr

Je Zuschauer auf der Rangliste (stationär, A–C übereinstimmend): ≈ 900 Anfragen/h und 2,8–3,0 MB/h bei einer
Publikation alle 30 s (Änderungsabfrage 0,6 KB alle 5 s; je Publikation Kopfzeilen 1,7 KB und Zeilen ≈ 19 KB).
Live liegt in derselben Grössenordnung (bisher 2,4 MB/h; die Kopfzeilen enthalten jetzt auch die Ranglisten,
+0,5 KB je Publikation).

| Szenario (50 Zuschauer, Publikation alle 30 s) | Supabase-Egress |
|---|---|
| 8 h | ≈ 1,2 GB |
| 10 h | ≈ 1,5 GB |

Supabase Free: 5 GB Egress pro Monat. Gezählt sind hier die übertragenen, gzip-komprimierten Bytes inklusive
Antwortkopf. Ob Supabase komprimiert oder unkomprimiert zählt, ist weiterhin nicht geklärt (unkomprimiert rund
15-mal mehr) – vor dem 31.10. im Dashboard prüfen.

## Ablauf bis zum 31. Oktober

**Vor dem Testtag testen** (mit synthetischem Testtag, danach wieder verbergen):
1. Branch nach `main` übernehmen (dein Entscheid) – ohne Secrets ändert sich für Zuschauer nur, dass `&ansicht=` wirkt.
2. GitHub → Settings → Pages: Quelle auf `main` / `/docs` umstellen (Adresse bleibt
   `https://stv-sev.github.io/testtag-public/`). `prototyp-live-statisch` danach nicht mehr als Quelle.
3. Streamlit Cloud → App `testtag` → Settings → Secrets ergänzen und speichern:
   ```toml
   live_url = "https://stv-sev.github.io/testtag-public/"
   statische_bereiche = ["rangliste", "startliste", "zeitplan"]
   ```
4. Mit Telefon und Laptop durchklicken: Übersicht → Live, Live ⇄ Ranglisten (Programm/AK, drei Ebenen, Details),
   Ranglisten → Startliste/Zeitplan → Ranglisten/Live, alter Link `https://testtag.streamlit.app/?t=<slug>`;
   einmal den Rückfall (unten) durchspielen und wieder aufheben.
5. Supabase → Usage: Egress der Messtage prüfen (Zählweise).

**Verteilter Link (QR-Code, Mitteilungen):** `https://stv-sev.github.io/testtag-public/?t=aargau-lenzburg-2026-10-09`
– öffnet Live, Ranglisten eine Pille weiter; beides ohne Streamlit-Prozess und ohne Schlafmodus. Der bisherige Link
`https://testtag.streamlit.app/?t=aargau-lenzburg-2026-10-09` bleibt gültig und führt mit einem Klick dorthin.

**Am Morgen des Testtags:** `https://testtag.streamlit.app/` öffnen, damit die App wach ist (Startliste, Zeitplan,
Übersicht); Live-Seite und eine Rangliste kurz prüfen.

## Rückfall bei einem Problem

Nur die Rangliste (Live bleibt statisch):
1. Streamlit-Secrets: Zeile `statische_rangliste` löschen → Ranglisten wieder in Streamlit.
2. `docs/config.js` auf `main`: `rangliste: false`, committen, pushen → die Pille und Links mit
   `&ansicht=rangliste` führen nach Streamlit.

Alles (Live und Ranglisten):
1. Streamlit-Secrets: Zeilen `live_url` und `statische_rangliste` löschen → alles wieder in Streamlit.
2. `docs/config.js` auf `main`: `rueckfall: true`, committen, pushen → der verteilte Link leitet auf
   `https://testtag.streamlit.app/?t=<slug>&ansicht=live` (bzw. `rangliste`) um.

Jeweils erst Schritt 1, dann 2 (sonst zeigt Streamlit kurz den Hinweis auf die statische Seite). Nach dem Push
wirkt die Änderung nach dem Pages-Build (≈ 1 min); Browser können `config.js` bis 10 min zwischenspeichern, bereits
offene Seiten laufen weiter, bis sie neu geladen werden. Aufheben: umgekehrt.

## Ansichtswechsel Streamlit (Messung vom 13.10.2026, Grundlage dieser Ergänzung)

Produktive Streamlit-App (Code von `main`), synthetischer Testtag, 50 Sitzungen starten innerhalb von 2,5 s und
wechseln zur Rangliste, Publikation alle 30 s, je 6 min (`streamlit_wechsel.py`, `wechsel_auswerten.py`):

| | Lauf A | Lauf C |
|---|---|---|
| Rangliste sichtbar nach Wechsel, Median / 95. P. / Max | 42,1 / 44,2 / 46,3 s | 42,4 / 43,6 / 43,9 s |
| Aktualisierung nach Publikation, Median / 95. P. | 19,4 / 25,2 s | 17,7 / 24,2 s |
| ausgefallene Aktualisierungen | 20 von 400 | 0 von 450 |
| Laufdauer je Takt (Soll < 5 s), Median / Max | 14,1 / 34,1 s | 14,9 / 32,6 s |

(Lauf B ungültig: Messlaptop stand 38 min still.) Die App schlief an diesem Tag wegen Inaktivität (Wecken 25 s).
