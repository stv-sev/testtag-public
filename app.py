"""
app.py – öffentliche Testtag-Ansicht (P1D), eigenständige Streamlit-App

Liest ausschliesslich über `quelle.py` mit dem Publishable Key (Streamlit-Secrets `supabase_url`,
`supabase_publishable_key`, optional `zeitueberschreitung`). Kein Schreibweg, keine Anmeldung.

Adressen:
- ohne Parameter: Übersicht aller öffentlichen Testtage (Live, Vorbereitet, Archiv);
- `?t=<slug>`: Testtag über seinen öffentlichen Slug (bevorzugt);
- `?t=<uuid>`: ältere Links mit der Testtag-UUID werden weiterhin aufgelöst und auf den Slug umgestellt.

Ein Testtag zeigt beide Programme (AK5–AK9+, AK9–AK12) in vier Bereichen: Live, Ranglisten, Startliste,
Zeitplan. Nur Testtage mit Status `live` aktualisieren sich etwa alle 10 Sekunden (`st.fragment`). Jeder
Ladevorgang wird vollständig geprüft; bei einem Fehler bleibt der letzte gültige Stand sichtbar. Archivierte
Testtage zeigen ausschliesslich ihren unveränderlichen Snapshot (`archiv_snapshots`, einmal je Sitzung geladen).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import quelle as q

TAKT = "10s"
ALLE = "Alle"
MAX_LIVE = 300
ROBOTS = '<meta name="robots" content="noindex, nofollow">'
STIL = """
<style>
  .tt-stand { font-size: 0.85rem; }
  .tt-karte { padding: .35rem 0; border-bottom: 1px solid #c9dde8; }
  .tt-zahl { font-variant-numeric: tabular-nums; }
</style>
"""
NICHT_VERFUEGBAR = "Die Daten dieses Testtags sind zurzeit nicht verfügbar. Bitte später erneut versuchen."
VERALTET = "Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt"


def takt_fuer(status: str):
    """Automatische Aktualisierung nur für Live-Testtage."""
    return TAKT if status == "live" else None


def _md(text) -> str:
    """Anzeigetext für Markdown entschärfen (Namen dürfen keine Formatierung auslösen)."""
    text = "" if text is None else str(text)
    for zeichen in "\\`*_[]<>|~$":  # Zeilen beginnen nie mit Namen, daher genügen die Inline-Zeichen
        text = text.replace(zeichen, "\\" + zeichen)
    return text


def _zahl(wert) -> str:
    return "–" if wert is None else str(wert)


def _programm(name: str) -> str:
    return q.PROGRAMM_TEXT.get(name, name)


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


# ── Übersicht ────────────────────────────────────────────────────────────────────────────────────────

def uebersicht(zugang) -> None:
    st.title("Testtage")
    try:
        testtage = q.lade_testtage(zugang)
    except q.QuellenFehler:
        st.warning("Die Testtage sind zurzeit nicht verfügbar. Bitte später erneut versuchen.")
        return
    if not testtage:
        st.info("Zurzeit ist kein Testtag veröffentlicht.")
        return
    for status, ueberschrift in (("live", "Live"), ("vorbereitet", "Demnächst"), ("archiviert", "Archiv")):
        gruppe = [t for t in testtage if t.status == status]
        if not gruppe:
            continue
        st.subheader(ueberschrift)
        for testtag in gruppe:
            marke = "● LIVE · " if status == "live" else ""
            st.button(f"{marke}{testtag.titel}", key=f"oeffnen|{testtag.slug}", on_click=_oeffnen,
                      args=(testtag.slug,), width="stretch")
            st.caption(f"Stand {q.zeit_text(testtag.stand_am, mit_datum=True)} · Adresse ?t={testtag.slug}")


# ── Laden mit letztem gültigem Stand ────────────────────────────────────────────────────────────────

def _schluessel(testtag) -> tuple[str, str]:
    return f"stand|{testtag.id}", f"fehler|{testtag.id}"


def _quelle(testtag) -> str:
    return "archiv" if testtag.status == "archiviert" else "live"


def laden(zugang, testtag) -> None:
    """Neuen Stand vollständig laden und prüfen; nur bei Erfolg atomar ersetzen (inkl. Herkunft Live/Archiv)."""
    stand_key, fehler_key = _schluessel(testtag)
    try:
        st.session_state[stand_key] = q.lade_stand(zugang, testtag)
        st.session_state[f"quelle|{testtag.id}"] = _quelle(testtag)
        st.session_state[fehler_key] = None
    except q.QuellenFehler as fehler:
        st.session_state[fehler_key] = fehler.code


def muss_laden(testtag) -> bool:
    """Live: bei jedem Takt. Archiv/vorbereitet: bis einmal ein Stand aus der passenden Quelle gültig geladen ist."""
    return testtag.status == "live" or st.session_state.get(f"quelle|{testtag.id}") != _quelle(testtag)


def _kopf(testtag, stand, fehler) -> None:
    st.title(testtag.titel)
    if testtag.status == "live":
        st.markdown("**● Live** – wird etwa alle 10 Sekunden aktualisiert")
    elif testtag.status == "archiviert":
        st.markdown("**Archiv** – abgeschlossener Testtag, keine Aktualisierung")
    else:
        st.markdown("**Vorbereitet** – noch nicht gestartet")
    zeitpunkte = [d.stand_am for d in stand.saetze]
    veroeffentlicht = max(zeitpunkte, key=q.zeit) if zeitpunkte else testtag.stand_am
    st.markdown(f"<div class='tt-stand'>Datenstand {q.zeit_text(veroeffentlicht, mit_datum=True)} · "
                f"geladen {q.nach_zuerich(stand.geladen_am).strftime('%H:%M:%S')}</div>", unsafe_allow_html=True)
    if fehler:
        st.warning(f"{VERALTET} (Stand {q.nach_zuerich(stand.geladen_am).strftime('%H:%M:%S')}).")


def _auswahl(label: str, optionen: list, key: str, format_func=str):
    if st.session_state.get(key) not in optionen:
        st.session_state.pop(key, None)
    return st.selectbox(label, optionen, key=key, format_func=format_func)


# ── Bereiche ─────────────────────────────────────────────────────────────────────────────────────────

def _live(stand) -> None:
    programme = q.programme(stand, q.LIVE)
    if not programme:
        st.info("Es sind noch keine Live-Resultate veröffentlicht.")
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
        st.info("Für diese Auswahl sind noch keine Resultate veröffentlicht.")
        return
    st.caption(f"{len(resultate)} Resultate – neueste Änderung zuerst")
    for r in resultate[:MAX_LIVE]:
        if r.get("bereich") == "technik":
            wertung = f"{_md(r.get('geraet'))} · {_md(r.get('anzeige'))} · S {_md(_zahl(r.get('S')))} · " \
                      f"T {_md(_zahl(r.get('T')))}"
        else:
            wertung = f"{_md(q.BEREICH_ATHLETIK_TEXT.get(r.get('bereich'), r.get('bereich')))} · " \
                      f"{_md(r.get('test'))} · Punkte {_md(_zahl(r.get('punkte')))}"
        st.markdown(
            f"**{_md(r.get('vorname'))} {_md(r.get('name'))}** · {_md(r.get('verein'))}  \n"
            f"{_md(_programm(r.get('programm')))} · {_md(r.get('ak'))} · {wertung} · "
            f"{q.zeit_text(r.get('geaendert_am'))}")
    if len(resultate) > MAX_LIVE:
        st.caption(f"Die neuesten {MAX_LIVE} Resultate werden angezeigt.")


def _hat_resultate(resultate: list[dict], art: str) -> bool:
    if art == "technik":
        return any(r.get("bereich") == "technik" for r in resultate)
    if art == "athletik":
        return any(r.get("bereich") in ("beweglichkeit", "kraft") for r in resultate)
    return bool(resultate)


def _ranglisten(stand) -> None:
    programme = q.programme(stand, q.RANGLISTE)
    if not programme:
        st.info("Es ist noch keine Rangliste veröffentlicht.")
        return
    spalten = st.columns(2)
    with spalten[0]:
        programm = _auswahl("Programm", programme, "rl_programm", _programm)
    with spalten[1]:
        ak = _auswahl("Altersklasse", q.altersklassen(stand, q.RANGLISTE, programm), "rl_ak")
    art = st.radio("Rangliste", q.RANGLISTEN, horizontal=True, key="rl_art",
                   format_func=lambda a: q.RANGLISTE_TEXT[a])
    hinweis = q.hinweis(stand, q.RANGLISTE, programm, ak)
    if hinweis:
        st.caption(hinweis)
    eintraege = q.rangliste(stand, programm, ak, art)
    if not eintraege:
        st.info("Für diese Auswahl ist noch keine Rangliste veröffentlicht.")
        return
    je_person: dict[str, list] = {}
    for r in q.live_resultate(stand, programm, ak):
        je_person.setdefault(r.get("teilnehmer"), []).append(r)
    zeilen = []
    for e in eintraege:  # Reihenfolge und Rang unverändert vom Publisher
        eigene = je_person.get(e.get("teilnehmer"), [])
        punkte = _zahl(e.get("punkte")) if _hat_resultate(eigene, art) else "–"
        zeilen.append({"Rang": str(e.get("rang", "")), "Name": e.get("name", ""), "Vorname": e.get("vorname", ""),
                       "Verein": e.get("verein", ""), "Punkte": punkte})
    st.dataframe(pd.DataFrame(zeilen, columns=["Rang", "Name", "Vorname", "Verein", "Punkte"]), hide_index=True,
                 width="stretch")
    st.caption("– = noch keine veröffentlichten Resultate. Gleiche Punktzahl ergibt den gleichen Rang.")
    with st.expander("Einzelheiten je Person"):
        for e in eintraege:
            eigene = je_person.get(e.get("teilnehmer"), [])
            st.markdown(f"**{_md(e.get('rang'))}. {_md(e.get('vorname'))} {_md(e.get('name'))}** – "
                        f"Technik {_md(_zahl(e.get('technik')))} · Beweglichkeit {_md(_zahl(e.get('beweglichkeit')))}"
                        f" · Kraft {_md(_zahl(e.get('kraft')))} · Athletik {_md(_zahl(e.get('athletik')))}"
                        f" · Gesamt {_md(_zahl(e.get('gesamt')))}")
            if not eigene:
                st.caption("Noch keine Einzelresultate veröffentlicht.")
                continue
            details = [{"Gerät/Test": r.get("geraet") if r.get("bereich") == "technik"
                        else q.BEREICH_ATHLETIK_TEXT.get(r.get("bereich"), ""),
                        "Linie": r.get("anzeige") if r.get("bereich") == "technik" else r.get("test"),
                        "S": _zahl(r.get("S")) if r.get("bereich") == "technik" else "–",
                        "T": _zahl(r.get("T") if r.get("bereich") == "technik" else r.get("punkte"))}
                       for r in eigene]
            st.dataframe(pd.DataFrame(details, columns=["Gerät/Test", "Linie", "S", "T"]), hide_index=True,
                         width="stretch")


def _startliste(stand) -> None:
    listen = q.startlisten(stand)
    if not listen:
        st.info("Es ist noch keine Startliste veröffentlicht.")
        return
    for programm, gruppen in listen:
        st.subheader(_programm(programm))
        for gruppe in gruppen:
            st.markdown(f"**Startgerät / Riege: {_md(gruppe.get('startgeraet'))}**")
            zeilen = [{"Pos.": str(p.get("position", "")), "Name": p.get("name", ""), "Vorname": p.get("vorname", ""),
                       "Jg.": str(p.get("jahrgang", "")), "AK": p.get("ak", ""), "Verein": p.get("verein", "")}
                      for p in gruppe["teilnehmer"]]
            st.dataframe(pd.DataFrame(zeilen, columns=["Pos.", "Name", "Vorname", "Jg.", "AK", "Verein"]),
                         hide_index=True, width="stretch")


def _zeitplan(stand) -> None:
    bloecke = q.zeitplan(stand)
    if not bloecke:
        st.info("Es ist noch kein Zeitplan veröffentlicht.")
        return
    for block in bloecke:  # Reihenfolge exakt vom Publisher
        st.subheader(_programm(block.get("programm", "")))
        for zeile in block["zeilen"]:
            text = f"**{_md(zeile.get('zeit'))}** · {_md(zeile.get('programmpunkt'))}"
            if zeile.get("hinweis"):
                text += f" – {_md(zeile.get('hinweis'))}"
            st.markdown(text)


ANSICHTEN = {q.LIVE: _live, q.RANGLISTE: _ranglisten, q.STARTLISTE: _startliste, q.ZEITPLAN: _zeitplan}


def testtag_ansicht(zugang, testtag) -> None:
    """Lädt (Live: bei jedem Takt) und zeichnet den Testtag aus dem letzten gültigen Stand."""
    stand_key, fehler_key = _schluessel(testtag)
    if muss_laden(testtag):
        laden(zugang, testtag)
    stand = st.session_state.get(stand_key)
    fehler = st.session_state.get(fehler_key)
    if stand is None:
        st.title(testtag.titel)
        st.error(NICHT_VERFUEGBAR)
        return
    _kopf(testtag, stand, fehler)
    bereich = st.radio("Bereich", q.BEREICHE, horizontal=True, key="bereich",
                       format_func=lambda b: q.BEREICH_TEXT[b])
    ANSICHTEN[bereich](stand)


def zeichne() -> None:
    st.set_page_config(page_title="Testtag", layout="centered")
    st.markdown(ROBOTS + STIL, unsafe_allow_html=True)
    zugang = _zugang()
    if zugang is None:
        return
    kennung = str(st.query_params.get("t", "") or "").strip()
    if not kennung:
        uebersicht(zugang)
        return
    st.button("← Alle Testtage", on_click=_zur_uebersicht, key="zurueck")
    merk = f"testtag|{kennung}"
    try:
        testtag = q.finde_testtag(zugang, kennung)
        st.session_state[merk] = testtag
    except q.QuellenFehler as fehler:
        testtag = None if fehler.code == "KENNUNG" else st.session_state.get(merk)
        if testtag is None:
            st.info("Dieser Testtag ist nicht verfügbar." if fehler.code == "KENNUNG" else NICHT_VERFUEGBAR)
            return
    if testtag is None:
        st.info("Dieser Testtag ist nicht verfügbar.")
        return
    if kennung != testtag.slug:  # alter UUID-Link: künftig immer mit dem Slug
        st.query_params["t"] = testtag.slug
    takt = takt_fuer(testtag.status)
    if takt:
        st.fragment(run_every=takt)(testtag_ansicht)(zugang, testtag)
    else:
        testtag_ansicht(zugang, testtag)
    st.caption("Öffentliche Ansicht – nur lesend. Noch nicht freigegebene Rotationen sind nicht enthalten.")


if __name__ == "__main__":
    zeichne()
