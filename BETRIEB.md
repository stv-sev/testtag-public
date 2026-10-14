# Betrieb am Testtag

Kurzanleitung für den Publikationslaptop. Technische Details stehen im [README](README.md).
Alle Befehle werden in PowerShell im Projektordner `testtag-public` ausgeführt.

> **Wichtig:** Der Dauerpublisher veröffentlicht alles, was in den Arbeitsmappen freigegeben ist.
> `Public!B6` (Freigabe) und `Public!B5` (freigegebene Rotation) entscheiden, was öffentlich wird.
> Solange die Mappen Testwerte enthalten, darf der Dauerpublisher **nicht** mit dem produktiven Slug laufen.

## Datenfluss

```text
Excel Desktop oder Excel Online
→ SharePoint
→ OneDrive-Synchronisierung auf dem Publisher-Laptop
→ lokale synchronisierte XLSX-Datei
→ Dauerpublisher
→ Supabase
→ statische Seite https://stv-sev.github.io/testtag-public/ (Archiv: Streamlit, https://testtag.streamlit.app/)
```

Aufteilung, Schalter und Rückfall der öffentlichen Seite: [Öffentliche Seite](#öffentliche-seite).

- Der Publisher greift **nicht** über eine SharePoint-API zu. Er überwacht nur die lokale, von OneDrive
  synchronisierte Datei und liest sie über eine temporäre Kopie. Die Originaldatei wird nie beschrieben.
- Die Arbeitsmappen **dürfen und sollen während des ganzen Wettkampfs geöffnet bleiben**: in Excel Desktop mit
  AutoSpeichern, in Excel Online auf SharePoint und gleichzeitig durch mehrere Kampfrichter (Co-Authoring).
- Gelesen wird der letzte vollständig gespeicherte und synchronisierte Stand, erst wenn die Datei zwei
  Prüfintervalle lang unverändert ist. Ein halb gespeicherter oder noch nicht vollständig synchronisierter Stand
  wird nicht publiziert.
- Die Excel-Sperrdatei `~$…xlsx` ist normal und wird ignoriert.
- Ist OneDrive pausiert oder unterbrochen, bleiben öffentlich die letzten gültigen Daten sichtbar. Nach der
  Wiederaufnahme werden die Änderungen automatisch nachgezogen.
- Excel Desktop auf dem Publisher-Laptop speichert direkt in die lokale Datei. Seine Änderungen werden auch bei
  pausiertem OneDrive publiziert. Änderungen aus Excel Online oder von anderen Geräten kommen erst nach der
  Synchronisierung an.

## Vor dem Wettkampf

1. **`.env` prüfen:** `python -B -m testtag_public.local_config` (mit `$env:PYTHONPATH = "src"`) zeigt nur
   `gesetzt` oder `fehlt`, nie Werte. Alle sechs Variablen müssen gesetzt sein, auch die beiden Arbeitsmappenpfade.
   Schlüssel nie in Chat, Ticket, E-Mail oder Kommandozeile kopieren.
2. **OneDrive:** OneDrive muss angemeldet und aktiv sein (nicht pausiert). Für den SharePoint-Ordner der beiden
   Mappen im Explorer „Immer auf diesem Gerät behalten“ wählen. Vor dem Start müssen beide Mappen den grünen
   Synchronisierungsstatus zeigen. Nur online verfügbare Dateien (Wolkensymbol) werden abgelehnt.
3. **Excel:** Die Mappen dürfen geöffnet sein und bleiben es während des Wettkampfs. In Excel Desktop muss
   **AutoSpeichern** eingeschaltet sein (Schalter oben links). Excel Online speichert immer automatisch.
4. **Prüflauf ohne Netzwerk:** `.\start_live.cmd --check`. Erwartet wird der Event-Slug, beide Programme,
   die Anzahl Publikationszeilen und `Prüfung erfolgreich`. Es wird nichts geschrieben.
5. **Testwerte entfernen:** Alle Probe- und Testeinträge aus den Mappen löschen. Zur Kontrolle `--check` erneut
   ausführen.
6. **Freigabe vorbereiten:** `Public!B5` = `0` (noch keine Rotation) und `Public!B6` = WAHR erst, wenn die
   Startlisten öffentlich sein dürfen. Mit `B6` = FALSCH wird nichts veröffentlicht.
7. **Internet und Supabase:** Die öffentliche Seite im Browser öffnen. Sie muss die Übersicht ohne Fehlermeldung
   zeigen.
8. **Öffentliche Seite:** Den Testtag unter „Demnächst“ oder „Live“ öffnen (`?t=<slug>`). Er darf keine Testdaten
   zeigen.
9. **Streamlit wecken:** Am Morgen `https://testtag.streamlit.app/` einmal öffnen, damit die App für das Archiv und
   einen allfälligen Rückfall wach ist.

**Verteilter Link (QR-Code, Mitteilungen):** `https://stv-sev.github.io/testtag-public/?t=<slug>` oder die Übersicht
`https://stv-sev.github.io/testtag-public/`. Der Streamlit-Link `https://testtag.streamlit.app/?t=<slug>` bleibt
gültig und führt mit einem Klick dorthin.

## Wettkampf starten

```powershell
.\start_live.cmd aargau-lenzburg-2026-10-09
```

- Der Slug muss exakt `Public!B2` beider Mappen entsprechen, sonst wird nichts publiziert.
- Nach wenigen Sekunden erscheint `Publikation bestätigt` oder `Abgleich bestätigt`.
- In `.env` sollte `TESTTAG_POLL_SECONDS=1` stehen. Mit `5` dauert jede Publikation rund 5–10 s länger.
- Das Fenster offen lassen. Der Laptop darf nicht in den Ruhezustand gehen.
- Ein zweiter Start für dieselben Mappen wird abgewiesen (`läuft bereits`).

## Während des Wettkampfs

| Meldung | Bedeutung |
|---|---|
| `Änderung erkannt – Datei noch in Bearbeitung` | Excel speichert oder OneDrive synchronisiert noch. Es wird gewartet. |
| `Datei stabil … gelesen wird …` | Die geänderte Mappe wird gelesen. Eine unveränderte Mappe wird nicht erneut gelesen. |
| `Publikation bestätigt: n Zeilen geändert …` | Öffentlich ist der neue Stand, geprüft durch Rücklesen. |
| `Abgleich bestätigt … keine Schreibanfrage` | Der öffentliche Stand ist schon aktuell. |
| `Inhalt unverändert (nur Speicherzeitpunkt)` | Gespeichert ohne Inhaltsänderung. Es wird nichts gesendet. |
| `Netzwerk-/Datenbankfehler … Wiederholung geplant in n s` | Der alte öffentliche Stand bleibt. Neuer Versuch folgt automatisch. |
| `Dateifehler … Wiederholung geplant` | Die Datei wird gerade gespeichert oder synchronisiert, oder sie fehlt. Der Versuch wird automatisch wiederholt. |
| `Validierung fehlgeschlagen (CODE)` | Ein Eingabefehler in Excel. Der alte Stand bleibt, siehe Fehlerfall. |
| `Archivstatus: Testtag archiviert` | Der Testtag ist abgeschlossen. Es wird nichts mehr publiziert. |

- **Excel offen lassen:** Die Mappen nicht zwischen den Eingaben schliessen. Der Publisher wartet, bis die Datei
  stabil ist, und publiziert dann automatisch.
- **Excel Online:** Eingaben erscheinen über Co-Authoring in der auf dem Publisher-Laptop geöffneten Mappe. Der
  Publisher liest diesen geöffneten Stand auch ohne neuen Speicherstand der Datei. Dafür muss die Mappe auf dem
  Publisher-Laptop in Excel Desktop geöffnet bleiben (Berechnung automatisch). Meldung im Protokoll:
  `Excel-Stand geändert … über eine Kopie gelesen`. Gemessen mit einer ungespeicherten Desktop-Eingabe: rund 7 s
  bis zur App; die Dauer über Excel Online selbst ist noch nicht gemessen.
- **Excel Desktop: nach einer Eingabe Ctrl+S drücken.** AutoSpeichern schreibt die Datei sonst erst nach bis zu
  rund 1,5 Minuten. Mit Ctrl+S ist sie sofort gespeichert und das Resultat nach rund 7 s öffentlich sichtbar.
  AutoSpeichern bleibt eingeschaltet.
- **OneDrive-Status:** Grün bedeutet synchron. „Upload ausstehend“ bei einer in Excel Desktop geöffneten Mappe ist
  normal, Excel lädt selbst hoch.
- **Netzwerkunterbruch:** Nichts tun. Nach der Wiederverbindung wird der letzte Stand automatisch nachpubliziert.
- **Rotation freigeben:** In beiden Mappen `Public!B5` auf die abgeschlossene Rotation setzen und speichern. Die
  Resultate dieser Rotation erscheinen dann in Live zuoberst.
- **Neustart:** Mit Ctrl+C beenden, dann erneut `.\start_live.cmd <slug>`. Der Neustart gleicht den Stand ab und
  schreibt nichts doppelt.
- **Protokoll:** `out\live_runner.log`, rotierend. Es enthält nur Zeitpunkte, Zustände und Anzahlen, keine Namen,
  Resultate oder Schlüssel.

## Wettkampf beenden

1. Den letzten Excel-Stand speichern und auf `Publikation bestätigt` oder `Abgleich bestätigt` warten.
2. Den Dauerpublisher mit **Ctrl+C** beenden. Auf die Frage „Batchvorgang abbrechen (J/N)?“ ist J und N
   gleichermassen richtig: Der Publisher hat bereits sauber beendet und die Sperre entfernt.
3. Archivieren (unveränderlich, nur einmal möglich):

   ```powershell
   $env:PYTHONPATH = "src"
   python -B -m testtag_public.publisher --archive --confirm-archive --confirm-slug aargau-lenzburg-2026-10-09
   ```

   Erwartet wird `archiviert – Snapshot neu eingefügt und geprüft`. Eine Wiederholung meldet `bereits archiviert`
   und schreibt nichts.
4. Die öffentliche Seite prüfen. Der Testtag steht unter „Archiv“, alle Bereiche sind vorhanden, und es gibt keine
   automatische Aktualisierung.
5. **Lokale Sicherung:** Beide Arbeitsmappen bleiben die verbindliche Quelle. OneDrive-Synchronisation abschliessen
   lassen und eine Kopie gemäss Vereinsablage sichern.

## Fehlerfall

| Situation | Vorgehen |
|---|---|
| Datei kurz gesperrt | Nichts tun, es wird automatisch wiederholt. Excel muss dafür nicht geschlossen werden. |
| `Dateifehler` hält über mehrere Minuten an | OneDrive-Status und AutoSpeichern prüfen. Excel bleibt offen. Mit der technischen Verantwortung klären. |
| OneDrive noch nicht synchron | Auf das grüne Häkchen warten. „Immer auf diesem Gerät behalten“ wählen. |
| OneDrive pausiert oder abgemeldet | Synchronisierung fortsetzen oder anmelden. Bis dahin bleibt der letzte gültige Stand öffentlich, danach wird automatisch nachgezogen. |
| Internet unterbrochen | Nichts tun. Der alte Stand bleibt öffentlich, nach der Wiederverbindung wird automatisch nachpubliziert. |
| Supabase nicht erreichbar | Wie Internet. Hält es an, den Status von Supabase prüfen. Den Publisher nicht wiederholt neu starten. |
| `läuft bereits (PID …)` | Ein Publisher läuft schon, das andere Fenster suchen. Keinen zweiten starten. |
| Veraltete Sperrdatei nach Absturz | Wird beim Start automatisch erkannt und ersetzt (`Sperre veraltete Sperre ersetzt`). |
| `Validierung fehlgeschlagen (CODE)` | Den Code nachschlagen (README, P1B) und die Eingabe in Excel korrigieren. Nach dem Speichern wird automatisch neu versucht. |
| `ARCHIV_ABWEICHUNG` | Nichts überschreiben. Der bestehende Snapshot weicht ab. Mit der technischen Verantwortung klären. |
| Falsche Daten öffentlich | In beiden Mappen `Public!B6` = FALSCH und speichern. Alle Inhalte werden gesperrt (Tombstones) und der Testtag wird verborgen. |

## Öffentliche Seite

| Ansicht | Wo | Adresse |
|---|---|---|
| Übersicht (Live, Demnächst, Archiv) | statische Seite | `https://stv-sev.github.io/testtag-public/` |
| Live | statische Seite | `…/?t=<slug>` (`&p=…&ak=…&g=<Gerät>`) |
| Beamer (neueste Noten, dunkel, ohne Bedienung) | statische Seite | `…/?t=<slug>&beamer=1` (`&p=…&ak=…&g=<Gerät>`); im Browser F11 |
| Gerät | statische Seite | `…/?t=<slug>&ansicht=geraet&p=…&g=<Gerät>&r=<Rotation>` |
| Ranglisten | statische Seite | `…/?t=<slug>&ansicht=rangliste` (`&p=…&ak=…&art=technik\|athletik`) |
| Startliste | statische Seite | `…/?t=<slug>&ansicht=startliste` (`&p=…`) |
| Zeitplan | statische Seite | `…/?t=<slug>&ansicht=zeitplan` |
| Archiv | Streamlit | `https://testtag.streamlit.app/?t=<slug>` eines archivierten Testtags |

Für laufende und bevorstehende Testtage lädt Streamlit keine Daten: Streamlit-Links zeigen nur eine Pille auf die
statische Seite, „← Alle Testtage“ führt auf die statische Übersicht. Die statische Seite liegt auf GitHub Pages
(`main`, Ordner `/docs`); ein Push wirkt nach dem Pages-Build (≈ 1 min). Nach einem Push, der Python-Dateien ändert,
die Streamlit-App neu starten (Streamlit Cloud → Manage app → Reboot app), sonst bleibt `quelle.py` alt.

### Schalter

| Ort | Schalter | Wirkung |
|---|---|---|
| `docs/config.js` | `ansichten: ["rangliste", "startliste", "zeitplan"]` | Ansichten auf der statischen Seite zusätzlich zu Live; „Gerät“ kommt mit den Ranglisten. Fehlt eine, führen Pille und Links `&ansicht=…` ohne Datenabruf nach `streamlitUrl/?t=<slug>&ansicht=…` |
| `docs/config.js` | `rueckfall: false` / `true` | `true`: jeder Aufruf geht vor dem ersten Datenabruf nach Streamlit (`?t=<slug>&ansicht=…`; ohne `?t=` auf die Streamlit-Übersicht) |
| `docs/config.js` | `streamlitUrl: "https://testtag.streamlit.app"` | Ziel für Archiv, fehlende Ansichten und Rückfall – nicht ändern |
| Streamlit-Secrets | `live_url = "https://stv-sev.github.io/testtag-public/"` | Live und „← Alle Testtage“ auf der statischen Seite; ohne: alles in Streamlit. Gilt nur als `https://`-Adresse ohne Abfrage |
| Streamlit-Secrets | `statische_bereiche = ["rangliste", "startliste", "zeitplan"]` | diese Bereiche in Streamlit nur als Link auf die statische Seite (nur zusammen mit `live_url`); TOML-Liste, auch teilweise |

**Grundregel:** Eine Ansicht ist nur dann statisch, wenn sie in `ansichten` **und** in `statische_bereiche` steht.
Steht sie nur in `statische_bereiche`, verweisen sich beide Seiten gegenseitig – Zuschauer pendeln. Darum:
**Rückfall: zuerst Secrets, dann `config.js`. Aufheben: zuerst `config.js`, dann Secrets.**
Live hat keinen eigenen Schalter; zurück nach Streamlit kommt Live nur mit dem vollständigen Rückfall.

### Rückfall bei einem Problem

**Eine Ansicht** (Ranglisten, Startliste oder Zeitplan; die übrigen bleiben statisch):
1. Streamlit-Secrets: den Bereich aus `statische_bereiche` entfernen, z. B.
   `statische_bereiche = ["rangliste", "zeitplan"]` → die Startliste ist wieder in Streamlit.
2. `docs/config.js` auf `main`: denselben Bereich aus `ansichten` entfernen, committen, pushen.

Beim Rückfall der Ranglisten fällt auch „Gerät“ weg; Streamlit zeigt dann die Technik-Rangliste mit einem Hinweis.

**Alles** (auch Live und Übersicht):
1. Streamlit-Secrets: Zeilen `live_url` und `statische_bereiche` löschen → alles wieder in Streamlit.
2. `docs/config.js` auf `main`: `rueckfall: true`, committen, pushen → jeder Aufruf der statischen Seite leitet auf
   Streamlit um.

Die Secrets wirken nach dem Speichern, der Push nach dem Pages-Build (≈ 1 min). Browser können `config.js` bis
10 min zwischenspeichern; offene Seiten laufen weiter, bis sie neu geladen werden.
**Aufheben:** umgekehrt – zuerst `config.js` (Bereich wieder in `ansichten` bzw. `rueckfall: false`), nach dem
Pages-Build die Secrets.

### Supabase wach halten

Die GitHub Action `.github/workflows/supabase-wach.yml` liest täglich einen Testtag, damit das Gratis-Projekt nicht
pausiert. GitHub schaltet sie ab, wenn 60 Tage nichts committet wurde (Mail; unter Actions wieder einschalten).

## Nützliche Befehle

PowerShell im Projektordner; für alle `python`-Befehle zuerst `$env:PYTHONPATH = "src"`. Jeder schreibende Befehl
verlangt den exakten Slug (`--confirm-slug`) und verändert nur diesen Testtag.

| Zweck | Befehl |
|---|---|
| `.env` prüfen (zeigt nur `gesetzt`/`fehlt`) | `python -B -m testtag_public.local_config` |
| Mappen prüfen, ohne Netzwerk | `.\start_live.cmd --check` |
| Probelauf: Publikationsstand anzeigen, nichts schreiben | `python -B -m testtag_public.publisher` |
| Einmal publizieren (statt Dauerbetrieb) | `python -B -m testtag_public.publisher --publish --confirm-slug <slug>` |
| Dauerbetrieb | `.\start_live.cmd <slug>` |
| Archivieren (unveränderlich, einmalig) | `python -B -m testtag_public.publisher --archive --confirm-archive --confirm-slug <slug>` |
| Verbergen (nicht mehr öffentlich, auch archiviert) | `python -B -m testtag_public.publisher --hide --confirm-slug <slug>` |

Ein verborgener, **nicht** archivierter Testtag wird wieder sichtbar, sobald er mit `Public!B6` = WAHR publiziert
wird. Ein verborgener archivierter Testtag lässt sich nur in Supabase wieder einblenden (unten).

**Öffentlich sichtbare Testtage** (ohne Anmeldung, mit dem öffentlichen Schlüssel aus `docs/config.js`):

```powershell
$k = "sb_publishable_j9MGb4EU6J_afcu0vhAMkg_JybzHBKN"
Invoke-RestMethod "https://lxljapebdcutlaaidtry.supabase.co/rest/v1/testtage?select=slug,status,stand_am&apikey=$k"
```

**Supabase → SQL Editor** (sieht alle Testtage, auch verborgene; nur `testtage` von Hand ändern, nie
`veroeffentlichung` oder `archiv_snapshots`):

```sql
-- alle Testtage
select slug, titel, status, ist_oeffentlich, stand_am from public.testtage order by stand_am desc;

-- kommenden Testtag ankündigen („Demnächst“); Slug und Titel wie später in Public!B2/B3
insert into public.testtage (slug, titel, status, ist_oeffentlich)
values ('<slug>', '<Titel>', 'vorbereitet', true);

-- verborgenen archivierten Testtag wieder einblenden
update public.testtage set ist_oeffentlich = true where slug = '<slug>' and status = 'archiviert';
```

Solange ein Testtag „Demnächst“ zeigen soll, den Publisher nicht mit lauter `B6` = FALSCH laufen lassen: Er
verbirgt ihn dann.
