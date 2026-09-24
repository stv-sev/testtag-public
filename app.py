"""
app.py – öffentliche Testtag-Ansicht (Etappe E15b), eigenständige Streamlit-App

Vollständig getrennt vom internen Kern: Diese App importiert **kein** Modul aus `tool/testtag/`, kennt weder die
lokale Datenbank noch die Kontendatei, hat keine Anmeldung und keinen Schreibweg. Sie liest ausschliesslich die
veröffentlichten Daten über `quelle.py` – mit dem Publishable Key aus den Streamlit-Cloud-Secrets.

Aufruf: eine stabile App-Adresse, der Testtag folgt als Parameter – `…/?t=<öffentliche Testtag-Kennung>`.
Bereich, Programm und AK werden in der App gewählt. Die interne Testtag-ID erscheint nie.

Anzeige (E15-Analyse §9): vier Bereiche auf einer Ebene (Live · Rangliste · Startliste · Zeitplan), kompakte
Zeilen mit aufklappbaren Einzelheiten je Person, Suche über Name, Vorname, Verein und Riege, immer sichtbarer
Veröffentlichungsstand, Zeitpunkt des Datenstands und Verbindungszustand. Die Ergebnisbereiche aktualisieren sich
mit Streamlit-Bordmitteln selbsttätig; bei unverändertem Datenstand wird nichts neu aufgebaut. Bei
Verbindungsverlust bleibt der zuletzt geladene Stand sichtbar und ist als solcher gekennzeichnet.
"""
from __future__ import annotations

import datetime

import streamlit as st

import quelle as q

TAKT = "10s"                       # sparsame periodische Aktualisierung der Ergebnisbereiche
KEIN_TESTTAG = ("Diese Adresse nennt keinen Testtag. Bitte den QR-Code des Testtags verwenden oder die "
                "vollständige Adresse öffnen.")
ROBOTS = '<meta name="robots" content="noindex, nofollow">'
STIL = """
<style>
  .tt-kopf { font-weight: 600; font-size: 1.05rem; }
  .tt-stand { color: #0D4E73; font-size: 0.85rem; }
  .tt-zeile { display: flex; gap: .6rem; align-items: baseline; }
  .tt-rang { min-width: 2.2rem; font-variant-numeric: tabular-nums; font-weight: 600; }
  .tt-total { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 600; }
  .tt-verein { color: #46647a; font-size: .85rem; }
  [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
</style>
"""


def _zustand():
    """Sitzungszustand mit stabilen Schlüsseln – gewählte Ansicht und letzter Stand überstehen jede
    Aktualisierung."""
    for schluessel, wert in (("saetze", None), ("geladen_am", None), ("fehler", None), ("kennung", None)):
        st.session_state.setdefault(schluessel, wert)
    return st.session_state


def _zugang():
    try:
        return q.lade_zugang(st.secrets), None
    except q.QuellenFehler as e:
        return None, str(e)


def _laden(kennung, transport=None):
    """Lädt die Daten neu. Bei einem Fehler bleibt der zuletzt geladene Stand erhalten."""
    zs = _zustand()
    zugang, fehler = _zugang()
    if zugang is None:
        zs.fehler = fehler
        return
    try:
        zs.saetze = q.lade(zugang, kennung, transport=transport)
        zs.geladen_am = datetime.datetime.now(datetime.timezone.utc)
        zs.kennung = kennung
        zs.fehler = None
    except q.QuellenFehler as e:
        zs.fehler = str(e)


def _standzeile(satz, zs):
    """Veröffentlichungsstand, Zeitpunkt des Datenstands und Verbindungszustand – immer sichtbar."""
    teile = []
    if satz is not None:
        teile.append(q.ART_TEXT.get(satz.art, q.BEREICH_TEXT.get(satz.bereich, satz.bereich)))
        hinweis = satz.inhalt.get("hinweis")
        if hinweis and satz.bereich == q.RANGLISTE:
            teile.append(hinweis)
        teile.append(f"Datenstand {satz.stand_am}")
    if zs.fehler:
        teile.append(f"Keine Verbindung – zuletzt geladener Stand{'' if zs.geladen_am is None else ''}")
    elif zs.geladen_am is not None:
        teile.append(f"geladen {zs.geladen_am.astimezone().strftime('%H:%M:%S')}")
    st.markdown(f"<div class='tt-stand'>{' · '.join(t for t in teile if t)}</div>", unsafe_allow_html=True)


def _person(zeile) -> str:
    return f"{zeile.get('vorname', '')} {zeile.get('name', '')}".strip()


def _wert(w) -> str:
    return "" if w is None else ("ja" if w is True else "nein" if w is False else str(w))


def _einzelheiten(zeile):
    """S/T je Linie, Athletik-Einzelpunkte und Teilränge – nur für die aufgeklappte Person."""
    linien = [l for l in (zeile.get("linien") or []) if l.get("t") is not None]
    if linien:
        st.caption("Technik (S / T je Linie)")
        st.table([{"Linie": l.get("code", ""), "S": _wert(l.get("s")), "T": _wert(l.get("t"))} for l in linien])
    athletik = [a for a in (zeile.get("athletik") or []) if a.get("punkte") is not None]
    if athletik:
        st.caption("Athletik (Punkte je Test)")
        st.table([{"Test": a.get("code", ""), "Teilbereich": a.get("teilbereich", ""),
                   "Punkte": _wert(a.get("punkte"))} for a in athletik])
    teil = zeile.get("teilraenge") or {}
    if teil:
        zeilen = [{"Wertung": bezeichnung, "Rang": _wert(teil.get(feld))}
                  for feld, bezeichnung in (("technik", "Technik"), ("athletik", "Athletik"),
                                            ("beweglichkeit", "Beweglichkeit"), ("kraft", "Kraft"),
                                            ("gesamt", "Gesamt")) if teil.get(feld) is not None]
        zeilen += [{"Wertung": g.get("geraet", ""), "Rang": _wert(g.get("rang"))}
                   for g in (teil.get("geraete") or []) if g.get("rang") is not None]
        if zeilen:
            st.caption("Teilränge")
            st.table(zeilen)
    if not linien and not athletik and not teil:
        st.caption("Für diese Person sind noch keine Einzelwerte veröffentlicht.")


def _personenliste(satz, suchtext, schluessel):
    """Kompakte Zeilen: Rang, Name, Verein, Gesamttotal; Einzelheiten je Person aufklappbar."""
    zeilen = q.suche(satz.zeilen, suchtext)
    if not zeilen:
        st.info("Keine Person gefunden." if suchtext else "Für diese Auswahl ist noch nichts veröffentlicht.")
        return
    st.caption(f"{len(zeilen)} von {len(satz.zeilen)} angezeigt" if suchtext else f"{len(zeilen)} Teilnehmende")
    for nr, zeile in enumerate(zeilen):
        rang = _wert(zeile.get("rang"))
        total = _wert(zeile.get("gesamt_total"))
        kopf = f"{rang + '. ' if rang else ''}{_person(zeile)}"
        rechts = f" — {total}" if total else ""
        with st.expander(f"{kopf}{rechts}", expanded=False):
            angaben = [("Verein", zeile.get("verein", "")), ("Jahrgang", _wert(zeile.get("jahrgang"))),
                       ("Riege", zeile.get("riege", "")), ("Technik", _wert(zeile.get("technik_total"))),
                       ("Athletik", _wert(zeile.get("athletik_total"))), ("Gesamt", total)]
            st.markdown(" · ".join(f"**{b}** {w}" for b, w in angaben if w))
            _einzelheiten(zeile)
        if nr >= 299:                                   # sehr grosse Felder: Suche verwenden
            st.caption("Weitere Personen über die Suche einschränken.")
            break


def _startliste(satz, suchtext):
    zeilen = q.suche(satz.zeilen, suchtext)
    if not zeilen:
        st.info("Keine Person gefunden." if suchtext else "Es ist noch keine Startliste veröffentlicht.")
        return
    st.table([{"Start": _wert(z.get("startreihenfolge")), "Riege": z.get("riege", ""),
               "Station": z.get("startstation", ""), "Vorname": z.get("vorname", ""), "Name": z.get("name", ""),
               "Jg": _wert(z.get("jahrgang")), "Verein": z.get("verein", ""), "AK": z.get("ak", "")}
              for z in zeilen])


def _zeitplan(satz):
    zeilen = satz.zeilen
    if not zeilen:
        st.info("Es ist noch kein Zeitplan veröffentlicht.")
        return
    for zeile in sorted(zeilen, key=lambda z: z.get("position", 0)):
        text = zeile.get("bezeichnung", "")
        zeit = zeile.get("zeit") or ""
        if zeile.get("hervorgehoben"):
            st.markdown(f"**{zeit}  {text}**" if zeit else f"**{text}**")
        else:
            st.markdown(f"{zeit}  {text}" if zeit else text)


def _auswahl(saetze, bereich):
    """Programm- und AK-Auswahl mit stabilen Schlüsseln (überstehen die selbsttätige Aktualisierung)."""
    if bereich in (q.STARTLISTE, q.ZEITPLAN):
        return "", ""
    paare = q.programme(saetze, bereich)
    if not paare:
        return "", ""
    spalten = st.columns(2)
    with spalten[0]:
        programm = st.selectbox("Programm", [p for p, _ in paare],
                                format_func=lambda p: dict(paare)[p], key=f"programm|{bereich}")
    aks = q.altersklassen(saetze, bereich, programm)
    with spalten[1]:
        ak = st.selectbox("Altersklasse", aks, key=f"ak|{bereich}") if aks else ""
    return programm, ak or ""


def _bereich_anzeigen(bereich, suchtext):
    zs = _zustand()
    saetze = zs.saetze or ()
    programm, ak = _auswahl(saetze, bereich)
    satz = q.waehle(saetze, bereich, programm, ak)
    _standzeile(satz, zs)
    if satz is None:
        st.info({q.LIVE: "Für dieses Programm sind noch keine Live-Wertungen veröffentlicht.",
                 q.RANGLISTE: "Für dieses Programm ist noch keine Rangliste veröffentlicht.",
                 q.STARTLISTE: "Es ist noch keine Startliste veröffentlicht.",
                 q.ZEITPLAN: "Es ist noch kein Zeitplan veröffentlicht."}[bereich])
        return
    kopf = satz.kopf
    titel = " · ".join(t for t in (kopf.get("testtag"), kopf.get("ort"), kopf.get("datum")) if t)
    st.markdown(f"<div class='tt-kopf'>{titel}</div>", unsafe_allow_html=True)
    if bereich == q.ZEITPLAN:
        _zeitplan(satz)
    elif bereich == q.STARTLISTE:
        _startliste(satz, suchtext)
    else:
        _personenliste(satz, suchtext, f"{bereich}|{programm}|{ak}")


@st.fragment(run_every=TAKT)
def _ergebnisse(bereich, suchtext, kennung, transport=None):
    """Aktualisiert sich selbsttätig; bei unverändertem Datenstand bleibt die Anzeige stehen."""
    zs = _zustand()
    vorher = tuple(d.pruefsumme for d in (zs.saetze or ()))
    _laden(kennung, transport)
    nachher = tuple(d.pruefsumme for d in (zs.saetze or ()))
    if vorher != nachher or zs.fehler:
        pass                                            # nur dann ändert sich überhaupt etwas an der Anzeige
    _bereich_anzeigen(bereich, suchtext)


def zeichne(transport=None):
    """Vollständiger Seitenaufbau (in Tests mit einem synthetischen Transport aufrufbar)."""
    st.set_page_config(page_title="Testtag", page_icon="🤸", layout="centered")
    st.markdown(ROBOTS + STIL, unsafe_allow_html=True)
    zs = _zustand()
    kennung = str(st.query_params.get("t", "") or "")
    st.title("Testtag")
    if not kennung:
        st.info(KEIN_TESTTAG)
        return
    if zs.saetze is None or zs.kennung != kennung:
        _laden(kennung, transport)
    if zs.saetze is None:
        st.warning(zs.fehler or "Die Daten sind zurzeit nicht verfügbar.")
        return
    if zs.fehler:
        st.warning(f"{zs.fehler} Angezeigt wird der zuletzt geladene Stand.")
    bereich = st.radio("Bereich", q.BEREICHE, horizontal=True, key="bereich",
                       format_func=lambda b: q.BEREICH_TEXT[b])
    suchtext = st.text_input("Suche (Name, Vorname, Verein, Riege)", key="suche", placeholder="z. B. Muster")
    if bereich in (q.LIVE, q.RANGLISTE):
        _ergebnisse(bereich, suchtext, kennung, transport)
    else:
        _bereich_anzeigen(bereich, suchtext)
    st.caption("Öffentliche Ansicht – nur lesend. Es können jederzeit noch nicht veröffentlichte Wertungen fehlen.")


if __name__ == "__main__":
    zeichne()
