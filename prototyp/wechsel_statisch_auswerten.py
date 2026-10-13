"""Auswertung wechsel_statisch.mjs: Wechselzeit je Ziel, Aktualisierung der Rangliste, Anfragen, Datenmenge.

    python -B wechsel_statisch_auswerten.py <zuschauerlog> <publog>

Wechselzeit = Klick auf „Ranglisten“ (bzw. Beginn des Seitenaufrufs) bis zur gezeichneten Rangliste.
Aktualisierung = Supabase-Bestätigung der Publikation (PATCH testtage.stand_am) bis zur ersten neu gezeichneten
Rangliste, deren neuestes Resultat den publizierten Wert trägt. Ausgefallen = keine solche Anzeige vor der nächsten
Publikation. Gewertet: Publikationen nach der letzten ersten Rangliste bis 20 s vor dem Ende.
Je Ziel (Startliste, Zeitplan): Wechselzeit sowie Anfragen/Bytes vom Wechsel bis 10 s danach; stationär = ab 10 s
nach dem letzten Wechsel bis zum Ende (Ansicht des letzten Ziels).
"""
import json
import statistics
import sys
from collections import Counter, defaultdict


def p95(w):
    w = sorted(w)
    return w[min(len(w) - 1, int(round(0.95 * (len(w) - 1))))] if w else None


def r(x, n=2):
    return None if x is None else round(x, n)


z = [json.loads(zeile) for zeile in open(sys.argv[1], encoding="utf-8")]
pubs_alle = [json.loads(zeile) for zeile in open(sys.argv[2], encoding="utf-8")]
start = next(x for x in z if x["art"] == "start")
n, wechsel = start["anzahl"], start["wechsel"]
ende = max(x["t"] for x in z if x["art"] == "ende")
ziele_log = [x for x in z if x["art"] == "ziel"]
erste = {x["sitzung"]: x for x in z if x["art"] == "rangliste"}
if not erste:  # ohne Rangliste: stationär ab der letzten ersten Anzeige des letzten Ziels
    letzte = [x for x in ziele_log if x["ziel"] == start.get("ziele", ["rangliste"])[-1]]
    erste = {x["sitzung"]: x for x in letzte}
dauer = [x["dauer"] for x in erste.values()]
bereit = max((x["t"] for x in erste.values()), default=ende)
pubs = [p for p in pubs_alle if p.get("supabase") and p["code"] == 0 and bereit < p["supabase"] <= ende - 20]
alle_ts = sorted(p["supabase"] for p in pubs_alle if p.get("supabase") and p["code"] == 0)
anzeigen = defaultdict(list)
for x in z:
    if x["art"] == "anzeige" and x["ansicht"] == "rangliste" and x["n"] > 0:
        anzeigen[x["sitzung"]].append(x)
verz, fehlend, falsch = [], 0, 0
for p in pubs:
    t = p["supabase"]
    naechste = next((u for u in alle_ts if u > t), ende)
    for s in range(n):
        treffer = [a for a in anzeigen[s] if t - 0.5 <= a["ts"] < naechste and a["erster"]]
        passend = [a for a in treffer if a["erster"].split("|")[1] == str(p["wert"])]
        if passend:
            verz.append(passend[0]["ts"] - t)
        else:
            fehlend += 1
            falsch += bool(treffer)
reqs = [x for x in z if x["art"] == "req"]


def zusammen(liste):
    m = defaultdict(lambda: {"requests": 0, "bytes": 0})
    for q in liste:
        m[q["teil"]]["requests"] += 1
        m[q["teil"]]["bytes"] += q["antwort"]
    return {"requests": len(liste), "bytes": sum(q["antwort"] for q in liste), "nach_teil": dict(m)}


im_wechsel = [q for q in reqs if wechsel <= q["t"] <= bereit + 1]
stationaer = [q for q in reqs if q["t"] > bereit + 1]
stat_dauer = ende - (bereit + 1)
stat_pubs = sum(1 for t in alle_ts if bereit + 1 < t <= ende)
ms = [q["ms"] for q in reqs if q["ms"] > 0]
res = [x for x in z if x["art"] == "ressourcen"]
stat = zusammen(stationaer)
je_ziel = {}
for i, ziel in enumerate(start.get("ziele", ["rangliste"])):
    d = [x["dauer"] for x in ziele_log if x["ziel"] == ziel]
    w = start.get("wechsel_alle", [wechsel])[i] if start["modus"] == "klick" else start["t"]
    fenster = [q for q in reqs if w <= q["t"] <= w + 12.5]
    je_ziel[ziel] = {"erreicht": f"{len(d)}/{n}", "median": r(statistics.median(d)) if d else None, "p95": r(p95(d)),
                     "max": r(max(d, default=None)), "anfragen_12s": zusammen(fenster)}
print(json.dumps({"je_ziel": je_ziel,
    "modus": start["modus"], "sitzungen": n, "dauer_s": round(ende - start["t"]),
    "rangliste_erreicht": f"{len(erste)}/{n}",
    "wechsel_bis_rangliste_s": {"median": r(statistics.median(dauer)) if dauer else None, "p95": r(p95(dauer)),
                                "max": r(max(dauer, default=None))},
    "wechsel_anfragen": zusammen(im_wechsel),
    "publikationen_gewertet": len(pubs),
    "aktualisierung_s": {"paare": len(pubs) * n, "median": r(statistics.median(verz)) if verz else None,
                         "p95": r(p95(verz)), "max": r(max(verz, default=None)), "ausgefallen": fehlend,
                         "davon_falscher_wert": falsch},
    "stationaer": {"dauer_s": round(stat_dauer), "publikationen": stat_pubs, **stat,
                   "je_zuschauer_und_stunde": {"requests": round(stat["requests"] / n / stat_dauer * 3600),
                                               "mb": round(stat["bytes"] / n / stat_dauer * 3600 / 1e6, 2)}},
    "gesamt": zusammen(reqs),
    "http_status": dict(Counter(str(q["status"]) for q in reqs)),
    "supabase_ms": {"median": statistics.median(ms) if ms else None, "p95": p95(ms)},
    "req_fehler": sum(x["art"] == "req_fehler" for x in z),
    "seitenfehler": dict(Counter(x["code"] for x in z if x["art"] == "seitenfehler")),
    "js_fehler": sum(x["art"] == "js_fehler" for x in z),
    "ziel_fehler": sum(x["art"] in ("rangliste_fehler", "ziel_fehler") for x in z),
    "messrechner": {"cpu_median": statistics.median(x["cpu_system"] for x in res) if res else None,
                    "cpu_max": max((x["cpu_system"] for x in res), default=None),
                    "ram_frei_min_mb": min((x["ram_frei_mb"] for x in res), default=None),
                    "loop_lag_max_s": max((x["loop_lag"] for x in res), default=None)},
}, ensure_ascii=False, indent=1))
