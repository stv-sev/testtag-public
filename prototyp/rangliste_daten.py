"""Synthetische Stände für den Test der statischen Seite und Vergleich mit der Streamlit-Darstellung.

    python -B rangliste_daten.py erzeugen <projekt> <ziel.json> [<voll-ordner>]
    python -B rangliste_daten.py vergleichen <projekt> <ziel.json> <gezeichnet.json>

`erzeugen` baut mit den Testhilfen (tests/_support) und dem Publikationsmodell mehrere Stände eines synthetischen
Testtags – nur erfundene Personen – und speichert je Stand die Zeilen `veroeffentlichung` (wie publiziert) sowie die
erwartete Darstellung jeder Rangliste (Programm × AK × Ebene), der Startliste (alle Programme und je Programm) und
des Zeitplans als Klartext, erzeugt mit app.rang_karte, quelle.turner_details, app.startliste_tabelle und dem Aufbau
von app._zeitplan – also genau so, wie die Streamlit-App sie zeigt:
  r0            noch keine Rotation freigegeben (AK5–AK9+)
  r2            Rotationsgrenze: nur Rotation 1–2 freigegeben (Athletik noch offen)
  r8            alle Rotationen freigegeben (neue Resultate gegenüber r2)
  korrektur     korrigierte Note (Rang- und Totaländerung) und erstes Resultat einer bisher leeren Person
  ohne_struktur ältere Publikation ohne `struktur` und ohne Einteilung (Gerät) der Startliste, dazu ein Eintrag mit
                widersprüchlichem Total
  gross         optional: vollständig gefüllter Testtag (108 Personen, alle Resultate) aus <voll-ordner>
`vergleichen` prüft die im Browser gezeichneten Ansichten (rangliste_test.mjs) gegen diese Erwartung.
"""
import html
import json
import re
import sys
import tempfile
from pathlib import Path

MODUS, PROJEKT = sys.argv[1], Path(sys.argv[2]).resolve()
sys.path[:0] = [str(PROJEKT), str(PROJEKT / "src"), str(PROJEKT / "tests")]
sys.dont_write_bytecode = True

import app  # noqa: E402
import quelle as q  # noqa: E402
from testtag_public import publication_model as pm  # noqa: E402

TESTTAG_ID = "5e7a0c1d-0000-4000-8000-00000000a11e"
SLUG = "systemtest-rangliste"
FUSS = "– = noch keine Note"


def klartext(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def erwartet(stand, programm, ak, art) -> str:
    """Inhalt wie app._ranglisten unterhalb der Auswahlfelder (Klartext)."""
    eintraege = q.rangliste(stand, programm, ak, art)
    if not eintraege:
        return "Noch keine Rangliste."
    hinweis = q.hinweis(stand, q.RANGLISTE, programm, ak)
    alle = q.live_resultate(stand, programm, ak)
    struktur = q.ranglisten_struktur(stand, programm, ak)
    je_person = {}
    for r in alle:
        je_person.setdefault(r.get("teilnehmer"), []).append(r)
    karten = []
    for e in eintraege:
        eigene = je_person.get(e.get("teilnehmer"), [])
        karten.append(app.rang_karte(e, art, eigene, q.turner_details(e, eigene, struktur, alle)))
    return klartext("".join([f"<div>{app._h(hinweis)}</div>" if hinweis else "", *karten, f"<div>{FUSS}</div>"]))


def erwartet_startliste(stand, programm) -> str:
    """Wie app._startliste; mit gewähltem Programm nur dessen Gruppen (Alle = Streamlit-Ansicht)."""
    listen = [(p, g) for p, g in q.startlisten(stand) if programm in ("Alle", p)]
    if not listen:
        return "Noch keine Startliste."
    teile = []
    for p, gruppen in listen:
        teile.append(f"<div>{app._h(app._programm(p))}</div>")
        teile += [app.startliste_tabelle(g) for g in gruppen]
    return klartext("".join(teile))


def erwartet_zeitplan(stand) -> str:
    """Wie app._zeitplan."""
    bloecke = q.zeitplan(stand)
    if not bloecke:
        return "Noch kein Zeitplan."
    teile = []
    for block in bloecke:
        teile.append(f"<div>{app._h(app._programm(block.get('programm', '')))}</div>")
        for zeile in block["zeilen"]:
            hinweis = f"<small>{app._h(zeile.get('hinweis'))}</small>" if zeile.get("hinweis") else ""
            teile.append(f"<div>{app._h(zeile.get('zeit'))}</div><span>{app._h(zeile.get('programmpunkt'))}{hinweis}</span>")
    return klartext("".join(teile))


def stand_aus(zeilen, zeit):
    tt = q.Testtag(TESTTAG_ID, SLUG, "Systemtest Rangliste – keine Echtdaten", "live", zeit)
    return q.Stand(tt, tuple(q.Datensatz(z["bereich"], z["programm"], z["ak"], z["pruefsumme"], zeit, z["inhalt"])
                             for z in zeilen if z["bereich"] in q.BEREICHE), None)


def erzeugen(ziel: Path, voll: Path | None):
    import _support as s
    from _support import AK5, AK9, make_testtag_workbook, testtag_spec
    from testtag_public.excel_publication import read_program_source

    leute5 = testtag_spec(AK5)["participants"] + [(5, "Ersonnen", "Mia", 2018, "TV Testdorf", "AK 7", 7, "Reck")]
    noten5 = {(1, "Boden", 1): (5, 1.5, 7.5), (1, "Boden", 2): (4, 1, 4), (1, "Pferd", 1): (5, 1, 5),
              (1, "Kraft", 1): (None, 1, 8), (1, "Beweglichkeit", 1): (None, 1, 0), (2, "Pferd", 1): (6, 1, 6),
              (4, "Sprung", 1): (6, 1, 6)}
    noten9 = {(1, "Sprung", 1): (3, 1, 3), (1, "Sprung", 2): (4, 1, 4), (3, "Sprung", 1): (5, 1, 5),
              (3, "Sprung", 3): (6, 1, 6)}
    direkt9 = {(3, "Kraft", 2): 4, (3, "Beweglichkeit", 1): 0}
    tmp = Path(tempfile.mkdtemp())

    def zustand(name, rotation, noten, zeit, vorher):
        a = make_testtag_workbook(tmp / f"{name}-ak5.xlsx", AK5, participants=leute5, scores=noten,
                                  public={"published_rotation_max": rotation})
        b = make_testtag_workbook(tmp / f"{name}-ak9.xlsx", AK9, scores=noten9, direct=direkt9)
        quellen = [read_program_source(p, wait_seconds=0, sleep=lambda _s: None) for p in (a, b)]
        return pm.build_state(quellen, observed_at=zeit, previous=vorher)

    staende, vorher = [], None
    korrigiert = {**noten5, (1, "Boden", 1): (6, 1.5, 9), (5, "Reck", 1): (3, 1, 3)}  # Korrektur + erstes Resultat
    for name, rotation, noten, zeit in (("r0", 0, noten5, "2026-10-09T07:50:00Z"), ("r2", 2, noten5, "2026-10-09T08:00:00Z"),
                                        ("r8", 8, noten5, "2026-10-09T08:10:00Z"),
                                        ("korrektur", 8, korrigiert, "2026-10-09T08:20:00Z")):
        state = zustand(name, rotation, noten, zeit, vorher)
        vorher = state
        staende.append((name, zeit, [dict(r) for r in pm.publication_rows(state)]))
    # ältere Publikation ohne struktur; ein Eintrag mit widersprüchlichem Total (Aufschlüsselung entfällt)
    alt = json.loads(json.dumps(staende[-1][2]))
    for z in alt:
        if z["bereich"] == q.STARTLISTE:
            z["inhalt"].pop("einteilung", None)
        if z["bereich"] == q.RANGLISTE:
            z["inhalt"].pop("struktur", None)
            if z["programm"] == AK5 and z["ak"] == "AK 7":
                for g in z["inhalt"]["gruppen"]:
                    for e in g["eintraege"]:
                        if e["vorname"] == "Lea":
                            e["technik"] = 99
        z["pruefsumme"] = pm.content_checksum(z["inhalt"])
    staende.append(("ohne_struktur", "2026-10-09T08:30:00Z", alt))
    if voll:
        quellen = [read_program_source(voll / n, wait_seconds=0, sleep=lambda _s: None) for n in ("ak5.xlsx", "ak9.xlsx")]
        state = pm.build_state(quellen, observed_at="2026-10-09T08:40:00Z", previous=vorher)
        staende.append(("gross", "2026-10-09T08:40:00Z", [dict(r) for r in pm.publication_rows(state)]))

    out = {"testtag": {"id": TESTTAG_ID, "slug": SLUG, "titel": "Systemtest Rangliste – keine Echtdaten"}, "staende": []}
    for name, zeit, zeilen in staende:
        for z in zeilen:
            assert pm.content_checksum(z["inhalt"]) == z["pruefsumme"]
        stand = stand_aus(zeilen, zeit)
        listen = {}
        for programm in q.programme(stand, q.RANGLISTE):
            for ak in q.altersklassen(stand, q.RANGLISTE, programm):
                for art in q.RANGLISTEN:
                    listen[f"{programm}|{ak}|{art}"] = erwartet(stand, programm, ak, art)
        ansichten = {"zeitplan": erwartet_zeitplan(stand)}
        for programm in ["Alle", *q.programme(stand, q.STARTLISTE)]:
            ansichten[f"startliste|{programm}"] = erwartet_startliste(stand, programm)
        out["staende"].append({"name": name, "stand_am": zeit, "listen": listen, "ansichten": ansichten,
                               "zeilen": [{"oeffentliche_testtag_id": TESTTAG_ID, "bereich": z["bereich"],
                                           "programm": z["programm"], "ak": z["ak"], "pruefsumme": z["pruefsumme"],
                                           "stand_am": zeit, "inhalt": z["inhalt"]} for z in zeilen]})
    ziel.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(", ".join(f"{s['name']}: {len(s['zeilen'])} Zeilen, {len(s['listen'])} Ranglisten, {len(s['ansichten'])} weitere"
                    for s in out["staende"]))


def vergleichen(ziel: Path, gezeichnet: Path):
    soll = {s["name"]: {**s["listen"], **{f"ansicht:{k}": v for k, v in s.get("ansichten", {}).items()}}
            for s in json.loads(ziel.read_text(encoding="utf-8"))["staende"]}
    ist = json.loads(gezeichnet.read_text(encoding="utf-8"))
    fehler, geprueft = [], 0
    for name, listen in soll.items():
        for schluessel, text in listen.items():
            roh = ist.get(name, {}).get(schluessel)
            geprueft += 1
            if roh is None:
                fehler.append(f"{name} {schluessel}: nicht gezeichnet")
            elif klartext(roh) != text:
                a, b = klartext(roh), text
                i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                fehler.append(f"{name} {schluessel}: abweichend ab …{b[max(0, i - 40):i + 40]!r} / …{a[max(0, i - 40):i + 40]!r}")
    for f in fehler[:20]:
        print("FEHL", f)
    print(f"{geprueft - len(fehler)} von {geprueft} Ansichten identisch mit der Streamlit-Darstellung "
          f"({sum(not k.startswith('ansicht:') for v in soll.values() for k in v)} Ranglisten, übrige Startliste/Zeitplan)")
    sys.exit(1 if fehler else 0)


if MODUS == "erzeugen":
    erzeugen(Path(sys.argv[3]), Path(sys.argv[4]) if len(sys.argv) > 4 else None)
else:
    vergleichen(Path(sys.argv[3]), Path(sys.argv[4]))
