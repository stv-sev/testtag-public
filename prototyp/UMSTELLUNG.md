# Verlauf: Umstellung auf die statische Seite

Abgeschlossen am 14.10.2026: GitHub Pages liefert `main` / `/docs` aus, die Streamlit-Secrets `live_url` und
`statische_bereiche` sind gesetzt. Die gültige Anleitung zu Aufteilung, Schaltern und Rückfall steht in
[BETRIEB.md](../BETRIEB.md#öffentliche-seite). Was folgt, sind Prüfungen, Nachträge und Messberichte aus der
Umstellung.

## Geprüft gegen den Code (14.10.2026)

- `docs/config.js` und `docs/live.js`: Schalter `ansichten`, `rueckfall`, `streamlitUrl`; Live wird immer angeboten;
  fehlende Ansichten und der Rückfall leiten vor dem ersten Datenabruf um (Browsertest `rangliste_test.mjs`, auch mit
  `ansichten` ohne Ranglisten bzw. ohne Startliste/Zeitplan und mit `rueckfall: true`).
- `app.py`: Secrets `live_url` (`_live_basis`, nur `https://` ohne Abfrage; `http://localhost` nur für Tests) und
  `statische_bereiche` (`statische_links`, nur als Liste); weitere Schalter liest Streamlit nicht. pytest u. a.
  `test_static_ranking_link_replaces_ranking_area_without_loading_data` (teilweise Liste, Text statt Liste, ohne
  `live_url`), `test_all_areas_static_streamlit_shows_only_links_and_loads_no_data`,
  `test_rotation_view_falls_back_to_technik_ranking_with_hint`.

---

## Nachtrag 14.10.2026 abends: „Gerät“ zeigt alle vorgesehenen Linien

Beim Antippen eines Turners stehen alle Linien seiner AK am gewählten Gerät (bzw. alle Beweglichkeitstests), noch
nicht gewertete mit „–“. Quelle ist die bereits publizierte Gliederung (`struktur`) der Ranglistenzeilen; dieselbe
Logik wie in den Ranglisten (`rangliste.js`, `geraetZeilen`). Kein Publisher-Lauf nötig. Browsertest 120 Prüfungen
bestanden, 126/126 wie Streamlit.

## Nachtrag 14.10.2026 nachmittags: Fehler blockiert nur sein Programm

- **Anlass:** Externe Kraftwerte AK9–AK12 erschienen nicht. Ursache: Der Dauer-Publisher wies ab 14:51 jede Änderung
  mit `ATHLETIK_INAKTIVE_QUELLE` ab (ein Athletikwert in der Direktspalte der AK5–AK9+-Mappe, die vor Ort erfasst)
  und blockierte damit auch AK9–AK12; das Protokoll nannte nur den Code.
- **Neu:** Ein Validierungsfehler blockiert nur sein Programm (alter öffentlicher Stand inkl. Zeitplanblock bleibt),
  die übrigen werden publiziert; im Dauerbetrieb gilt bei einem Lesefehler der zuletzt gültige Stand der Mappe.
  Probelauf und Protokoll nennen Programm, Code, Zelle und Test (ohne Namen). Ereignisfehler oder Fehler in allen
  Programmen blockieren weiter alles; B6 = FALSCH verbirgt auch eine fehlerhafte Mappe.
- **Probelauf** (synthetische Kopien, Slug `systemtest-probelauf-202610141531`, danach verborgen): AK5 mit Kraftwert
  in der Direktspalte → blockiert, 11 AK5-Zeilen öffentlich unverändert, Meldungen mit Zellen (`Noteneingabe!NB12` …);
  AK9 mit externen Werten → publiziert (50 Beweglichkeit, 70 Kraft), sichtbar in Live und Rangliste Athletik;
  nach Korrektur unverändert. Originalmappen, produktiver Testtag, `.env`, `main`, Pages unverändert.
- **Tests:** pytest 340 (dreimal grün).

## Nachtrag 14.10.2026 spät: Beweglichkeit in „Gerät“, Subdivisionen

- **Beweglichkeit** ist ein Gerät in „Gerät“ (alle Stationen der Startliste): Riege der Rotation mit Total
  Beweglichkeit und Einzeltests, nur bis zur Freigabe.
- **Subdivisionen:** Ein Programm kann auf mehrere Mappen verteilt werden (`Public!A9` `subdivision`, `B9`
  Anzeigename, frei wählbar). Jede Subdivision ist öffentlich ein eigenes Programm mit eigener Freigabe (B5);
  jede AK muss ganz in einer Subdivision stehen. Mappen über `--workbook` (mehrfach) bzw. `TESTTAG_WORKBOOKS`.
  Programmnamen werden nicht mehr fest übersetzt; die Reihenfolge folgt der ersten AK-Zahl.
- **Probelauf** (synthetische Kopien: AK5–AK9+ aufgeteilt in AK5–AK7 und AK8–AK9+, dazu AK9–AK12; Slug
  `systemtest-probelauf-202610141416`, danach verborgen): drei Programme publiziert, Einteilung aller 177 Riegen =
  `id_rotation` (inkl. Beweglichkeit), Freigabe je Subdivision unabhängig (Morgen 2 → 8 → 1, Vormittag 2) mit
  offener Seite, Streamlit-Rückfall; Originalmappen, produktiver Testtag, `.env`, `main`, Pages unverändert.
- **Tests:** pytest 337 (dreimal grün), Browsertest bestanden, 126/126 wie Streamlit.

## Nachtrag 14.10.2026 abends: eigene Ansicht „Gerät“, Einteilung aus der Startliste

- **Warum vorher „nicht verfügbar“:** Die statische Seite liest nie Excel. Die produktive Publikation stammte noch
  vom alten Publisher und enthielt keine Einteilung. Sichtbar wird sie erst nach einem Publisher-Lauf mit neuem Code.
- **Publisher:** Die Einteilung kommt jetzt direkt aus den Codes `@ID°Rotation çGerät ^Startposition` im Blatt
  `Startliste` (statt aus Startgerät + Stationsfolge berechnet). An allen vier Mappen in `00-tool` geprüft (lesend):
  alle Codes gültig, `ç` = Position in `tab_startgeraet` (nicht GerID), `^` = Pos, Einteilung = `id_rotation`
  (574/574 bzw. 182/182). Neue Zeile `inhalt.einteilung` ersetzt `inhalt.rotation`; neuer Fehlercode
  `EINTEILUNG_UNGUELTIG`.
- **Seite:** „Gerät“ ist eine eigene Pille (Live · Gerät · Ranglisten · Startliste · Zeitplan); der Umschalter unter
  Technik entfällt; frühere Adressen öffnen „Gerät“. Streamlit zeigt bei statischen Ranglisten die Link-Pille
  „🤸 Gerät“, im Rückfall Hinweis und Technik-Rangliste.

## Nachtrag 14.10.2026: gestufte Gesamtdetails, Technik „Gerät & Rotation“ (abgelöst, siehe oben)

Nichts produktiv umgeschaltet; `main`, Pages, Secrets, Excel und die öffentliche Publikation unverändert.

**Vorprüfung Zelladressen** (alle Mappen in `00-tool`, nur gelesen über temporäre Kopie, Werte = Formeln):

| Mappe | B2 `event_id` | B5 `published_rotation_max` | B6 aktiv | B7 Kraftmodus | B8 Beweglichkeit |
|---|---|---|---|---|---|
| 261009-Test-AK5-AK9plus | aargau-lenzburg-2026-10-09 | 7 | WAHR | vor_ort | vor_ort |
| 261009-Test-AK9-AK12 | aargau-lenzburg-2026-10-09 | 6 | WAHR | extern | extern |
| 261031-Test-AK5-AK9plus | aargau-lenzburg-2026-10-**31** | 7 | FALSCH | vor_ort | vor_ort |
| 261031-Test-AK9-AK12 | aargau-lenzburg-2026-10-**09** (!) | 6 | FALSCH | extern | extern |

Zuordnung unverändert: `Public!B5` = Rotationsgrenze, `Public!B7` = Kraftmodus (A-Spalte beschriftet); der Publisher
liest B5 nur als `published_rotation_max` (`excel_probe.PUBLIC_FIELDS`) und B7 nur als Kraftmodus. **Offen:** In
`261031-Test-AK9-AK12.xlsx` steht in B2 noch der Slug vom 9.10. – zusammen mit der AK5-Mappe bricht der Publisher mit
`EVENT_WIDERSPRUCH` ab. `.env` zeigt weiterhin auf die 261009-Mappen.

**Rotationseinteilung geprüft:** In allen vier Mappen entspricht `id_rotation` jeder Stationszeile der Noteneingabe
der Formel Station = Stationsfolge[(Index Startgerät + r − 1) mod n] (2 882 Zeilen, 0 Abweichungen). Die bisher
publizierte Startliste enthielt weder Stationsfolge noch Freigabegrenze (nur Hinweistext) → Publisher ergänzt.

- **Publisher:** Zeile `startliste` je Programm neu mit `inhalt.rotation = {stationsfolge, rotationen, freigegeben}`
  (Stationsfolge aus `tab_startgeraet`, `freigegeben` = geprüftes `Public!B5`). Ändert die Freigabe, ändert sich die
  Prüfsumme der Startlisten-Zeile. Erst ein Publisher-Lauf mit diesem Branch bringt das Feld in die Publikation;
  bis dahin zeigt „Gerät & Rotation“ „nicht verfügbar“.
- **Gesamt gestuft** (statisch und Streamlit/Archiv gleich): nach dem Öffnen Total Technik, Total Athletik, Gesamt;
  Total Technik → Gerätetotale → Linien S/T; Total Athletik → Total Beweglichkeit / Total Kraft → Einzeltests.
  Totale aus der Publikation, keine Rangberechnung; offene Stufen bleiben bei Aktualisierungen offen.
- **Technik „Gerät & Rotation“** (statisch, `&art=technik&sicht=rotation&g=…&r=…`): Programm, Gerät (nur
  Technikstationen in Stationsfolge), Rotation 1 … `freigegeben`, AK optional. Zeigt die eingeteilte Riege in
  Startlisten-Reihenfolge, auch ohne Note; bei veröffentlichten Resultaten Gerätetotal und Linien S/T; keine Ränge.
  Ohne Wahl keine Riege („keine aktuelle Rotation“); `r` über der Freigabe wird verworfen (auch aus der Adresse);
  Freigabe 0 → nichts wählbar; ältere Publikation ohne `rotation` → Hinweis statt Einteilung.

**Tests:** pytest 327 bestanden (neu: Rotationsfeld der Startliste; gestufte Gesamtdetails). Browsertest gegen
nachgebildete Data API: alle Prüfungen bestanden, neu u. a. Gerät & Rotation (Freigabe 0/2/8, Freigabe sinkt, r per
Adresse, AK-Filter, leere Riege, Korrektur mit offener Karte, ältere Publikation) – **126 von 126 Ansichten identisch
mit Streamlit** (102 Ranglisten). Mit lokal erzeugten Zeilen aus den echten Mappen (nichts publiziert): 91 von 91
Kombinationen Programm × Gerät × Rotation stimmen mit `id_rotation` überein (beide Mappensätze).
**Echte öffentliche Daten (nur lesend):** statische Übersicht = `quelle.lade_testtage` (1 Testtag, live; Gruppen,
Reihenfolge, Ziele, Stand/Adresse; 390 und 1280 px), Übersicht → Testtag → Ranglisten/Startliste/Zeitplan/Live →
„← Alle Testtage“ ohne Fehler; Gerät & Rotation zeigt „nicht verfügbar“ (Publikation ohne `rotation`). Die echte
Publikation enthält zurzeit 0 Live-Resultate; Noten-Darstellung daher nur synthetisch geprüft.

**Offen:** Startreihenfolge innerhalb der Riege = Startliste (Positionen rotieren je Gerät nicht, falls das in der
Praxis anders ist, fehlt die Angabe); Gerät & Rotation gibt es nur statisch (Streamlit-Rückfall: siehe unten);
Reihenfolge überall Beweglichkeit vor Kraft (Athletik = Beweglichkeit + Kraft; entschieden 14.10.2026).

**Abschluss 14.10.2026:** Streamlit-Rückfall für „Gerät & Rotation“: Die Umleitung der statischen Seite
(`rueckfall: true` oder Ranglisten nicht in `ansichten`) gibt `&art=` und bei Gerät & Rotation `&sicht=rotation` mit
(Gerät/Rotation entfallen). Streamlit übernimmt die Ebene einmal, wählt bei `sicht=rotation` die Technik-Rangliste und
zeigt den Hinweis „«Gerät & Rotation» ist in dieser Ansicht nicht verfügbar. Angezeigt wird die Technik-Rangliste …“;
danach entfernt es die Angaben aus der Adresse, die Ebene bleibt frei wählbar (Wechsel blendet den Hinweis aus).
Zeitabhängiger App-Test behoben: jeder Test beginnt am Anfang eines 300-s-Kontrollfensters (vorher löste eine zufällig
überschrittene Fenstergrenze eine zusätzliche Kontrollladung aus – `2 != 1`; 10 s vor der Grenze reproduzierbar).

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
- **Schalter:** `ansichten` in `docs/config.js` und Secret `statische_bereiche` (siehe Anleitung).
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

## Statische Rangliste

- **Darstellung:** Portierung von `app.rang_karte` und `quelle.turner_details` (`docs/rangliste.js`): Programm, AK,
  Ebenen Gesamt/Technik/Athletik, kompakte Zeile (Rang, Turner, Verein, Total), Details erst nach Antippen;
  Gesamt gestuft (Total Technik → Geräte → Linien, Total Athletik → Beweglichkeit/Kraft → Tests, Gesamt);
  Technik je Gerät aufklappbar mit Linien S/T und „(Alternative)“ beim Sprung; Athletik je Test, Total
  Beweglichkeit/Kraft/Athletik. Gleiche CSS-Regeln wie `app.py`.
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

## Tests

Stand 14.10.2026 (Einzelheiten in den Nachträgen):
- `python -m pytest tests` – 328 Tests, drei volle Läufe hintereinander grün; darin die Secrets `live_url` und
  `statische_bereiche` (alle, teilweise, ungültig), die Pille „Gerät“ und der Rückfall aus „Gerät“.
- `rangliste_daten.py` + `rangliste_test.mjs` (Chromium gegen nachgebildete Data API, nur synthetische Stände):
  **126 von 126 Ansichten identisch mit der Streamlit-Darstellung** (102 Ranglisten, übrige Startliste/Zeitplan);
  Stände Freigabe 0, Rotationsgrenze, alle Rotationen, Korrektur, ältere Publikation ohne `struktur`, vollständiger
  Testtag (108 Personen). Dazu Wechsel ohne Neuladen, Adresse, Zurück/Vorwärts, Korrektur mit offenen Details,
  offline, Prüfsummenfehler, verborgen, Archivlink, 390/1280 px, Schalter `ansichten` (fehlende Ansicht → Streamlit
  ohne Datenabruf) und `rueckfall: true` (alle Ansichten, „Gerät“ als Technik-Rangliste) – alle bestanden.
- `pruefsumme_test.mjs` – Prüfsummen von `live.js` gleich wie beim Publisher (synthetische Zeilen).
- `navigation_test.mjs` – Streamlit mit `live_url` und `statische_bereiche = ["rangliste", "startliste", "zeitplan"]`,
  synthetischer Testtag in Supabase: alle Wege zwischen den vier Ansichten, alte Streamlit-Links je Ansicht.
- `rueckfall_test.mjs` – `rueckfall: true`, Streamlit ohne `live_url`/`statische_bereiche`: Umleitung ohne
  Supabase-Abruf auf die jeweilige Ansicht in Streamlit.
- `funktionstest.mjs` – Live (Filter, offline, verborgen).

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

## Startliste und Zeitplan in Streamlit (gemessen vor der statischen Umsetzung)

Produktive Streamlit-App (Code von `main`), synthetischer Testtag, 50 Sitzungen öffnen innerhalb von 2,5 s den Testtag
und wechseln zur Startliste bzw. zum Zeitplan, Publikation alle 30 s, je 4 min (`streamlit_wechsel.py … startliste`
bzw. `zeitplan`), 13.10.2026 ab 16:22:

| | Startliste | Zeitplan | zum Vergleich Rangliste (früher am Tag) |
|---|---|---|---|
| sichtbar nach Seitenaufruf, Median / Max | 114,6 / 115,8 s | 83,5 / 84,7 s | 42,1–42,4 / 43,9–46,3 s |
| Seitenaufruf HTTP (inkl. Weiterleitungen), Median | 4,1 s | 4,5 s | 3,5–4,0 s |
| Laufdauer je 5-s-Takt, Median / Max | 45,3 / 54,2 s | 28,8 / 55,4 s | 14,1–14,9 / 32,6–34,1 s |
| erreicht / Trennungen | 50/50 / 0 | 50/50 / 0 | – |

- Streamlit führt für einen Live-Testtag auch Startliste und Zeitplan alle 5 s für jede Sitzung neu aus; bei
  50 Sitzungen staut sich das in einem Prozess. Die Startliste (alle 108 Personen in Tabellen) ist dabei teurer als
  eine Rangliste einer AK.
- Einschränkungen: Die App war an diesem Nachmittag langsamer als am Morgen (schon 2 Sitzungen brauchten 7–12 s je
  Lauf); die Werte sind daher eher pessimistisch, die Grössenordnung aber eindeutig. Der erste Lauf jeder Sitzung
  zeichnete auf `main` noch Live (4 301 Resultate); mit `live_url` entfällt das, der 5-s-Takt bleibt.
- Folge: Startliste und Zeitplan sind seit dem Nachtrag vom 13.10.2026 abends ebenfalls statisch
  (Messung dort: 0,1–0,9 s); Streamlit zeigt sie nur noch im Rückfall.
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

## Vorgemerkt vom 13.10.2026 – umgesetzt am 14.10.2026

Siehe Nachtrag 14.10.2026 oben. Live-Liste unverändert.
