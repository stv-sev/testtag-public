# testtag-public

Öffentliche Testtag-Ansicht (Streamlit, `app.py` / `quelle.py`) und P0-Durchstich für die Publikation:

    lokal synchronisierte SharePoint-Excel  →  bereinigtes Diagnose-JSON  →  Supabase Postgres (Data API)
                                                                          →  Rücklesen mit Publishable Key (RLS)

Grundsätze:

- **Excel bleibt die verbindliche Originalquelle.** Die Datenbank ist nur Publikationsspeicher.
- **Eigenes Supabase-Projekt** nur für `testtag-public`. Das interne Projekt `testtag` wird nicht angebunden.
- **Secret Key** (`sb_secret_…`) bleibt ausschliesslich auf dem lokalen Publikationslaptop.
- **Streamlit** (`app.py`, `quelle.py`) erhält nur Projekt-URL und **Publishable Key** (`sb_publishable_…`).
- Neue Supabase-Schlüssel werden nur im Header `apikey` gesendet, nie als `Authorization: Bearer`.
  Ältere JWT-Schlüssel (anon / service_role) werden nicht unterstützt.
- P0 schreibt nur einen Probe-Datensatz mit Metadaten, Zählwerten und Prüfsummen – keine Namen, Jahrgänge,
  Vereine, IDs, Resultate oder lokalen Pfade. Kein Supabase Storage, kein Graph/SharePoint-API-Zugriff.

## Einrichtung (einmalig, manuell)

1. **Neues Supabase-Projekt anlegen** (z. B. `testtag-public`), getrennt vom bisherigen Projekt.
2. **Schema anlegen:** Inhalt von [`supabase/schema.sql`](supabase/schema.sql) im Supabase **SQL Editor**
   ausführen. Das Skript ist wiederholbar und löscht keine Daten. Die Kontrollabfrage am Ende muss zeigen:

   | Tabelle | RLS | anon / authenticated | service_role |
   |---|---|---|---|
   | `testtage` | an | SELECT | SELECT, INSERT, UPDATE |
   | `veroeffentlichung` | an | SELECT | SELECT, INSERT, UPDATE |
   | `archiv_snapshots` | an | SELECT | SELECT, INSERT |

   `PUBLIC` hat keine Rechte. Lesen über den Publishable Key nur für öffentliche Testtage
   (`ist_oeffentlich = true`), Archiv zusätzlich nur bei `status = 'archiviert'`.
3. **Schlüssel** unter *Project Settings → API Keys* entnehmen (Publishable Key und einen Secret Key).
4. **Python-Abhängigkeiten:**

   ```powershell
   python -m pip install -r requirements.txt     # streamlit (App) und openpyxl (P0)
   ```

## Lokale Konfiguration (`.env`, P1F)

Publisher und Dauerpublisher laden automatisch die Datei `.env` im Projektstamm. Eine virtuelle Umgebung muss
dafür nicht aktiviert sein. Vorlage ist [`.env.example`](.env.example) (nur Platzhalter, versioniert):

```env
SUPABASE_URL=https://<projekt>.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
SUPABASE_SECRET_KEY=sb_secret_...
TESTTAG_WORKBOOK_AK5_AK9PLUS="<absoluter lokaler Pfad>"
TESTTAG_WORKBOOK_AK9_AK12="<absoluter lokaler Pfad>"
TESTTAG_POLL_SECONDS=1
```

- `.env` und `.env.*` sind durch `.gitignore` ausgeschlossen. Schlüssel nie in Tickets, Chats oder
  Kommandozeilenargumente schreiben.
- Gesetzte Prozessvariablen haben Vorrang vor `.env`.
- Nur die sechs bekannten Variablen sind zulässig. Unbekannte, doppelte oder ungültige Zeilen brechen ab, die
  Meldung nennt nur Zeile und Variablenname.
- Werte mit Leerzeichen gehören in `"…"`, Inhalte werden wörtlich übernommen. Es gibt keine Variablenersetzung
  und keine mehrzeiligen Werte. Gelesen wird nur mit der Standardbibliothek (`testtag_public.local_config`).
- `TESTTAG_POLL_SECONDS`: ganze Zahl von 1 bis 60, Standard 1. Eine bestehende `.env` mit `5` verzögert jede
  Publikation um bis zu rund 10 s und sollte auf `1` gesetzt werden.
- Einmalige Anlage aus der aktuellen Prozessumgebung:
  `python -B -m testtag_public.local_config --init`.
  - Das geschieht nur, wenn `.env` fehlt und `git check-ignore` den Ausschluss bestätigt.
  - Werte werden nie ausgegeben, unter Windows erhält nur der aktuelle Benutzer Zugriff (`icacls`).
  - Eine bestehende `.env` bleibt unverändert.
- Ohne `--init` zeigt der Befehl nur an, welche Variablen gesetzt sind.

Für einzelne Aufrufe aus dem Projektstamm ohne `start_live.cmd`:
`$env:PYTHONPATH = "src"; $env:PYTHONDONTWRITEBYTECODE = "1"`.

## Aufruf

Nur lokal prüfen (keine Netzwerkverbindung, keine Supabase-Variablen nötig):

```powershell
python -B -m testtag_public.probe `
  --workbook "<pfad>\261009-Test-AK5-AK9plus.xlsx" `
  --output "out\probe.json"
```

Probe in Supabase veröffentlichen und prüfen:

```powershell
python -B -m testtag_public.probe --workbook "<pfad>.xlsx" --output "out\probe.json" --publish-probe
```

Ablauf mit `--publish-probe`:

1. Excel unverändert lesen (Stabilitätsprüfung, temporäre Kopie) und Diagnose-JSON erzeugen.
2. `Public!B6` (`public_active`) muss WAHR sein, sonst Abbruch vor jedem Schreibzugriff.
3. Testtag über `slug` (= `Public!B2`) anlegen/aktualisieren (`on_conflict=slug`), UUID übernehmen.
   Gesetzt werden `titel` (B3), `ist_oeffentlich` (B6), `stand_am`; `status`, `datum`, `ort` bleiben unberührt.
4. Probe-Datensatz mit **Secret Key** upserten (`on_conflict=oeffentliche_testtag_id,bereich,programm,ak`):
   `bereich='probe'`, `programm` = `Public!B4`, `ak=''`, `inhalt` = Diagnose-JSON,
   `pruefsumme` = SHA-256 der kanonischen JSON-Form, `stand_am` = UTC.
5. Denselben Datensatz über den vollständigen Primärschlüssel mit **Publishable Key** zurücklesen.
6. `inhalt` semantisch vergleichen (JSONB darf Feldreihenfolge ändern), kanonisch neu serialisieren,
   Prüfsumme vergleichen. Erfolg nur bei vollständiger Übereinstimmung.
7. **Schreibschutz nachweisen:** drei Schreibversuche mit Publishable Key, die selbst bei falschen Rechten keine
   Daten verändern würden (INSERT mit nicht existierender Testtag-UUID, INSERT mit ungültigem Slug, UPDATE mit
   unverändertem Wert über den vollständigen Primärschlüssel). Jeder Versuch muss mit HTTP 401/403 und
   Postgres-Code `42501` (permission denied) abgelehnt werden; jede andere Antwort gilt als Fehler.
8. Erst danach wird `out\probe.json` atomar ersetzt. Bei jedem Fehler bleibt eine bestehende Ausgabe unverändert.

Kanonische JSON-Form: `json.dumps(…, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8.

### Exitcodes

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Unerwarteter Fehler (ohne Details) |
| 2 | Aufruffehler (Argumente) |
| 3 | Excelquelle: fehlt, gesperrt, instabil, OneDrive-Platzhalter, ungültig |
| 4 | Validierung: Pflichtblätter, `Public!A2:B6`, `public_active` für `--publish-probe` |
| 5 | Supabase-Konfiguration (fehlende Variable, falscher Schlüsseltyp) |
| 6 | Schreiben in die Datenbank |
| 7 | Rücklesen / Inhalt oder Prüfsumme weicht ab |
| 8 | Lokale Ausgabedatei |
| 9 | Sicherheitsprüfung: Publishable Key konnte schreiben oder Ablehnung nicht eindeutig |

## Excelprüfung

1. Grösse und Änderungszeit erfassen, warten (`--stability-seconds`, Standard 2 s), erneut prüfen.
2. OneDrive-Platzhalter (nicht lokal vorhandene Dateien) werden abgelehnt.
3. Temporäre Kopie anlegen, nur diese lesen, danach entfernen. Die Quelle bleibt unverändert. Unter Windows
   wird die Quelle nur lesend und mit vollständiger Freigabe (Lesen, Schreiben, Löschen) geöffnet, damit eine in
   Excel geöffnete Mappe gelesen werden kann (P1I).
4. Pflichtblätter `Public`, `Noteneingabe`, `Startliste`, `Zeitplan`; `Public!B2:B6` mit Beschriftungen in `A2:A6`.
5. `Noteneingabe` wird zweimal geladen (Formeln / gespeicherte Resultate). openpyxl berechnet nichts.
   `dimension` = tatsächlich belegter Bereich, `dimension_declared` = in der Datei deklarierter Bereich.
6. `cached_values_sha256`: SHA-256 über alle Formelzellen (Adresse, Typ, gespeicherter Wert). Leere Resultate
   sind kein Fehler.

## Tests

```powershell
python -B -m unittest discover -s tests -v
```

Nur synthetische Arbeitsmappen in temporären Verzeichnissen. Die Data API wird im Speicher simuliert
(`tests/_support.py`); echte Netzwerkverbindungen sind in den Tests global gesperrt.
`supabase/schema.sql` wird statisch geprüft (keine Datenbankverbindung).

## Kontrollierter Excel-Änderungstest (manuell)

Das Programm verändert die Exceldatei nie. Die Änderung erfolgt von Hand:

1. Diagnose ausführen, `cached_values_sha256` (Ausgabe „Resultat-Hash“) notieren.
2. In einem Eingabeblatt (z. B. einer Gerätenote) einen zulässigen Testwert ändern.
3. In Excel speichern, OneDrive-Synchronisation vollständig abschliessen lassen (grünes Häkchen).
4. Diagnose erneut ausführen (mit `--publish-probe`, falls auch die Datenbank geprüft werden soll).
5. Der Resultat-Hash muss sich geändert haben; mit `--publish-probe` auch die gespeicherte `pruefsumme`.
   Bleibt der Resultat-Hash gleich, ist P0 **nicht bestanden** (keine Ersatzberechnung der Formeln).
6. Testwert fachlich korrekt zurücksetzen, speichern, synchronisieren und erneut prüfen, dass der
   ursprüngliche Resultat-Hash wieder erscheint.

## P1B: lokaler Publisher-Kern (ohne Upload)

Module:

- `excel_publication.py` liest eine Testtag-Arbeitsmappe nur lesend (temporäre Kopie und gespeicherte Resultate
  aus `excel_probe`). Zusätzlich prüft er das XML der verwendeten Blätter: Eine verwendete Formelzelle ohne
  gespeichertes Resultat oder mit Excel-Fehlerwert ist ein Fehler. Kosmetische Reste ausserhalb der
  gelesenen Zellen bleiben ohne Wirkung. `Noteneingabe` wird über die Kopfzeilen erkannt: Gerät, Linie,
  Sonderzeichen, Suchbereich, Ergebnisbereich und Feld.
- `publication_model.py` ist die reine Logik: Validierung, Sichtbarkeit, Summen, Ranglisten, Live-Vergleich,
  kanonischer Stand und Publikationszeilen.
- `publication_check.py` ist ein Prüflauf mit ausschliesslich anonymen Kennzahlen.

Regeln:

- **Testtag:** Die Mappen gehören zusammen, wenn `Public!B2` (Slug) und `B3` (Titel) gleich sind. `B4` ist das
  Programm (`AK5-AK9+` oder `AK9-AK12`, je einmal). `B6` = WAHR gibt das Programm frei, bei FALSCH entstehen
  keine öffentlichen Inhalte.
- **Rotation:** `B5` (0–8 bzw. 0–7) begrenzt Technik und Vor-Ort-Athletik auf `id_rotation <= B5`.
  Extern erfasste Athletik hat keine Rotation und ist immer sichtbar.
- **Athletikmodus:** `B7` Kraft und `B8` Beweglichkeit, je `vor_ort` oder `extern`, in AK9–AK12 nur
  `extern`. Verwendet wird ausschliesslich die gewählte Quelle. Ein Wert in der anderen Quelle ist ein
  Konflikt, auch eine Null. Vorbefülltes TAL allein ist kein Resultat.
- **Technik:** öffentlich `S` = SKS und `T` = Endwert. `End = SKS × TAL` wird geprüft. TAL, interne IDs und
  Suchschlüssel werden nicht veröffentlicht. Eine Wertung in einer nicht vorgesehenen Linie (`akMinMax` 0/0 oder
  ohne Testprogramm-Definition) ist ein Konflikt.
- **Sprung AK9–AK12:**
  - AK9: Excel-Linien 1 und 2 erscheinen öffentlich neutral als `Sprung 1` / `Sprung 2`, auch bei 0/0.
  - AK10–AK12: Linie 1 plus genau eine Alternative (Linie 2 oder 3). Beide Alternativen gleichzeitig sind ein
    Konflikt.
- **Summen und Ränge:**
  - Technik ist die Summe der sichtbaren `T`-Werte, Athletik ist Beweglichkeit plus Kraft, Gesamt ist
    Technik plus Athletik. Excel-Totale werden nie übernommen.
  - Alle angemeldeten Personen erscheinen. Bei Gleichstand gilt `1, 2, 2, 4`, ohne Stichentscheid.
- **Teilnehmerschlüssel:** SHA-256 über Slug, Programm, Name, Vorname, Jahrgang und Verein, normalisiert
  mit NFKC, bereinigten Leerzeichen und einheitlicher Gross-/Kleinschreibung. Kollisionen brechen ab.
- **Live-Liste:** `merge_live(vorher, aktuell, beobachtet_am)`. Neue oder geänderte Resultate erhalten den
  Beobachtungszeitpunkt, unveränderte behalten ihren. Sortiert wird absteigend nach `geaendert_am`, dann
  nach `ergebnis_id`.
- **Publikationszeilen:** `publication_rows(stand)` erzeugt `zeitplan` (einmal), `startliste` (je Programm) sowie
  `resultate_live` und `resultate_rangliste` (je Programm und AK), mit `schema_version`, `art`, `gruppen` und
  gegebenenfalls `hinweis`. `pruefsumme` ist SHA-256 über den kanonischen Inhalt ohne Zeitstempel
  (`geaendert_am`).
- **Fehler:** Jeder Struktur- oder Datenkonflikt bricht vollständig ab (`PublicationError`). Die Meldung trägt
  einen stabilen Code, z. B. `ATHLETIK_BEIDE_QUELLEN`, und enthält keine Namen und keine Pfade.

Prüflauf (nur lesend, keine Ausgabedatei, kein Netzwerk):

```powershell
python -B -m testtag_public.publication_check `
  --workbook "<pfad>\261009-Test-AK5-AK9plus.xlsx" --workbook "<pfad>\261009-Test-AK9-AK12.xlsx"
```

Exitcodes: 0 gültig · 1 unerwartet · 2 Aufruf · 3 Excelquelle · 4 Validierung (mit Fehlercode).

### Liniencodes (verbindliche Entscheidung)

Interne Liniencodes stammen aus der stabilen Gerätekennung (`GerID` in `tab_startgeraet` = Gerätecode `ç` im
Testprogramm), **nicht** aus der Position eines Geräts in der Rotation:

| Bereich | Code |
|---|---|
| Sprung | `5-1`, `5-2`, `5-3` (auch in AK9–AK12, obwohl dort Sprung die 4. Rotationsstation ist) |
| Kraft | `9-n` |
| Beweglichkeit | `4-n` |

Programm, Gerätekennung, Rotationsposition und öffentliche Anzeige sind getrennt. Dass die Beweglichkeitsstation
in AK9–AK12 fehlt, verändert keine Gerätenummer.

Öffentlich zeigt AK9 neutral `Sprung 1` / `Sprung 2` (Code `sprung-1` / `sprung-2`). Der betrieblich umgenutzte
zweite Platz erscheint nie als „Tsukahara“. AK10–AK12 zeigen die tatsächliche Sprungart.

## P1C: Datenbank-Publisher (einmaliger Lauf)

```powershell
# Probelauf (Standard): kein Netzwerk, keine Umgebungsvariablen, keine Ausgabedatei
python -B -m testtag_public.publisher --workbook-ak5 "<pfad>\261009-Test-AK5-AK9plus.xlsx" `
  --workbook-ak9 "<pfad>\261009-Test-AK9-AK12.xlsx"

# Echte Publikation – nur mit exakter Slug-Bestätigung und gesetzten SUPABASE_*-Variablen
python -B -m testtag_public.publisher --workbook-ak5 "…" --workbook-ak9 "…" `
  --publish --confirm-slug aargau-lenzburg-2026-10-09
```

Ablauf mit `--publish` (`database_publisher.publish`):

1. Excel lesen und vollständig validieren. Bei einem Fehler wird vor dem ersten Netzwerkzugriff abgebrochen.
2. Den Testtag über den Slug lesen. Ist er archiviert, wird abgebrochen. Danach werden die verwalteten
   Publikationszeilen gelesen (Secret Key).
3. Den vorherigen Live-Stand aus `resultate_live` rekonstruieren und mit `merge_live` zusammenführen. Eine
   unbekannte `schema_version` führt zum Abbruch mit `SCHEMA_INKOMPATIBEL`, ohne Schreibvorgang.
4. Den Endzustand bestimmen. Verwaltete Schlüssel, die nicht mehr gewünscht sind (gesperrtes Programm,
   entfallene AK), werden zu **Tombstones**: `{"schema_version", "art", "aktiv": false, "gruppen": []}`, ohne
   Personendaten. Verwaltet werden nur `zeitplan`, `startliste`, `resultate_live` und
   `resultate_rangliste`. `probe` bleibt unberührt.
5. Nur Zeilen schreiben, deren Prüfsumme oder kanonischer Inhalt sich geändert hat. Alle diese Zeilen gehen in
   **einem** Batch-Upsert über den vollständigen Primärschlüssel. Die Antwort muss genau die gesendeten Schlüssel
   und Prüfsummen bestätigen. Bei einem Fehler gibt es keine Einzelversuche und keine Erfolgsmeldung.
6. Neuer Testtag: Er wird vor dem Batch als `vorbereitet` und nicht öffentlich angelegt und erst danach freigegeben
   (`live`, öffentlich). Ein bestehender Testtag behält UUID, `datum` und `ort`, sein Titel wird bei Bedarf
   aktualisiert.
7. Mit dem **Publishable Key** zurücklesen und vollständig prüfen: Anzahl, Schlüssel, Prüfsumme, kanonischer
   Inhalt, `stand_am`, Tombstones ohne Daten, keine nicht freigegebene Rotation, keine Daten gesperrter
   Programme.
8. Erst danach `testtage.stand_am` setzen. Ist kein Programm mehr freigegeben, wird zugleich
   `ist_oeffentlich = false` gesetzt, und zwar nach dem Bereinigen.
9. Den öffentlichen Gesamtstand erneut prüfen.

Ist nichts geändert, wird keine Schreibanfrage gesendet (Status `unverändert`), und `stand_am` bleibt.
Die Ausgabe läuft ausschliesslich über `SafeReport`. Sie zeigt Slug, Programme, AK, Anzahlen, gekürzte
Prüfsummen, HTTP-Status und Fehlercodes. Bekannte Personenangaben, Teilnehmerschlüssel, Schlüssel und Pfade
werden zusätzlich geschwärzt. Fehlermeldungen der Datenbank enthalten nur HTTP-Status und Postgres-Code, nie
die Serverantwort.

Die Streamlit-App muss Tombstones (`inhalt.aktiv == false`) später ignorieren. Das gehört nicht zu P1C.

Exitcodes: 0 Erfolg · 1 unerwartet · 2 Aufruf/Bestätigung · 3 Excelquelle · 4 Validierung · 5 Konfiguration
· 6 Schreiben · 7 Lesen/Rücklesen.

## P1D: öffentliche Streamlit-App (`app.py`, `quelle.py`)

Die App liest ausschliesslich über PostgREST mit dem **Publishable Key**, nur im Header `apikey`, nie mit
`Authorization` oder `Bearer`. Sie hat keinen Schreibweg und keine Anmeldung.

Streamlit-Secrets (nur in Streamlit Community Cloud hinterlegen, keine lokale `secrets.toml`):

```toml
supabase_url = "https://<projekt-ref>.supabase.co"
supabase_publishable_key = "sb_publishable_…"
# zeitueberschreitung = 5      # optional, Sekunden (0 < Wert <= 60)
```

Secret-/Service-Role-Namen (z. B. `supabase_secret_key`) und Schlüssel, die nicht mit `sb_publishable_` beginnen,
werden abgewiesen. Die URL muss `https://` ohne Pfad sein.

**Adressen:**

- Ohne Parameter zeigt die App eine Übersicht aller öffentlichen Testtage: Live, Demnächst (vorbereitet),
  Archiv. Innerhalb jeder Gruppe steht der zuletzt aktualisierte Testtag zuerst.
- `?t=<slug>` öffnet einen Testtag (bevorzugt), z. B. `?t=aargau-lenzburg-2026-10-09`.
- `?t=<uuid>` wird für ältere Links weiterhin aufgelöst und auf den Slug umgestellt. Angezeigte Links verwenden
  immer den Slug.
- Ungültige Kennungen (nicht Slug-Schema und nicht UUID) erreichen PostgREST nie. Unbekannte oder nicht
  öffentliche Testtage zeigen eine neutrale Meldung.

**Datenfluss:**

1. Den Testtag über Slug oder UUID auflösen (`testtage`).
2. Alle Zeilen `veroeffentlichung` des Testtags laden.
3. Die Zeilen prüfen:
   - Primärschlüssel sind eindeutig.
   - `schema_version` ist `publikation-1`.
   - Die Prüfsumme stimmt (`publication_model.content_checksum`, dieselbe Funktion wie im Publisher).
4. Ausblenden: Tombstones (`inhalt.aktiv == false`), `probe` und unbekannte Bereiche.
5. Den validierten Stand in `st.session_state` ersetzen, unter dem Schlüssel `stand|<testtag-uuid>`.

Jeder Fehler verwirft den ganzen Ladevorgang. Ist ein früherer gültiger Stand vorhanden, bleibt er sichtbar mit
dem Hinweis „Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt“. Beim ersten
Laden erscheint eine neutrale Meldung, nie eine scheinbar gültige leere Rangliste.

`quelle.py` ergänzt `src/` selbst im Importpfad. Damit funktioniert der Import von `testtag_public` lokal aus
dem Projektstamm und auf Streamlit Community Cloud.

**Ansichten:**

- **Live:** letzte Änderungen zuerst. Sortiert wird nach `geaendert_am` absteigend, dann nach Ergebnis-ID
  (`publication_model.sort_live`). Filter gibt es für Programm und AK (Standard „Alle“).
  - Technik zeigt `S` (SKS) und `T` (Linienpunkte).
  - Athletik zeigt die fertige Punktzahl.
  - AK9 zeigt `Sprung 1` / `Sprung 2`.
  - Nie angezeigt werden TAL, IDs, Teilnehmerschlüssel und Liniencodes.
- **Ranglisten:** Auswahl nach Programm, AK und Art (Gesamt, Technik oder Athletik). Rang und Reihenfolge kommen
  unverändert vom Publisher, etwa `1, 2, 2, 4`.
  - `–` bedeutet: Für diese Person sind noch keine Resultate veröffentlicht.
  - Einzelheiten je Person sind aufklappbar (Teilsummen des Publishers und die Einzelresultate aus Live).
- **Startliste:** je Programm, dann nach Startgerät / Riege in der Reihenfolge des Publishers, nach
  Startposition. Leere Gruppen entfallen.
- **Zeitplan:** ein gemeinsamer Tagesablauf, AK5–AK9+ vor AK9–AK12, in der Reihenfolge des Publishers.

Nur Testtage mit Status `live` aktualisieren sich etwa alle 5 Sekunden (`st.fragment(run_every="5s")`, keine
neue Abhängigkeit). Die Lesezugriffe werden zwischen allen Zuschauern geteilt (`st.cache_data`):

- **Änderungsabfrage:** `testtage` (`stand_am`, Status, Sichtbarkeit) einmal je 3-s-Zeitfenster für alle.
- **Datenstand:** alle Zeilen, vollständig geprüft (Schema, Prüfsummen, Tombstones), je Testtag, Status und
  `stand_am`. Der Publisher setzt `stand_am` erst nach allen Zeilen; ein neuer `stand_am` lädt einmal für alle.
- **Kontrollladung:** einmal je 5-min-Zeitfenster für alle gemeinsam, falls Zeilen ohne neuen `stand_am`
  geändert wurden (Publikation brach nach dem Schreiben der Zeilen ab). Kein Reload je Zuschauer.
- **Fehler** werden nie zwischengespeichert. Jede Sitzung behält ihren letzten gültigen Stand mit Hinweis.
- **Ausfallbremse** (`quelle.AUSFALL`, prozessweit für alle Zuschauer): Nach einem Netz-, Zeit- oder Serverfehler
  (HTTP 5xx/429) senden alle Zuschauer eine gemeinsame Sperrzeit lang keine Anfrage (2, 4, 8, höchstens 10 s);
  danach prüft genau ein Abruf, ob Supabase wieder antwortet. Daten-, Prüfsummen- und Zugangsfehler zählen nicht.
- **Seitenaufruf während eines Ausfalls:** Hat die Sitzung noch keinen Stand, zeigt die Seite einen Hinweis und
  versucht es im Takt automatisch erneut; nach der Wiederherstellung baut sie sich selbst neu auf (Daten oder
  „nicht verfügbar“, falls der Testtag inzwischen verborgen wurde). Gilt auch für die Übersicht.
- **Verborgen:** liefert die Änderungsabfrage keinen öffentlichen Testtag mehr, verwirft die Sitzung ihren Stand und
  zeigt „nicht verfügbar“ – kein Weiterzeigen aus Cache oder Sitzung.
- **Archiv:** einmal je Sitzung (aus dem gemeinsamen Cache), danach eingefroren, keine Abfragen.
- **Komprimierung:** Anfragen senden `Accept-Encoding: gzip`; Supabase antwortet komprimiert (gemessen: Datenstand
  161 324 B → 34 856 B). Entpackt wird mit der Standardbibliothek, höchstens 64 MB.

Gemessen mit 10 gleichzeitigen Zuschauern und einem vollständig gefüllten synthetischen Testtag (2,0 MB entpackt):
eine Ladung des Datenstands (138 KB übertragen) je Publikation für alle, sonst rund eine kleine Abfrage je 3–5 s
für alle zusammen; Publikation bis Anzeige 2–8 s.

Vorbereitete und archivierte Testtage werden je Sitzung einmal geladen.

Archivierte Testtage zeigen ausschliesslich ihren Snapshot aus `archiv_snapshots` (P1F, siehe unten).

Zeiten werden in `Europe/Zurich` angezeigt. Ohne Zeitzonendaten gilt als Rückfall die EU-Sommerzeitregel.

## P1F: Dauerpublisher, Archivierung, Archivquelle der App

### Dauerpublisher (`live_runner.py`, `start_live.cmd`)

```powershell
.\start_live.cmd --check                        # beide Arbeitsmappen einmal prüfen, ohne Netzwerk
.\start_live.cmd aargau-lenzburg-2026-10-09     # Dauerbetrieb; Beenden mit Ctrl+C (danach ggf. "J")
```

`start_live.cmd` wechselt immer in den Projektstamm und setzt `PYTHONPATH=src`. Es verwendet
`.venv\Scripts\python.exe`, falls vorhanden, sonst `python` bzw. `py -3` aus dem Suchpfad. Fehlt Python, erscheint
eine Meldung. Pakete werden nicht automatisch installiert. Der Slug muss exakt `Public!B2` beider Mappen
entsprechen, sonst publiziert der Dauerbetrieb nichts.

Ablauf je Takt (`TESTTAG_POLL_SECONDS`):

1. Nur Grösse und Änderungszeit beider Dateien prüfen. Ist der Stand schon verarbeitet, passiert nichts.
2. Bei einer Änderung warten, bis jede Datei mindestens 2 s und in mindestens zwei aufeinanderfolgenden Takten
   unverändert ist. Die Mappen dürfen in Excel geöffnet sein. Excel-Sperrdateien (`~$…`) spielen keine Rolle (P1I).
3. Nur geänderte Mappen über die sicheren temporären Kopien lesen. Eine Mappe mit gleicher Grösse und Änderungszeit
   wie beim letzten erfolgreichen Lesen wird nicht erneut gelesen. Beide gemeinsam validieren; der Slug muss dem
   bestätigten entsprechen. Nach einem Neustart werden beide gelesen.

   **Geöffnete Excel-Mappe als zusätzlicher Auslöser** (`excel_live.py`, nur Windows mit Excel Desktop und
   `pywin32`): Eine in Excel Online eingegebene Note erscheint über Co-Authoring in der geöffneten Desktop-Mappe,
   die lokale Datei erhält aber oft erst nach bis zu rund 1,5 Minuten einen neuen Speicherstand. Ist die Datei
   unverändert, prüft der Publisher deshalb alle 2 s einen Fingerabdruck der vom Publisher gelesenen Blätter in der
   geöffneten Mappe (nur lesen). Ändert er sich und bleibt er zwei Prüfungen lang gleich, wird mit
   `SaveCopyAs` eine temporäre Kopie erstellt und wie eine Datei gelesen, validiert und publiziert. Voraussetzungen:
   Berechnung automatisch und abgeschlossen, Excel nicht beschäftigt (keine Zelle in Bearbeitung), Fingerabdruck und
   Berechnungsstatus vor und nach der Kopie gleich. Fehlende Formelresultate oder Excel-Fehlerwerte in der Kopie
   führen wie beim Dateiweg zu keiner Publikation. `SaveCopyAs` verändert weder die Originaldatei noch ihren
   Speicherzustand und lädt nichts hoch (nachgewiesen an einer Testmappe; Excel setzt nur die Eigenschaft
   „Zuletzt gespeichert“ der geöffneten Mappe im Speicher). Derselbe Excel-Stand wird nie zweimal verarbeitet;
   speichert Excel ihn später in die Datei, folgt ein Abgleich ohne Schreibanfrage. Ist die Mappe nicht geöffnet,
   gilt unverändert nur der Dateiweg.
4. Hat sich der Inhalt (SHA-256) nicht geändert, wird keine Anfrage gesendet. Sonst folgt eine P1C-Publikation: nur
   geänderte Zeilen, öffentliches Rücklesen, gleiche Regeln für Korrektur, Rotationsgrenze und Sperre.
5. Erst nach Erfolg gilt der kombinierte Stand als verarbeitet.

Fehlerverhalten:

- **Datei-, Netzwerk- und Datenbankfehler:** Der alte öffentliche Stand bleibt. Neue Versuche folgen nach 1×, 2×,
  4× … Takt, höchstens alle 60 s.
- **Validierungsfehler:** Der alte Stand bleibt. Neuer Versuch erst nach der nächsten Dateiänderung.
- **Archivierter Testtag:** keine Schreibanfrage mehr, nur noch ein Protokolleintrag.

Wiederanlauf:

- Beim Start und nach jedem Neustart gibt es einmal einen vollständigen Abgleich mit Supabase. Ist der Stand
  identisch, wird nichts geschrieben.
- `out/live-<kennung>.lock` (PID und Startzeit) verhindert einen zweiten Dauerpublisher für dieselben Mappen.
- Eine Sperre eines beendeten Prozesses wird als veraltet erkannt und ersetzt (Windows prüft auch die
  Prozess-Startzeit). Bei Ende und Ctrl+C wird die Sperre entfernt.

Protokoll: `out/live_runner.log` (rotierend, 256 KB × 4) und die Konsole enthalten nur Zeitpunkt,
Start und Ende, Programm, Dateizustand, Validierung oder Fehlerkategorie, Anzahl geänderter Zeilen,
Bestätigung oder geplante Wiederholung sowie den Archivstatus.

Exitcodes: 0 Ende · 1 unerwartet · 2 Aufruf · 3 Excelquelle · 4 Validierung · 5 Konfiguration · 10 Sperre aktiv.

### Unveränderliche Archivierung

```powershell
python -B -m testtag_public.publisher --archive --confirm-archive --confirm-slug <slug>
python -B -m testtag_public.publisher --hide --confirm-slug <slug>      # danach nicht mehr öffentlich
```

Ohne beide Bestätigungen wird nichts archiviert. Ablauf (`archive.py`):

1. Beide Mappen validieren. Mindestens ein Programm muss freigegeben sein (`ARCHIV_LEER`).
2. Besteht noch kein Snapshot, werden die letzten Änderungen normal publiziert und öffentlich geprüft.
3. Der Archivinhalt `archiv-1` wird gebildet: Testtag-Metadaten und alle aktiven Zeilen (Zeitplan, Startlisten,
   Live-Endstand, Ranglisten) mit Prüfsumme und `stand_am`. Probe, Tombstones und unbekannte Bereiche gehören nicht
   dazu. Die Prüfsumme ist SHA-256 über den kanonischen Inhalt.
4. Den Snapshot einmalig einfügen (INSERT, kein Upsert), mit dem Secret Key zurücklesen und prüfen.
5. Erst danach `status = 'archiviert'`. Anschliessend öffentlich zurücklesen, sofern der Testtag öffentlich ist.

Unveränderlichkeit:

- Es gibt pro Testtag genau einen Snapshot und keine UPDATE- oder DELETE-Operationen. Auch das Schema gewährt
  `service_role` nur SELECT und INSERT.
- Ist der Snapshot identisch, ist eine Wiederholung idempotent (`bereits archiviert`, keine Schreibanfrage).
  Weicht er ab, bricht der Lauf mit `ARCHIV_ABWEICHUNG` ab.
- Bricht ein Lauf zwischen Snapshot und Status ab, setzt der nächste Lauf ihn fort. Ohne neue Publikation wird der
  identische Snapshot bestätigt und der Status abgeschlossen.
- Nach `archiviert` lehnen Publisher und Dauerpublisher jede Publikation ab (`TESTTAG_ARCHIVIERT`).

Die App liest archivierte Testtage nur aus `archiv_snapshots`. Geprüft werden Testtag-ID, Schema, Prüfsumme,
eindeutige Schlüssel, bekannte Bereiche, keine Probe und keine Tombstones. Fehlt der Snapshot oder ist er
ungültig, wird nichts aus `veroeffentlichung` ersetzt. Die App zeigt dann den letzten gültigen Sitzungsstand mit
Hinweis oder eine neutrale Meldung. Archivierte Testtage aktualisieren sich nicht automatisch.

## P1G: Schreibschutz auf Transportebene und Betrieb

Publisher (`--publish`, `--archive`, `--hide`) und Dauerpublisher senden Schreibanfragen nur über `SlugScope`
(`write_scope.py`). Lesende Anfragen gehen unverändert durch; aus den Antworten von `testtage` lernt die Sperre die
UUID des bestätigten Slugs. Erlaubt sind nur folgende Schreibanfragen, alle anderen werden **vor dem Senden**
abgewiesen:

- Anlage eines Testtags mit genau diesem Slug;
- `PATCH` dieses Testtags über seine UUID;
- `POST` von Publikations- und Archivzeilen dieser UUID.

Damit kann ein Lauf mit `--confirm-slug X` keinen anderen Testtag verändern, auch nicht bei einem Fehler im
übrigen Code.

Die Betriebsanleitung für den Wettkampftag steht in [BETRIEB.md](BETRIEB.md).

## P1H: Gestaltung der öffentlichen App

Die App übernimmt die visuelle Sprache der öffentlichen AMM-Anzeige von Turnsport Aargau. Inhalte, Bezeichnungen
und die gesamte Daten- und Publikationslogik bleiben testtagspezifisch und unverändert.

- **Zentrale Gestaltung:** `STIL` in `app.py`. Alle Selektoren beginnen mit `tt-` oder mit dem Schlüssel des
  Widgets (`st-key-bereich`, `st-key-rl_art`, `st-key-oeffnen-…`, `st-key-zurueck`).
- **Farben:** Dunkelblau `#0D4E73`, Blau `#15608A`, Mittelblau `#2C7FA6`, Hellblau `#6FAFC9`, Fläche `#D6EAF3`,
  Akzent Magenta `#E6007E`. Die Theme-Werte in `.streamlit/config.toml` sind identisch mit der Referenz.
- **Komponenten:**
  - Kopfband mit Logo, Verlauf und magentafarbener Unterkante;
  - Status-Badges Live (pulsierend), Demnächst und Archiv;
  - Navigation als Pillen mit mindestens 40 px Touch-Höhe;
  - Live-Ticker-Karten und Ranglisten-Karten mit Rangkreis (Gold, Silber, Bronze) und Chips; ein Chip zeigt „–“,
    solange kein Resultat veröffentlicht ist;
  - Startlisten-Tabellen mit Titelleiste und fünf Spalten (Pos., Turner = Vorname Name, Jg., AK, Verein);
  - Zeitplan als Zeilenliste ohne Tabelle; einheitliche leere Zustände und Fusszeile.
- **Mobil:** Unter 640 px werden Abstände kleiner. Lange Namen und Vereine brechen um. Es gibt keine
  `st.dataframe`-Tabellen und damit keinen horizontalen Tabellenüberlauf.
- **Sicherheit der Darstellung:** Alle angezeigten Texte laufen durch `_h` und sind damit HTML-escaped. Zusätzlich
  werden Zeichen entschärft, die Streamlit-Markdown auswerten würde (zum Beispiel `$`). Es gibt kein JavaScript.

Assets (`assets/`):

| Datei | Herkunft |
|---|---|
| `turnsport_aargau_logo.png` | Logo von Turnsport Aargau aus `amm/public_display/logo/Turnsport_Aargau_rgb.png` (lokales Referenzprojekt), verkleinert auf 271×96 px |

Das Logo wird lokal eingebettet, ohne Hotlink. Bewusst nicht übernommen wurden die Webschrift Inter (Google Fonts,
externe Abhängigkeit) sowie die AMM-Fachbegriffe und die AMM-Datenlogik.

## P1I: Betrieb mit geöffneten, über OneDrive synchronisierten Arbeitsmappen

Die produktiven Mappen bleiben während des Wettkampfs geöffnet. Kampfrichter bearbeiten sie in Excel Desktop mit
AutoSpeichern, in Excel Online auf SharePoint oder gleichzeitig über Co-Authoring.

```text
Excel Desktop oder Excel Online
→ SharePoint
→ OneDrive-Synchronisierung auf dem Publisher-Laptop
→ lokale synchronisierte XLSX-Datei
→ Dauerpublisher
→ Supabase
→ öffentliche Streamlit-App
```

- Kein Zugriff über eine SharePoint- oder Graph-API. Der Publisher überwacht nur die lokale, von OneDrive
  synchronisierte Datei und liest sie über eine temporäre Kopie. Die Originaldatei wird nie beschrieben.
- Voraussetzungen: AutoSpeichern eingeschaltet, OneDrive angemeldet und aktiv, SharePoint-Ordner mit „Immer auf
  diesem Gerät behalten“, grüner Synchronisierungsstatus vor dem Start.
- Die Excel-Sperrdatei `~$…xlsx` wird ignoriert.
- Gelesen wird erst nach zwei stabilen Prüfintervallen. Ein halb gespeicherter Stand (kein gültiger ZIP-Container,
  Grösse oder Änderungszeit ändern sich während des Kopierens) führt zu einer Wiederholung, nie zu einer
  Publikation. Ein Formelwert ohne von Excel gespeichertes Resultat wird abgelehnt (`FORMELRESULTAT_FEHLT`).
- Pausierte oder unterbrochene Synchronisierung: Der letzte gültige Stand bleibt öffentlich. Nach der
  Wiederaufnahme wird die Änderung automatisch erkannt und publiziert.

**Korrektur (P1I):** Excel hält eine über OneDrive/SharePoint geöffnete Mappe mit Löschzugriff offen. Windows lässt
dann nur Leser zu, die ihrerseits Löschen freigeben (`FILE_SHARE_DELETE`). Das bisherige `open()` gab das nicht
frei. Jeder Leseversuch scheiterte deshalb mit „Exceldatei ist gesperrt“, und der Dauerpublisher publizierte bei
geöffneter Mappe nie. `excel_probe.stable_temp_copy` öffnet die Quelle jetzt unter Windows mit `CreateFileW`,
nur lesend und mit Freigabe für Lesen, Schreiben und Löschen. Ein wirklich exklusiv gesperrter Speicherstand wird
weiterhin wie bisher wiederholt. Regressionstests: `OpenWorkbookTests` in `tests/test_live_runner.py`.

## Ranglistenansicht: Gesamt, Technik, Athletik

Die Rangliste hat drei Ebenen (Gesamt, Technik, Athletik), je rangiert nach dem Total der Ebene. Programm und AK
bleiben wählbar. Die Liste zeigt kompakt Rang, Turner, Verein und Total. Erst nach Antippen erscheinen die Details:

- **Gesamt (gestuft):** nach dem Öffnen nur Total Technik, Total Athletik und Gesamt. Total Technik klappt auf zu den
  Gerätetotalen, jedes Gerät weiter zu den Linien mit S und T; Total Athletik klappt auf zu Total Beweglichkeit und
  Total Kraft (Athletik = Beweglichkeit + Kraft), diese weiter zu den Einzeltests. Beweglichkeit und Kraft zählen nur im Athletiktotal, auch wenn ein
  Krafttest organisatorisch an einem Gerät erfasst wird.
- **Technik:** Total Technik und je Gerät ein einzeln aufklappbares Feld mit den Linien (S = gewertete SKS,
  T = Linienpunkte). Sprung AK9 heisst „Sprung 1“ / „Sprung 2“; bei AK10–AK12 erscheint die gewählte Alternative.
- **Athletik:** jeder vorgesehene Beweglichkeits- und Krafttest, Total Beweglichkeit, Total Kraft, Total Athletik.

Ränge, Gleichstände und alle Totale stammen unverändert aus der Rangliste des Publishers. Die App summiert nur die
veröffentlichten Linienpunkte je Gerät zur Anzeige. Passt die Aufschlüsselung nicht exakt zu den veröffentlichten
Totalen, zeigt sie keine Details. Ein fehlendes Resultat erscheint als „–“, eine Nullwertung als „0“.

**Publikationsformat:** Jede Zeile `resultate_rangliste` enthält zusätzlich `inhalt.struktur` mit den für die AK
vorgesehenen Geräten und Linien (`code`, `anzeige`, bei Sprung-Alternativen `alternative`) und den Athletiktests
(`code`, `test`). Die Struktur stammt nur aus dem Testprogramm (akMinMax, Athletikmodus) und enthält keine Resultate.
Sie ist nötig, damit ein noch fehlendes Resultat als „–“ erscheinen kann. Ältere Stände ohne `struktur` (z. B.
bestehende Archive) zeigen nur die veröffentlichten Linien und Tests.

Jede Zeile `startliste` enthält `inhalt.rotation` = `{stationsfolge: [{station, bereich: technik|athletik}, …],
rotationen, freigegeben}`: die Stationen in der Reihenfolge von `tab_startgeraet` und die geprüfte Freigabegrenze
(`Public!B5`). Die Riege mit Startgerät an Position *i* steht in Rotation *r* an Station (*i* + *r* − 1) mod *n*
(gleich wie `id_rotation` der Noteneingabe). Daraus zeigt die statische Seite unter Technik „Gerät & Rotation“; ältere
Stände ohne `rotation` zeigen dort nur einen Hinweis. Im Streamlit-Rückfall gibt es diese Sicht nicht: Streamlit zeigt einen Hinweis und die
Technik-Rangliste.

