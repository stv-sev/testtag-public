# Umstellung Live auf die statische Seite – Vorbereitung (Branch `umstellung-live-statisch`)

Stand 13.10.2026. Nichts ist produktiv umgeschaltet; `main`, die beworbene Adresse `https://testtag.streamlit.app/`,
Publisher, Excel, Supabase-Schema, RLS und produktive Daten sind unverändert.

## Navigation

| Von | Nach | Weg |
|---|---|---|
| Statische Live-Seite | Ranglisten / Startliste / Zeitplan desselben Testtags | `https://testtag.streamlit.app/?t=<slug>&ansicht=rangliste` (`startliste`, `zeitplan`) |
| Statische Live-Seite | Übersicht | „← Alle Testtage“ → `https://testtag.streamlit.app/` |
| Streamlit-Testtag | Live | Pille „⚡ Live“ in der Bereichszeile → `<live_url>?t=<slug>` (gleicher Tab) |
| Streamlit-Übersicht | Live | Pille „⚡ Live-Resultate“ je Testtag (nicht im Archiv) |
| Bestehender Link `?t=<slug>` | – | öffnet weiterhin den Testtag; mit `live_url` zeigt er statt Live den Hinweis „Live-Resultate öffnen“ (lädt keine Daten) |

- Neu in `app.py`: Adressparameter `ansicht` (wählt den Bereich, ein Wechsel schreibt ihn zurück) und das optionale
  Secret `live_url`. **Ohne `live_url` verhält sich die App wie bisher** (Live bleibt in Streamlit) – das ist der Rückfall.
  Archivierte Testtage bleiben immer vollständig in Streamlit (die statische Seite verweist dorthin).
- Programm und AK werden **nicht** übergeben: Die Streamlit-Adresse kennt keine Filter, Ranglisten haben kein „Alle“.
  Die statische Seite behält ihre eigenen `&p=…&ak=…`.
- Gleiches Erscheinungsbild: gleiche Pillen (Grösse, Abstände, Farben, mobil 0,8 rem in einer Zeile), Farben der
  Auswahlfelder und Schrift wie das Streamlit-Theme.
- Unbekannt/verborgen: beide Seiten zeigen „Dieser Testtag ist nicht verfügbar.“ ohne Navigation und ohne Daten;
  die statische Seite verwirft bereits geladene Daten sofort (Funktionstest).
- `docs/config.js` hat den Schalter `rueckfall`: `true` leitet jeden Aufruf vor dem ersten Datenabruf auf
  `https://testtag.streamlit.app/?t=<slug>&ansicht=live` (ohne Testtag: Übersicht) um.

## Engpass beim Ansichtswechsel (gemessen)

Produktive Streamlit-App (Code von `main`, heute ohne `ansicht`: Wechsel wie ein Klick auf „Ranglisten“),
synthetischer Testtag, 50 Sitzungen starten innerhalb von 2,5 s (Seitenaufruf mit Cookie-Weiterleitung, Websocket,
Wechsel), danach 5-s-Takt der App, Publikation alle 30 s, je 6 min (`streamlit_wechsel.py`, `wechsel_auswerten.py`).

| | Lauf A | Lauf C |
|---|---|---|
| Rangliste sichtbar nach Wechsel, Median / 95. P. / Max | 42,1 / 44,2 / 46,3 s | 42,4 / 43,6 / 43,9 s |
| Seitenaufruf (HTTP inkl. Weiterleitungen), Median | 4,0 s | 3,5 s |
| Aktualisierung nach Publikation, Median / 95. P. | 19,4 / 25,2 s | 17,7 / 24,2 s |
| ausgefallene Aktualisierungen | 20 von 400 | 0 von 450 |
| Laufdauer je Takt (Soll < 5 s), Median / Max | 14,1 / 34,1 s | 14,9 / 32,6 s |
| Trennungen | 1 | 0 |

Lauf B ist ungültig: Der Messlaptop stand rund 38 min still (Event-Loop 2 273 s verzögert, CPU 100 %, danach
DNS-Fehler; auch der Publisher stand), daher Lauf C.

Weiterer Befund: Die Streamlit-App **schlief** am 13.10. wegen Inaktivität („Zzzz … get this app back up“); Wecken
per Knopf dauerte 25 s. Ein Zuschauer, der dann von Live zur Rangliste wechselt, sieht diese Seite.

**Entscheidungsgrundlage:** Mit Live statisch fällt die Live-Last weg (gemessen 2,5–2,8 s statt ≈ 20 s). Wechseln
aber viele Zuschauer gleichzeitig zur Rangliste, scheitert Streamlit dort wie bisher bei Live (≈ 42 s bis zur
Anzeige, Aktualisierung ≈ 18 s statt 5 s). Soll die Rangliste bei 50 Zuschauern zuverlässig sein, muss sie als
nächste Ansicht statisch werden (Zeilen `resultate_rangliste` plus Live-Zeilen für die Details, gleiche Prüfung
wie Live). Startliste und Zeitplan laufen in Streamlit ebenfalls im 5-s-Takt und belasten gleich, ändern sich aber
selten und werden kaum von allen gleichzeitig geöffnet. Schlechter als heute wird nichts: Heute liegt jede Ansicht in
Streamlit.

## Tests

- `python -m pytest tests` – 323 Tests (5 neue für `ansicht`/`live_url`).
- `navigation_test.mjs` – lokal (Streamlit dieses Branchs mit `live_url`, statische Kopie mit lokaler
  `streamlitUrl`), 390 × 844 und 1280 × 900: Links in beide Richtungen, Übersicht → Live, alter `?t=`-Link,
  „← Alle Testtage“, unbekannter Testtag, alles im gleichen Tab, keine waagrechte Scrollbar – alle bestanden.
- `rueckfall_test.mjs` – `rueckfall: true` und Streamlit ohne `live_url`: Umleitung ohne Supabase-Abruf, Live in
  Streamlit wie bisher, kein Rücklink – bestanden.
- `funktionstest.mjs` – statische Seite: Anzeige, Filter, offline/Erholung, verborgener Testtag – unverändert bestanden.
- Für lokale Tests lassen beide Seiten `http://localhost` bzw. `127.0.0.1` als Gegenadresse zu, sonst nur https.

## Ablauf bis zum 31. Oktober

**Vor dem Testtag testen** (mit synthetischem Testtag, danach wieder verbergen):
1. Branch nach `main` übernehmen (dein Entscheid) – ohne Secret ändert sich für Zuschauer nur, dass `&ansicht=` wirkt.
2. GitHub → Settings → Pages: Quelle auf `main` / `/docs` umstellen (Adresse bleibt
   `https://stv-sev.github.io/testtag-public/`). `prototyp-live-statisch` danach nicht mehr als Quelle.
3. Streamlit Cloud → App `testtag` → Settings → Secrets: Zeile `live_url = "https://stv-sev.github.io/testtag-public/"`
   ergänzen und speichern.
4. Mit Telefon und Laptop durchklicken: Übersicht → Live, Live → Ranglisten/Startliste/Zeitplan → Live, alter Link
   `https://testtag.streamlit.app/?t=<slug>`; einmal den Rückfall (unten) durchspielen und wieder aufheben.
5. Supabase → Usage: Egress der Messtage prüfen (Zählweise komprimiert/unkomprimiert, siehe README).
6. Entscheiden, ob die Rangliste vorher statisch werden soll (siehe Messung).

**Verteilter Link (QR-Code, Mitteilungen):** `https://stv-sev.github.io/testtag-public/?t=aargau-lenzburg-2026-10-09`
– öffnet direkt Live, braucht keinen Streamlit-Prozess und schläft nicht. Der bisherige Link
`https://testtag.streamlit.app/?t=aargau-lenzburg-2026-10-09` bleibt gültig und führt mit einem Klick zu Live.

**Am Morgen des Testtags:** `https://testtag.streamlit.app/` öffnen, damit die App wach ist (sonst Weckseite beim
ersten Wechsel zur Rangliste); Live-Seite und eine Rangliste kurz prüfen.

## Rückfall bei einem Problem

1. Streamlit Cloud → Secrets: Zeile `live_url` löschen und speichern → Live wird wieder in Streamlit angezeigt,
   die Live-Pille verschwindet.
2. `docs/config.js` auf `main`: `rueckfall: true`, committen und pushen → nach dem Pages-Build (≈ 1 min) leitet
   der verteilte Link auf `https://testtag.streamlit.app/?t=<slug>&ansicht=live` um. Browser können `config.js`
   bis 10 min zwischenspeichern; bereits offene Live-Seiten laufen weiter, bis sie neu geladen werden.
3. Reihenfolge einhalten (erst 1, dann 2), sonst zeigt Streamlit beim Rückfall den Hinweis auf die Live-Seite.
4. Aufheben: umgekehrt (`rueckfall: false`, dann `live_url` wieder eintragen).
