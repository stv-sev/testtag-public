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
→ öffentliche Streamlit-App
```

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

## Wettkampf starten

```powershell
.\start_live.cmd aargau-lenzburg-2026-10-09
```

- Der Slug muss exakt `Public!B2` beider Mappen entsprechen, sonst wird nichts publiziert.
- Nach rund 10 Sekunden erscheint `Publikation bestätigt` oder `Abgleich bestätigt`.
- Das Fenster offen lassen. Der Laptop darf nicht in den Ruhezustand gehen.
- Ein zweiter Start für dieselben Mappen wird abgewiesen (`läuft bereits`).

## Während des Wettkampfs

| Meldung | Bedeutung |
|---|---|
| `Änderung erkannt – Datei noch in Bearbeitung` | Excel speichert oder OneDrive synchronisiert noch. Es wird gewartet. |
| `Datei stabil … gemeinsam gelesen` | Beide Mappen werden jetzt gelesen. |
| `Publikation bestätigt: n Zeilen geändert …` | Öffentlich ist der neue Stand, geprüft durch Rücklesen. |
| `Abgleich bestätigt … keine Schreibanfrage` | Der öffentliche Stand ist schon aktuell. |
| `Inhalt unverändert (nur Speicherzeitpunkt)` | Gespeichert ohne Inhaltsänderung. Es wird nichts gesendet. |
| `Netzwerk-/Datenbankfehler … Wiederholung geplant in n s` | Der alte öffentliche Stand bleibt. Neuer Versuch folgt automatisch. |
| `Dateifehler … Wiederholung geplant` | Die Datei wird gerade gespeichert oder synchronisiert, oder sie fehlt. Der Versuch wird automatisch wiederholt. |
| `Validierung fehlgeschlagen (CODE)` | Ein Eingabefehler in Excel. Der alte Stand bleibt, siehe Fehlerfall. |
| `Archivstatus: Testtag archiviert` | Der Testtag ist abgeschlossen. Es wird nichts mehr publiziert. |

- **Excel offen lassen:** Die Mappen nicht zwischen den Eingaben schliessen. AutoSpeichern speichert nach wenigen
  Sekunden bis rund einer Minute. Der Publisher wartet, bis die Datei stabil ist, und publiziert dann automatisch.
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
