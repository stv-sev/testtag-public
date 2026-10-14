"""
app.py – öffentliche Testtag-Ansicht (P1D), eigenständige Streamlit-App

Liest ausschliesslich über `quelle.py` mit dem Publishable Key (Streamlit-Secrets `supabase_url`,
`supabase_publishable_key`, optional `zeitueberschreitung`). Kein Schreibweg, keine Anmeldung.

Adressen:
- ohne Parameter: Übersicht aller öffentlichen Testtage (Live, Vorbereitet, Archiv);
- `?t=<slug>`: Testtag über seinen öffentlichen Slug (bevorzugt);
- `?t=<uuid>`: ältere Links mit der Testtag-UUID werden weiterhin aufgelöst und auf den Slug umgestellt;
- `&ansicht=live|rangliste|startliste|zeitplan`: öffnet diesen Bereich (Links aus der statischen Live-Ansicht);
  ein Bereichswechsel schreibt ihn in die Adresse. Filter (Programm, AK) stehen bewusst nicht in der Adresse.
- `&art=gesamt|technik|athletik` (Umleitung der statischen Seite): wählt einmal die Ranglistenebene;
  `&sicht=rotation` bzw. `&ansicht=geraet` ("Gerät" gibt es nur statisch) zeigt einen Hinweis und die
  Technik-Rangliste. Liegen die Ranglisten auf der statischen Seite, führt die Pille "Gerät" dorthin.

Statische Live-Ansicht (optional, Secret `live_url`, z. B. `https://stv-sev.github.io/testtag-public/`): Ist sie
hinterlegt, führt „← Alle Testtage“ auf deren Übersicht und „Live“ bei nicht archivierten Testtagen auf `live_url?t=<slug>`; Streamlit zeigt dann nur
Ranglisten, Startliste und Zeitplan und lädt für den Live-Hinweis keine Daten. Ohne `live_url` bleibt alles wie
bisher (Rückfall: Secret entfernen). Zusätzlich `statische_bereiche = ["rangliste", "startliste", "zeitplan"]` (Liste,
auch teilweise): diese Bereiche führen ebenfalls auf die statische Seite (`live_url?t=<slug>&ansicht=…`) und werden
hier nicht mehr geladen. Fehlt ein Bereich in der Liste, bleibt er hier (Rückfall je Bereich).

Ein Testtag zeigt beide Programme (AK5–AK9+, AK9–AK12) in vier Bereichen: Live, Ranglisten, Startliste,
Zeitplan. Nur Testtage mit Status `live` aktualisieren sich etwa alle 5 Sekunden (`st.fragment`).

Lesezugriffe werden zwischen allen Zuschauern geteilt (`st.cache_data`, sitzungsübergreifend):
- die kleine Abfrage des Testtags (`stand_am`, Status, Sichtbarkeit) für `PRUEF_TTL` Sekunden;
- der vollständig geprüfte Datenstand je Testtag, Status und `stand_am`. Der Publisher setzt `stand_am` erst nach
  allen Zeilen; ein neuer `stand_am` ergibt daher einen neuen Eintrag. Spätestens nach `KONTROLLE_SEKUNDEN` lädt
  ein Zuschauer für alle neu (gemeinsame Kontrollladung, falls Zeilen ohne neuen `stand_am` geändert wurden).
Fehler werden nie zwischengespeichert. Ein verborgener Testtag wird nicht weiter angezeigt, auch nicht aus dem
Cache oder dem Sitzungsstand. Jeder
Ladevorgang wird vollständig geprüft; bei einem Fehler bleibt der letzte gültige Stand sichtbar. Archivierte
Testtage zeigen ausschliesslich ihren unveränderlichen Snapshot (`archiv_snapshots`, einmal je Sitzung geladen).

Darstellung (P1H): visuelle Sprache der öffentlichen AMM-Anzeige von Turnsport Aargau – Kopfband mit Logo,
Karten, Status-Badges, Tabellen mit Titelleiste. Alle Stile stehen zentral in `STIL`; alle angezeigten Texte
laufen durch `_h` (HTML- und Markdown-sicher). Die Darstellung verändert keine Werte und keine Reihenfolge.
"""
from __future__ import annotations

import base64
import html
import time
import urllib.parse
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import streamlit as st

import quelle as q

TAKT = "5s"
PRUEF_TTL = 3  # s: kleine Änderungsabfrage, für alle Zuschauer gemeinsam
KONTROLLE_SEKUNDEN = 300  # s: gemeinsame Kontrollladung eines unveränderten Stands
ALLE = "Alle"
MAX_LIVE = 300
LOGO = Path(__file__).resolve().parent / "assets" / "turnsport_aargau_logo.png"
ROBOTS = '<meta name="robots" content="noindex, nofollow">'
NICHT_VERFUEGBAR = "Die Daten dieses Testtags sind zurzeit nicht verfügbar. Bitte später erneut versuchen."
VERALTET = "Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt"
WARTET = "Die Daten sind zurzeit nicht erreichbar. Die Seite versucht es automatisch erneut."
ANSICHT_PARAM = {q.LIVE: "live", q.RANGLISTE: "rangliste", q.STARTLISTE: "startliste", q.ZEITPLAN: "zeitplan"}
PARAM_ANSICHT = {wert: bereich for bereich, wert in ANSICHT_PARAM.items()}
GERAET = "geraet"  # Ansicht „Gerät“: nur auf der statischen Seite (folgt deren Ranglisten), kein Bereich hier
NAV_TEXT = {q.LIVE: "⚡ Live", GERAET: "🤸 Gerät", q.RANGLISTE: "🏅 Ranglisten", q.STARTLISTE: "📋 Startliste",
            q.ZEITPLAN: "🕐 Zeitplan"}
STATUS_BADGE = {"live": ("live", "LIVE"), "vorbereitet": ("demnaechst", "Demnächst"), "archiviert": ("archiv", "Archiv")}
RANG_FARBE = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}

# ── Zentrale Gestaltung (Farben, Typografie, Abstände wie die AMM-Anzeige) ───────────────────────────────
# Dunkelblau #0D4E73 · Blau #15608A · Mittelblau #2C7FA6 · Hellblau #6FAFC9 · Fläche #D6EAF3 · Magenta #E6007E
STIL = """
<style>
.block-container { max-width: 760px; padding-top: 3.5rem; padding-bottom: 2rem; }
[data-testid="stHeading"] h3 { font-size: 1.05rem; font-weight: 700; color: #15608A; padding: 14px 0 4px; }
.tt-kopf { background: linear-gradient(135deg, #0D4E73, #15608A); border-bottom: 3px solid #E6007E;
           border-radius: 12px; padding: 16px; display: flex; flex-direction: column; align-items: center;
           text-align: center; gap: 10px; margin: 0 0 12px; }
.tt-logo { background: #fff; border-radius: 8px; padding: 8px 16px; line-height: 0; }
.tt-logo img { height: 40px; width: auto; }
.tt-kopf h1 { font-size: 1.25rem; font-weight: 700; color: #fff; margin: 0; padding: 0; line-height: 1.3; }
.tt-kopf .tt-unter { font-size: 0.8rem; color: #6FAFC9; margin-top: 4px; }
.tt-badge { display: inline-flex; align-items: center; gap: 6px; border-radius: 20px; padding: 3px 10px;
            font-size: 0.75rem; font-weight: 700; white-space: nowrap; }
.tt-badge.live { background: #FFE0F0; color: #E6007E; }
.tt-badge.demnaechst { background: #D6EAF3; color: #15608A; }
.tt-badge.archiv { background: #EEF1F4; color: #4A5A66; }
.tt-punkt { width: 8px; height: 8px; border-radius: 50%; background: #E6007E; animation: tt-puls 1.2s infinite; }
@keyframes tt-puls { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .4; transform: scale(1.3); } }
.tt-stand { text-align: center; font-size: 0.72rem; color: #888; margin: 2px 0 8px; }
.tt-gruppe { font-size: 0.8rem; font-weight: 700; color: #15608A; text-transform: uppercase; letter-spacing: .04em;
             margin: 18px 0 6px; }
.tt-meta { font-size: 0.72rem; color: #888; margin: -4px 0 12px 4px; }
.tt-anzahl { font-size: 0.75rem; color: #888; margin: 4px 0 8px; }
.tt-karte { background: #fff; border-radius: 12px; margin-bottom: 10px; box-shadow: 0 1px 8px rgba(0,0,0,.07);
            padding: 10px 14px; }
.tt-karte-kopf { display: flex; align-items: center; gap: 12px; }
.tt-nr, .tt-rang { border-radius: 50%; display: flex; align-items: center; justify-content: center;
                   font-weight: 700; flex-shrink: 0; }
.tt-nr { width: 26px; height: 26px; background: #D6EAF3; color: #2C7FA6; font-size: 0.72rem; }
.tt-rang { width: 30px; height: 30px; background: #D6EAF3; color: #0D4E73; font-size: 0.82rem; }
.tt-text { flex: 1; min-width: 0; }
.tt-text strong { font-size: 0.9rem; color: #0D4E73; display: block; overflow-wrap: anywhere; }
.tt-text small { font-size: 0.72rem; color: #888; display: block; overflow-wrap: anywhere; }
.tt-wert { text-align: right; flex-shrink: 0; }
.tt-wert .tt-geraet { font-size: 0.7rem; color: #888; display: block; }
.tt-wert .tt-note { font-size: 1.05rem; font-weight: 700; color: #0D4E73; }
.tt-wert .tt-zeit { font-size: 0.65rem; color: #aaa; display: block; }
.tt-chips { display: flex; gap: 5px; flex-wrap: wrap; padding: 6px 0 0 38px; }
.tt-chip { background: #D6EAF3; color: #2C7FA6; border-radius: 4px; padding: 2px 6px; font-size: 0.72rem;
           font-weight: 500; }
.tt-chip.tt-end { background: #0D4E73; color: #fff; font-weight: 700; }
.tt-karte details > summary { list-style: none; cursor: pointer; }
.tt-karte details > summary::-webkit-details-marker { display: none; }
.tt-pfeil { font-style: normal; color: #6FAFC9; font-size: 0.8rem; flex-shrink: 0; transition: transform .15s; }
.tt-karte details[open] > summary .tt-pfeil { transform: rotate(180deg); }
.tt-details { margin: 8px 0 2px 42px; font-size: 0.8rem; color: #15608A; }
.tt-dtitel { font-size: 0.7rem; font-weight: 700; color: #2C7FA6; text-transform: uppercase; letter-spacing: .04em;
             margin: 8px 0 2px; }
.tt-dz, .tt-geraet > summary, .tt-linie { display: flex; align-items: baseline; gap: 8px; padding: 4px 0;
                                         border-bottom: 1px solid #f2f2f2; }
.tt-dz span, .tt-geraet > summary span, .tt-linie > span:first-child { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.tt-dz b, .tt-geraet > summary b { color: #0D4E73; flex-shrink: 0; }
.tt-dz.tt-total { font-weight: 600; }
.tt-geraet.tt-total > summary { font-weight: 600; }
.tt-stufe { padding: 0 0 4px 12px; }
.tt-dz.tt-haupt { background: #0D4E73; color: #fff; border-radius: 4px; padding: 4px 8px; margin-top: 4px; }
.tt-dz.tt-haupt b { color: #fff; }
.tt-geraet > summary i { font-style: normal; color: #6FAFC9; font-size: 0.75rem; width: 1em; text-align: center;
                         flex-shrink: 0; }
.tt-geraet > summary i::before { content: "▸"; }
.tt-geraet[open] > summary i::before { content: "▾"; }
.tt-linien { padding: 0 0 4px 10px; background: #F6FAFC; }
.tt-linie { font-size: 0.76rem; color: #4A6A80; }
.tt-st { flex-shrink: 0; min-width: 3.6em; text-align: right; }
.tt-linie b.tt-st { color: #0D4E73; }
.tt-dhinweis { color: #888; font-size: 0.76rem; padding: 4px 0; }
.tt-programm { font-size: 1.05rem; font-weight: 700; color: #0D4E73; margin: 14px 0 8px; }
.tt-tabelle { border: 1px solid #D6EAF3; border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
.tt-titel { background: #0D4E73; color: #fff; padding: 8px 14px; font-weight: 700; font-size: 0.92rem; }
.tt-tabelle table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin: 0; table-layout: auto; }
.tt-tabelle th { background: #D6EAF3; color: #0D4E73; padding: 5px 8px; text-align: left; font-weight: 600;
                 border: none; }
.tt-tabelle td { padding: 5px 8px; border: none; border-bottom: 1px solid #f0f0f0; color: #666;
                 overflow-wrap: anywhere; vertical-align: top; }
.tt-tabelle td.tt-pos { color: #15608A; font-weight: 600; }
.tt-tabelle table.tt-startliste { table-layout: fixed; }
.tt-startliste th:nth-child(1) { width: 3em; }
.tt-startliste th:nth-child(3) { width: 3.6em; }
.tt-startliste th:nth-child(4) { width: 4.4em; }
.tt-tabelle td.tt-turner { color: #0D4E73; font-weight: 500; }
.tt-zeile { display: flex; gap: 12px; padding: 7px 14px; border-bottom: 1px solid #f0f0f0; color: #15608A; }
.tt-zeile:last-child { border-bottom: none; }
.tt-uhr { font-weight: 700; color: #0D4E73; flex: 0 0 7.2em; }
.tt-zeile small { display: block; color: #888; font-size: 0.72rem; }
.tt-leer { text-align: center; padding: 28px 12px; color: #9aa7b0; font-size: 0.88rem; }
.tt-fuss { text-align: center; font-size: 0.7rem; color: #aaa; margin-top: 18px; padding-bottom: 12px; }
/* Navigation (Bereich, Ranglistenart): Pillen statt Optionskreise, grosse Touch-Ziele */
.st-key-bereich [role="radiogroup"], .st-key-rl_art [role="radiogroup"] { gap: 6px; flex-wrap: wrap; }
.st-key-bereich [role="radiogroup"] > label, .st-key-rl_art [role="radiogroup"] > label {
    background: #D6EAF3; color: #0D4E73; border-radius: 20px; padding: 7px 14px; margin: 0; min-height: 40px;
    align-items: center; }
.st-key-bereich [role="radiogroup"] > label > div:first-child,
.st-key-rl_art [role="radiogroup"] > label > div:first-child { display: none; }
.st-key-bereich [role="radiogroup"] > label:has(input:checked),
.st-key-rl_art [role="radiogroup"] > label:has(input:checked) { background: #0D4E73; color: #fff; }
.st-key-bereich [role="radiogroup"] > label:has(input:checked) p,
.st-key-rl_art [role="radiogroup"] > label:has(input:checked) p { color: #fff; font-weight: 700; }
/* Übersicht: Testtag-Knöpfe als Karten */
[class*="st-key-oeffnen-"] button { background: #fff; border: none; border-radius: 12px; min-height: 52px;
    box-shadow: 0 1px 8px rgba(0,0,0,.07); justify-content: flex-start; text-align: left; padding: 10px 16px; }
[class*="st-key-oeffnen-"] button > div { justify-content: flex-start; width: 100%; }
[class*="st-key-oeffnen-"] button p { color: #0D4E73; font-weight: 700; font-size: 0.95rem; text-align: left; }
[class*="st-key-oeffnen-"] button:hover { box-shadow: 0 2px 12px rgba(0,0,0,.12); border: none; }
.st-key-zurueck button { border-radius: 20px; min-height: 40px; }
a.tt-pille.tt-alle { background: #fff; border: 1px solid rgba(13,78,115,.2); padding: 0 14px; font-size: 0.875rem; }
/* Statische Live-Ansicht: Link als Pille in derselben Zeile wie die Bereiche */
.st-key-nav { gap: 6px; flex-wrap: wrap; align-items: center; }
.st-key-nav > div { width: auto !important; flex: 0 0 auto; }
.st-key-nav > .st-key-bereich, .st-key-nav .stRadio, .st-key-nav [role="radiogroup"] { display: contents; }
.st-key-bereich [role="radiogroup"] > label p { font-size: 0.9rem !important; }
.st-key-bereich [role="radiogroup"] > label > div:last-child { padding-left: 0; }
a.tt-pille { display: inline-flex; align-items: center; min-height: 40px; padding: 7px 14px; border-radius: 20px;
             background: #D6EAF3; color: #0D4E73 !important; text-decoration: none !important; font-size: 0.9rem; }
a.tt-pille:hover { box-shadow: 0 1px 6px rgba(0,0,0,.12); }
.tt-meta a.tt-pille { min-height: 32px; padding: 3px 12px; margin-left: 6px; font-size: 0.8rem; }
.tt-verweis { text-align: center; padding: 22px 12px; color: #15608A; font-size: 0.9rem; }
.tt-verweis a.tt-pille { background: #0D4E73; color: #fff !important; font-weight: 700; margin-top: 10px; }
@media (max-width: 640px) {
  .tt-chips { padding-left: 0; }
  .tt-details { margin-left: 0; }
  .tt-tabelle th, .tt-tabelle td { padding: 5px 6px; }
  .st-key-bereich [role="radiogroup"] > label { padding: 7px 9px; }
  .st-key-bereich [role="radiogroup"] { gap: 4px; }
  .st-key-nav { gap: 4px; }
  .st-key-nav a.tt-pille { padding: 7px 9px; font-size: 0.8rem; }
  .st-key-bereich [role="radiogroup"] > label p { font-size: 0.8rem !important; }
}
</style>
"""


# ── Hilfen (nur Darstellung) ─────────────────────────────────────────────────────────────────────────────

def takt_fuer(status: str):
    """Automatische Aktualisierung nur für Live-Testtage."""
    return TAKT if status == "live" else None


_MD_ZEICHEN = {ord(z): f"&#{ord(z)};" for z in "\\`*_[]|~$"}


def _h(text) -> str:
    """Anzeigetext HTML- und Markdown-sicher (Namen dürfen weder HTML noch Formatierung auslösen)."""
    text = "" if text is None else str(text)
    return html.escape(text, quote=True).translate(_MD_ZEICHEN)


def _zahl(wert) -> str:
    return "–" if wert is None else str(wert)


def _programm(name: str) -> str:
    return q.PROGRAMM_TEXT.get(name, name)


def _html(teile) -> None:
    """Ein zusammenhängender HTML-Block ohne Leerzeilen/Einrückung (sonst greift Markdown)."""
    st.markdown("".join(teile), unsafe_allow_html=True)


@lru_cache(maxsize=1)
def _logo_html() -> str:
    try:
        daten = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"<div class='tt-logo'><img src='data:image/png;base64,{daten}' alt='Turnsport Aargau'></div>"


def _badge(status: str, text: str | None = None) -> str:
    klasse, standard = STATUS_BADGE.get(status, ("demnaechst", status))
    punkt = "<span class='tt-punkt'></span>" if klasse == "live" else ""
    return f"<span class='tt-badge {klasse}'>{punkt}{_h(text or standard)}</span>"


def kopfband(titel: str, unter: str = "", badge: str = "") -> None:
    teile = ["<div class='tt-kopf'>", _logo_html(), f"<div><h1>{_h(titel)}</h1>"]
    if unter:
        teile.append(f"<div class='tt-unter'>{_h(unter)}</div>")
    teile.append("</div>")
    if badge:
        teile.append(f"<div>{badge}</div>")
    teile.append("</div>")
    _html(teile)


def leer(text: str) -> None:
    _html([f"<div class='tt-leer'>{_h(text)}</div>"])


def fuss() -> None:
    _html(["<div class='tt-fuss'>Öffentliche Ansicht – nur lesend. Noch nicht freigegebene Rotationen sind nicht "
           "enthalten.<br>Turnsport Aargau · Testtag</div>"])


def _warten(versuch) -> None:
    """Seitenaufruf während eines Ausfalls: im Takt erneut versuchen (gebremst über alle Zuschauer, `quelle.AUSFALL`)
    und nach der Wiederherstellung die ganze Seite neu aufbauen – ohne Neuladen durch den Zuschauer."""
    try:
        versuch()
    except q.QuellenFehler:
        st.info(WARTET)
        return
    st.rerun(scope="app")


def _zugang():
    try:
        return q.lade_zugang(st.secrets)
    except q.QuellenFehler as fehler:
        st.info(str(fehler))
    except Exception:  # z. B. keine Secrets vorhanden – nie technische Details zeigen
        st.info(str(q.NichtEingerichtet()))
    return None


def _oeffnen(slug: str) -> None:
    st.query_params["t"] = slug


def _zur_uebersicht() -> None:
    st.query_params.clear()


def _live_basis() -> str | None:
    """Adresse der statischen Live-Ansicht (Secret `live_url`, https ohne Abfrage; http nur für localhost-Tests)
    oder None: Live bleibt hier."""
    try:
        wert = str(st.secrets.get("live_url", "") or "").strip()
    except Exception:  # keine Secrets: wie ohne live_url
        return None
    teile = urllib.parse.urlsplit(wert)
    lokal = teile.scheme == "http" and teile.hostname in ("localhost", "127.0.0.1")
    if (teile.scheme != "https" and not lokal) or not teile.netloc or teile.query or teile.fragment or teile.username:
        return None
    return wert if wert.endswith("/") else f"{wert}/"


def live_link(testtag) -> str | None:
    """Link auf die statische Live-Ansicht dieses Testtags; archivierte Testtage bleiben im Streamlit-Archiv."""
    basis = _live_basis()
    if basis is None or testtag.status == "archiviert":
        return None
    return f"{basis}?t={urllib.parse.quote(testtag.slug, safe='')}"


def statische_links(testtag) -> dict[str, str]:
    """Bereiche dieses Testtags auf der statischen Seite → Link (leer: alles in Streamlit)."""
    link = live_link(testtag)
    if link is None:
        return {}
    try:
        liste = st.secrets.get("statische_bereiche", [])
        gewaehlt = {str(b) for b in liste} if isinstance(liste, (list, tuple)) else set()
    except Exception:
        gewaehlt = set()
    links = {q.LIVE: link}
    if ANSICHT_PARAM[q.RANGLISTE] in gewaehlt:  # Reihenfolge wie die statische Seite: Live · Gerät · Ranglisten …
        links[GERAET] = f"{link}&ansicht={GERAET}"
    links.update({b: f"{link}&ansicht={ANSICHT_PARAM[b]}" for b in q.BEREICHE
                  if b != q.LIVE and ANSICHT_PARAM[b] in gewaehlt})
    return links


def _live_pille(link: str, text: str = "⚡ Live") -> str:
    return f"<a class='tt-pille' href='{_h(link)}' target='_self'>{_h(text)}</a>"


def _bereiche(links: dict[str, str]) -> list[str]:
    return [b for b in q.BEREICHE if b not in links]


def _bereich_aus_adresse(bereiche: list[str]) -> None:
    """`&ansicht=` wählt den Bereich; ein unbekannter Wert (oder Live bei statischer Live-Ansicht) wählt keinen."""
    wert = str(st.query_params.get("ansicht", "") or "").strip()
    gewuenscht = q.RANGLISTE if wert == GERAET else PARAM_ANSICHT.get(wert)  # „Gerät“ hier: Technik-Rangliste
    if gewuenscht in bereiche:
        st.session_state["bereich"] = gewuenscht
    elif "ansicht" in st.query_params or st.session_state.get("bereich") not in bereiche:
        st.session_state.pop("bereich", None)


ROTATION_HINWEIS = ("«Gerät» ist in dieser Ansicht nicht verfügbar. Angezeigt wird die Technik-Rangliste; "
                    "unter «Rangliste» lässt sich die Ebene wechseln.")


def _rangliste_aus_adresse(bereiche: list[str]) -> None:
    """`&art=` (Umleitung der statischen Seite) wählt die Ebene einmal; „Gerät“ (`&sicht=rotation` oder
    `&ansicht=geraet`) gibt es hier nicht: Technik mit Hinweis. Die Angaben werden danach aus der Adresse entfernt,
    damit die Ebene frei wählbar bleibt."""
    geraet = str(st.query_params.get("ansicht", "") or "").strip() == GERAET
    if q.RANGLISTE not in bereiche or not (geraet or any(k in st.query_params for k in ("art", "sicht", "g", "r"))):
        return
    art = "technik" if geraet else str(st.query_params.get("art", "") or "").strip()
    rotation = geraet or (art == "technik" and str(st.query_params.get("sicht", "") or "").strip() == "rotation")
    if art in q.RANGLISTEN:
        st.session_state["rl_art"] = art
    st.session_state["rl_rotation"] = rotation
    for k in ("art", "sicht", "g", "r"):
        st.query_params.pop(k, None)
    if geraet:
        st.query_params["ansicht"] = ANSICHT_PARAM[q.RANGLISTE]


def _rotation_vergessen() -> None:
    st.session_state.pop("rl_rotation", None)


def _ansicht_merken() -> None:
    _rotation_vergessen()
    bereich = st.session_state.get("bereich")
    if bereich in ANSICHT_PARAM:
        st.query_params["ansicht"] = ANSICHT_PARAM[bereich]


# ── Übersicht ────────────────────────────────────────────────────────────────────────────────────────

def uebersicht(zugang) -> None:
    kopfband("Testtage", "Turnsport Aargau · Live-Resultate, Ranglisten, Startlisten")
    try:
        testtage = q.lade_testtage(zugang)
    except q.QuellenFehler:
        st.fragment(run_every=TAKT)(_warten)(lambda: q.lade_testtage(zugang))
        return
    if not testtage:
        leer("Zurzeit ist kein Testtag veröffentlicht.")
        return
    for status, ueberschrift in (("live", "Live"), ("vorbereitet", "Demnächst"), ("archiviert", "Archiv")):
        gruppe = [t for t in testtage if t.status == status]
        if not gruppe:
            continue
        st.subheader(ueberschrift, anchor=False)
        for testtag in gruppe:
            st.button(testtag.titel, key=f"oeffnen|{testtag.slug}", on_click=_oeffnen, args=(testtag.slug,),
                      width="stretch")
            link = statische_links(testtag).get(q.LIVE)
            _html([f"<div class='tt-meta'>{_badge(status)} &nbsp;Stand "
                   f"{_h(q.zeit_text(testtag.stand_am, mit_datum=True))} · Adresse ?t={_h(testtag.slug)}"
                   f"{_live_pille(link, '⚡ Live-Resultate') if link else ''}</div>"])


# ── Laden mit letztem gültigem Stand ────────────────────────────────────────────────────────────────

def _schluessel(testtag) -> tuple[str, str]:
    return f"stand|{testtag.id}", f"fehler|{testtag.id}"


def _quelle(testtag) -> str:
    return "archiv" if testtag.status == "archiviert" else "live"


def _fenster(sekunden: int) -> int:
    """Zeitfenster für gemeinsame Cache-Einträge: innerhalb eines Fensters teilen alle Zuschauer einen Abruf."""
    return int(time.time() // sekunden)


@st.cache_data(ttl=2 * PRUEF_TTL, max_entries=100, show_spinner=False)
def _testtag_gemeinsam(basis: str, kennung: str, fenster: int, _zugang) -> "q.Testtag | None":
    """Kleine Abfrage des öffentlichen Testtags, für alle Zuschauer gemeinsam (None: unbekannt oder verborgen)."""
    return q.finde_testtag(_zugang, kennung)


@st.cache_data(ttl=2 * KONTROLLE_SEKUNDEN, max_entries=20, show_spinner=False)
def _stand_gemeinsam(basis: str, testtag_id: str, slug: str, titel: str, status: str, stand_am: str, fenster: int,
                     _zugang):
    """Vollständig geprüfter Datenstand je Testtag, Status und `stand_am`, für alle Zuschauer gemeinsam."""
    return q.lade_stand(_zugang, q.Testtag(testtag_id, slug, titel, status, stand_am))


def finde(zugang, kennung: str):
    return _testtag_gemeinsam(zugang.url, kennung, _fenster(PRUEF_TTL), zugang)


def laden(zugang, testtag) -> None:
    """Stand aus dem gemeinsamen Cache oder neu laden und prüfen; nur bei Erfolg atomar ersetzen."""
    stand_key, fehler_key = _schluessel(testtag)
    fenster = _fenster(KONTROLLE_SEKUNDEN)
    try:
        stand = _stand_gemeinsam(zugang.url, testtag.id, testtag.slug, testtag.titel, testtag.status,
                                 testtag.stand_am, fenster, zugang)
    except q.QuellenFehler as fehler:
        st.session_state[fehler_key] = fehler.code
        return
    st.session_state[stand_key] = stand
    st.session_state[f"quelle|{testtag.id}"] = _quelle(testtag)
    st.session_state[f"standam|{testtag.id}"] = testtag.stand_am
    st.session_state[f"fenster|{testtag.id}"] = fenster
    st.session_state[f"geprueft|{testtag.id}"] = datetime.now(timezone.utc)
    st.session_state[fehler_key] = None


def vergessen(testtag) -> None:
    """Sitzungsstand eines nicht mehr öffentlichen Testtags verwerfen (nie weiter anzeigen)."""
    for key in (*_schluessel(testtag), f"quelle|{testtag.id}", f"standam|{testtag.id}", f"geprueft|{testtag.id}",
                f"fenster|{testtag.id}"):
        st.session_state.pop(key, None)


def aktualisieren(zugang, testtag) -> bool:
    """Stand der Sitzung nachführen. False: Testtag ist nicht (mehr) öffentlich.

    Archiv/vorbereitet: einmal je Sitzung aus dem gemeinsamen Cache, danach eingefroren. Live: je Takt die gemeinsame
    kleine Abfrage; der Datenstand wird nur bei neuem `stand_am`, ohne gültigen Stand, nach einem Fehler oder in einem
    neuen Kontrollfenster geholt – jeweils aus dem gemeinsamen Cache, wenn ihn schon ein Zuschauer geladen hat.
    Schlägt die kleine Abfrage fehl, bleibt der letzte gültige Stand (mit Hinweis)."""
    stand_key, fehler_key = _schluessel(testtag)
    if testtag.status != "live":
        if st.session_state.get(f"quelle|{testtag.id}") != _quelle(testtag):
            laden(zugang, testtag)
        return True
    try:
        aktuell = finde(zugang, testtag.slug)
    except q.QuellenFehler as fehler:
        st.session_state[fehler_key] = fehler.code
        return True
    if aktuell is None or aktuell.id != testtag.id:
        vergessen(testtag)
        return False
    if st.session_state.get(stand_key) is None or st.session_state.get(fehler_key) \
            or st.session_state.get(f"quelle|{testtag.id}") != _quelle(aktuell) \
            or st.session_state.get(f"standam|{testtag.id}") != aktuell.stand_am             or st.session_state.get(f"fenster|{testtag.id}") != _fenster(KONTROLLE_SEKUNDEN):
        laden(zugang, aktuell)  # neuer Stand, Fehler oder gemeinsame Kontrollladung (aus dem Cache, wenn vorhanden)
    else:
        st.session_state[f"geprueft|{testtag.id}"] = datetime.now(timezone.utc)
    return True


def status_text(status: str) -> tuple[str, str]:
    if status == "live":
        return "Live", "wird etwa alle 5 Sekunden aktualisiert"
    if status == "archiviert":
        return "Archiv", "abgeschlossener Testtag, keine Aktualisierung"
    return "Vorbereitet", "noch nicht gestartet"


def _stand_zeile(testtag, stand, fehler) -> None:
    zeitpunkte = [d.stand_am for d in stand.saetze]
    veroeffentlicht = max(zeitpunkte, key=q.zeit) if zeitpunkte else testtag.stand_am
    geprueft = st.session_state.get(f"geprueft|{testtag.id}") or stand.geladen_am
    _html([f"<div class='tt-stand'>Datenstand {_h(q.zeit_text(veroeffentlicht, mit_datum=True))} · geprüft "
           f"{q.nach_zuerich(max(geprueft, stand.geladen_am)).strftime('%H:%M:%S')}</div>"])
    if fehler:
        st.warning(f"{VERALTET} (Stand {q.nach_zuerich(stand.geladen_am).strftime('%H:%M:%S')}).")


def _auswahl(label: str, optionen: list, key: str, format_func=str):
    if st.session_state.get(key) not in optionen:
        st.session_state.pop(key, None)
    return st.selectbox(label, optionen, key=key, format_func=format_func)


# ── Bereiche ─────────────────────────────────────────────────────────────────────────────────────────

def live_karte(nr: int, r: dict) -> str:
    if r.get("bereich") == "technik":
        geraet, wert = r.get("geraet"), _zahl(r.get("T"))
        chips = (f"<span class='tt-chip'>{_h(r.get('anzeige'))}</span>"
                 f"<span class='tt-chip'>S {_h(_zahl(r.get('S')))}</span>"
                 f"<span class='tt-chip tt-end'>T {_h(wert)}</span>")
    else:
        geraet, wert = q.BEREICH_ATHLETIK_TEXT.get(r.get("bereich"), r.get("bereich")), _zahl(r.get("punkte"))
        chips = (f"<span class='tt-chip'>{_h(r.get('test'))}</span>"
                 f"<span class='tt-chip tt-end'>Punkte {_h(wert)}</span>")
    meta = " · ".join(x for x in (_h(r.get("verein")), _h(_programm(r.get("programm"))), _h(r.get("ak"))) if x)
    return (f"<div class='tt-karte'><div class='tt-karte-kopf'><div class='tt-nr'>{nr}</div>"
            f"<div class='tt-text'><strong>{_h(r.get('vorname'))} {_h(r.get('name'))}</strong><small>{meta}</small></div>"
            f"<div class='tt-wert'><span class='tt-geraet'>{_h(geraet)}</span><div class='tt-note'>{_h(wert)}</div>"
            f"<span class='tt-zeit'>{_h(q.zeit_text(r.get('geaendert_am')))}</span></div></div>"
            f"<div class='tt-chips'>{chips}</div></div>")


def _live(stand) -> None:
    programme = q.programme(stand, q.LIVE)
    if not programme:
        leer("Es sind noch keine Live-Resultate veröffentlicht.")
        return
    spalten = st.columns(2)
    with spalten[0]:
        programm = _auswahl("Programm", [ALLE, *programme], "live_programm",
                            lambda p: p if p == ALLE else _programm(p))
    with spalten[1]:
        aks = q.altersklassen(stand, q.LIVE, None if programm == ALLE else programm)
        ak = _auswahl("Altersklasse", [ALLE, *aks], "live_ak")
    resultate = q.live_resultate(stand, None if programm == ALLE else programm, None if ak == ALLE else ak)
    if not resultate:
        leer("Für diese Auswahl sind noch keine Resultate veröffentlicht.")
        return
    teile = [f"<div class='tt-anzahl'>{len(resultate)} Resultate – neueste Änderung zuerst</div>",
             "<div class='tt-liste tt-live'>"]
    teile += [live_karte(nr, r) for nr, r in enumerate(resultate[:MAX_LIVE], start=1)]
    teile.append("</div>")
    if len(resultate) > MAX_LIVE:
        teile.append(f"<div class='tt-anzahl'>Die neuesten {MAX_LIVE} Resultate werden angezeigt.</div>")
    _html(teile)


def _hat_resultate(resultate: list[dict], art: str) -> bool:
    if art == "technik":
        return any(r.get("bereich") == "technik" for r in resultate)
    if art == "athletik":
        return any(r.get("bereich") in ("beweglichkeit", "kraft") for r in resultate)
    return bool(resultate)


def _dz(label: str, wert, klasse: str = "") -> str:
    """Detailzeile: Bezeichnung links, Wert rechts (fehlend „–“, Nullwertung 0)."""
    return (f"<div class='tt-dz{(' ' + klasse) if klasse else ''}'><span>{_h(label)}</span>"
            f"<b>{_h(_zahl(wert))}</b></div>")


def _aufklappbar(label: str, wert, inhalt: str, klasse: str = "tt-geraet") -> str:
    """Zeile mit Wert, per Antippen aufklappbar (zu Beginn zu)."""
    return (f"<details class='{klasse}'><summary><span>{_h(label)}</span><b>{_h(_zahl(wert))}</b><i></i></summary>"
            f"{inhalt}</details>")


def _geraete(d: dict) -> list[str]:
    """Je Gerät das Gerätetotal, aufklappbar zu den Linien mit S und T."""
    teile = []
    for g in d["geraete"]:
        linien = "".join(
            f"<div class='tt-linie'><span>{_h(li['anzeige'])}{' (Alternative)' if li['alternative'] else ''}</span>"
            f"<span class='tt-st'>S {_h(_zahl(li['S']))}</span><b class='tt-st'>T {_h(_zahl(li['T']))}</b></div>"
            for li in g["linien"])
        teile.append(_aufklappbar(g["geraet"], g["total"], f"<div class='tt-linien'>{linien}</div>"))
    return teile


def _details_gesamt(e: dict, d: dict, vorhanden: dict) -> str:
    """Gestuft: Total Technik → Geräte → Linien; Total Athletik → Beweglichkeit/Kraft → Tests; Gesamt."""
    athletik = []
    for bereich in ("beweglichkeit", "kraft"):
        tests = "".join(f"<div class='tt-linie'><span>{_h(t['test'])}</span><b class='tt-st'>{_h(_zahl(t['punkte']))}</b>"
                        f"</div>" for t in d[bereich])
        athletik.append(_aufklappbar(f"Total {q.BEREICH_ATHLETIK_TEXT[bereich]}",
                                     e.get(bereich) if vorhanden[bereich] else None, f"<div class='tt-linien'>{tests}</div>"))
    return "".join([
        _aufklappbar("Total Technik", e.get("technik") if vorhanden["technik"] else None,
                     f"<div class='tt-stufe'>{''.join(_geraete(d))}</div>", "tt-geraet tt-total"),
        _aufklappbar("Total Athletik", e.get("athletik") if vorhanden["athletik"] else None,
                     f"<div class='tt-stufe'>{''.join(athletik)}</div>", "tt-geraet tt-total"),
        _dz("Gesamt", e.get("gesamt") if vorhanden["gesamt"] else None, "tt-total tt-haupt")])


def _details_technik(e: dict, d: dict, vorhanden: dict) -> str:
    return "".join([_dz("Total Technik", e.get("technik") if vorhanden["technik"] else None, "tt-total tt-haupt"),
                    *_geraete(d)])


def _details_athletik(e: dict, d: dict, vorhanden: dict) -> str:
    teile = []
    for bereich in ("beweglichkeit", "kraft"):
        text = q.BEREICH_ATHLETIK_TEXT[bereich]
        teile.append(f"<div class='tt-dtitel'>{text}</div>")
        teile += [_dz(t["test"], t["punkte"]) for t in d[bereich]]
        teile.append(_dz(f"Total {text}", e.get(bereich) if vorhanden[bereich] else None, "tt-total"))
    teile.append(_dz("Total Athletik", e.get("athletik") if vorhanden["athletik"] else None, "tt-total tt-haupt"))
    return "".join(teile)


DETAILS = {"gesamt": _details_gesamt, "technik": _details_technik, "athletik": _details_athletik}


def rang_karte(e: dict, art: str, eigene: list[dict], details: dict | None) -> str:
    """Kompakte Zeile (Rang, Turner, Verein, Total der Ebene); Details der Ebene erst nach Antippen."""
    rang = e.get("rang")
    punkte = _zahl(e.get("punkte")) if _hat_resultate(eigene, art) else "–"
    farbe = RANG_FARBE.get(rang) if isinstance(rang, int) and punkte != "–" else None
    stil = f" style='background:{farbe};color:#0D4E73;'" if farbe else ""
    vorhanden = {"technik": _hat_resultate(eigene, "technik"), "athletik": _hat_resultate(eigene, "athletik"),
                 "beweglichkeit": any(r.get("bereich") == "beweglichkeit" for r in eigene),
                 "kraft": any(r.get("bereich") == "kraft" for r in eigene), "gesamt": bool(eigene)}  # nie scheinbar 0
    if details is None:
        inhalt = "<div class='tt-dhinweis'>Die Aufschlüsselung ist zurzeit nicht verfügbar.</div>"
    else:
        inhalt = DETAILS[art](e, details, vorhanden)
    return (f"<div class='tt-karte tt-rangkarte'><details><summary><div class='tt-karte-kopf'>"
            f"<div class='tt-rang'{stil} title='Rang {_h(rang)}'>{_h(rang)}</div>"
            f"<div class='tt-text'><strong>{_h(e.get('vorname'))} {_h(e.get('name'))}</strong>"
            f"<small>{_h(e.get('verein'))}</small></div>"
            f"<div class='tt-wert'><div class='tt-note'>{_h(punkte)}</div></div><i class='tt-pfeil'>▾</i></div>"
            f"</summary><div class='tt-details'>{inhalt}</div></details></div>")


def _ranglisten(stand) -> None:
    if st.session_state.get("rl_rotation"):
        st.info(ROTATION_HINWEIS)
    programme = q.programme(stand, q.RANGLISTE)
    if not programme:
        leer("Es ist noch keine Rangliste veröffentlicht.")
        return
    spalten = st.columns(2)
    with spalten[0]:
        programm = _auswahl("Programm", programme, "rl_programm", _programm)
    with spalten[1]:
        ak = _auswahl("Altersklasse", q.altersklassen(stand, q.RANGLISTE, programm), "rl_ak")
    art = st.radio("Rangliste", q.RANGLISTEN, horizontal=True, key="rl_art",
                   format_func=lambda a: q.RANGLISTE_TEXT[a], on_change=_rotation_vergessen)
    hinweis = q.hinweis(stand, q.RANGLISTE, programm, ak)
    eintraege = q.rangliste(stand, programm, ak, art)
    if not eintraege:
        leer("Für diese Auswahl ist noch keine Rangliste veröffentlicht.")
        return
    alle = q.live_resultate(stand, programm, ak)
    struktur = q.ranglisten_struktur(stand, programm, ak)
    je_person: dict[str, list] = {}
    for r in alle:
        je_person.setdefault(r.get("teilnehmer"), []).append(r)
    karten = []
    for e in eintraege:  # Reihenfolge und Ränge vom Publisher
        eigene = je_person.get(e.get("teilnehmer"), [])
        karten.append(rang_karte(e, art, eigene, q.turner_details(e, eigene, struktur, alle)))
    teile = [f"<div class='tt-anzahl'>{_h(hinweis)}</div>" if hinweis else "", "<div class='tt-liste tt-rangliste'>"]
    teile += karten
    teile.append("</div><div class='tt-anzahl'>– = noch kein veröffentlichtes Resultat. Gleiche Punktzahl ergibt "
                 "den gleichen Rang. Turner antippen für Details; T = Linienpunkte.</div>")
    _html(teile)


def startliste_tabelle(gruppe: dict) -> str:
    """Höchstens fünf Spalten: Pos., Turner (Vorname Name), Jg., AK, Verein – keine Angabe geht verloren."""
    zeilen = "".join(
        f"<tr><td class='tt-pos'>{_h(p.get('position', ''))}</td>"
        f"<td class='tt-turner'>{_h(p.get('vorname', ''))} {_h(p.get('name', ''))}</td>"
        f"<td>{_h(p.get('jahrgang', ''))}</td><td>{_h(p.get('ak', ''))}</td><td>{_h(p.get('verein', ''))}</td></tr>"
        for p in gruppe["teilnehmer"])
    return (f"<div class='tt-tabelle'><div class='tt-titel'>Startgerät / Riege: {_h(gruppe.get('startgeraet'))}</div>"
            "<table class='tt-startliste'><thead><tr><th>Pos.</th><th>Turner</th><th>Jg.</th><th>AK</th><th>Verein</th></tr></thead>"
            f"<tbody>{zeilen}</tbody></table></div>")


def _startliste(stand) -> None:
    listen = q.startlisten(stand)
    if not listen:
        leer("Es ist noch keine Startliste veröffentlicht.")
        return
    teile = []
    for programm, gruppen in listen:
        teile.append(f"<div class='tt-programm'>{_h(_programm(programm))}</div>")
        teile += [startliste_tabelle(gruppe) for gruppe in gruppen]
    _html(teile)


def _zeitplan(stand) -> None:
    bloecke = q.zeitplan(stand)
    if not bloecke:
        leer("Es ist noch kein Zeitplan veröffentlicht.")
        return
    teile = []
    for block in bloecke:  # Reihenfolge exakt vom Publisher
        teile.append(f"<div class='tt-tabelle tt-zeitplan'><div class='tt-titel'>{_h(_programm(block.get('programm', '')))}</div>")
        for zeile in block["zeilen"]:
            hinweis = f"<small>{_h(zeile.get('hinweis'))}</small>" if zeile.get("hinweis") else ""
            teile.append(f"<div class='tt-zeile'><span class='tt-uhr'>{_h(zeile.get('zeit'))}</span>"
                         f"<span>{_h(zeile.get('programmpunkt'))}{hinweis}</span></div>")
        teile.append("</div>")
    _html(teile)


ANSICHTEN = {q.LIVE: _live, q.RANGLISTE: _ranglisten, q.STARTLISTE: _startliste, q.ZEITPLAN: _zeitplan}


def navigation(bereiche: list[str], links: dict[str, str]):
    """Bereichspillen; Bereiche der statischen Seite sind Links in derselben Zeile (nichts gewählt = statisch)."""
    with st.container(horizontal=True, key="nav"):
        for bereich, link in links.items():
            _html([_live_pille(link, NAV_TEXT[bereich])])
        if not bereiche:  # alle Bereiche auf der statischen Seite
            return None
        return st.radio("Bereich", bereiche, index=None if links else 0, horizontal=True, key="bereich",
                        label_visibility="collapsed", format_func=lambda b: NAV_TEXT[b], on_change=_ansicht_merken)


VERWEIS_TEXT = {q.LIVE: ("Die Live-Resultate werden", "⚡ Live-Resultate öffnen"),
                GERAET: ("Die Riegen je Gerät und Rotation werden", "🤸 Gerät öffnen"),
                q.RANGLISTE: ("Die Ranglisten werden", "🏅 Ranglisten öffnen"),
                q.STARTLISTE: ("Die Startliste wird", "📋 Startliste öffnen"),
                q.ZEITPLAN: ("Der Zeitplan wird", "🕐 Zeitplan öffnen")}


def live_verweis(links: dict[str, str]) -> None:
    """Bereich liegt auf der statischen Seite: kein Datenabruf, keine Personendaten, nur der Weg dorthin."""
    wert = str(st.query_params.get("ansicht", "") or "").strip()
    gewuenscht = GERAET if wert == GERAET else PARAM_ANSICHT.get(wert)
    bereich = gewuenscht if gewuenscht in links else q.LIVE
    text, knopf = VERWEIS_TEXT[bereich]
    link = links[bereich]
    _html([f"<div class='tt-verweis'>{text} auf einer eigenen, schnellen Seite angezeigt.<br>",
           _live_pille(link, knopf), "</div>"])


def testtag_ansicht(zugang, testtag, links: dict[str, str] | None = None) -> None:
    """Lädt (Live: bei jedem Takt) und zeichnet den Testtag aus dem letzten gültigen Stand."""
    stand_key, fehler_key = _schluessel(testtag)
    if not aktualisieren(zugang, testtag):
        st.info("Dieser Testtag ist nicht verfügbar.")
        return
    stand = st.session_state.get(stand_key)
    fehler = st.session_state.get(fehler_key)
    if stand is None:
        st.error(NICHT_VERFUEGBAR)
        return
    _stand_zeile(testtag, stand, fehler)
    links = links or {}
    bereich = navigation(_bereiche(links), links)
    if bereich is None:  # nur mit statischer Seite möglich
        live_verweis(links)
        return
    ANSICHTEN[bereich](stand)


def zeichne() -> None:
    st.set_page_config(page_title="Testtag", page_icon="🏆", layout="centered", initial_sidebar_state="collapsed")
    st.markdown(ROBOTS + STIL, unsafe_allow_html=True)
    zugang = _zugang()
    if zugang is None:
        return
    kennung = str(st.query_params.get("t", "") or "").strip()
    if not kennung:
        uebersicht(zugang)
        fuss()
        return
    basis = _live_basis()
    if basis:  # Übersicht der statischen Seite (auch aus dem Archiv)
        _html([f"<a class='tt-pille tt-alle' href='{_h(basis)}' target='_self'>← Alle Testtage</a>"])
    else:
        st.button("← Alle Testtage", on_click=_zur_uebersicht, key="zurueck")
    merk = f"testtag|{kennung}"
    try:
        testtag = finde(zugang, kennung)
        if testtag is not None:
            st.session_state[merk] = testtag
    except q.QuellenFehler as fehler:
        testtag = None if fehler.code == "KENNUNG" else st.session_state.get(merk)
        if testtag is None:
            if fehler.code == "KENNUNG":
                st.info("Dieser Testtag ist nicht verfügbar.")
            else:  # noch kein Stand in dieser Sitzung: automatisch weiter versuchen
                st.fragment(run_every=TAKT)(_warten)(lambda: finde(zugang, kennung))
            return
    if testtag is None:  # unbekannt oder verborgen: auch einen früheren Sitzungsstand nie mehr verwenden
        frueher = st.session_state.pop(merk, None)
        if frueher is not None:
            vergessen(frueher)
        st.info("Dieser Testtag ist nicht verfügbar.")
        return
    if kennung != testtag.slug:  # alter UUID-Link: künftig immer mit dem Slug
        st.query_params["t"] = testtag.slug
    titel, text = status_text(testtag.status)
    kopfband(testtag.titel, badge=_badge(testtag.status, f"{titel} – {text}"))
    links = statische_links(testtag)
    bereiche = _bereiche(links)
    _bereich_aus_adresse(bereiche)
    _rangliste_aus_adresse(bereiche)
    if links and st.session_state.get("bereich") not in bereiche:  # Bereich auf der statischen Seite: nichts laden
        navigation(bereiche, links)
        live_verweis(links)
        fuss()
        return
    takt = takt_fuer(testtag.status)
    if takt:
        st.fragment(run_every=takt)(testtag_ansicht)(zugang, testtag, links)
    else:
        testtag_ansicht(zugang, testtag, links)
    fuss()


if __name__ == "__main__":
    zeichne()
