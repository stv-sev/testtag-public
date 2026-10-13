"""Auswertung streamlit_wechsel.py: Wechselzeit, Aktualisierung der Rangliste nach Publikation, Abbrüche, Laufdauer.

    python -B wechsel_auswerten.py <wechsel-log> <publog>
"""
import json
import statistics
import sys


def p95(werte):
    w = sorted(werte)
    return w[min(len(w) - 1, int(round(0.95 * (len(w) - 1))))] if w else None


def runde(x):
    return None if x is None else round(x, 2)


zeilen = [json.loads(z) for z in open(sys.argv[1], encoding="utf-8")]
pubs = [json.loads(z) for z in open(sys.argv[2], encoding="utf-8")]
start = next(z for z in zeilen if z["art"] == "start")
ende = max(z["t"] for z in zeilen)
n = start["anzahl"]
erst = {z["sitzung"]: z for z in zeilen if z["art"] == "erstanzeige"}
wechsel = [z["ab_verbindung"] for z in erst.values()]
seite = [z["seite"] for z in zeilen if z["art"] == "verbunden" and z.get("erste")]
# Publikationen, die nach der Erstanzeige aller Sitzungen und mindestens 30 s vor Messende bestätigt wurden
bereit = max((z["t"] for z in erst.values()), default=ende)
ts = sorted(p["supabase"] for p in pubs if p.get("supabase") and p["code"] == 0)
gewertet = [t for t in ts if t > bereit and t < ende - 30]
aend = {}
for z in zeilen:
    if z["art"] == "aenderung":
        aend.setdefault(z["sitzung"], []).append(z["t"])
verz, verpasst = [], 0
for i, t in enumerate(gewertet):
    naechste = next((u for u in ts if u > t), ende)
    for s in range(n):
        treffer = [a for a in aend.get(s, []) if t < a <= naechste]
        if treffer:
            verz.append(treffer[0] - t)
        else:
            verpasst += 1
laeufe = [z["dauer"] for z in zeilen if z["art"] == "lauf"]
res = [z for z in zeilen if z["art"] == "ressourcen"]
print(json.dumps({
    "sitzungen": n, "dauer_s": round(ende - start["t"]),
    "rangliste_erreicht": f"{len(erst)}/{n}",
    "wechsel_bis_rangliste_s": {"median": runde(statistics.median(wechsel)) if wechsel else None,
                                "p95": runde(p95(wechsel)), "max": runde(max(wechsel, default=None))},
    "seitenaufruf_http_s": {"median": runde(statistics.median(seite)) if seite else None, "max": runde(max(seite, default=None))},
    "publikationen_gewertet": len(gewertet),
    "aktualisierung_s": {"paare": len(verz) + verpasst, "median": runde(statistics.median(verz)) if verz else None,
                         "p95": runde(p95(verz)), "max": runde(max(verz, default=None)), "verpasst": verpasst},
    "laufdauer_s": {"median": runde(statistics.median(laeufe)) if laeufe else None, "p95": runde(p95(laeufe)),
                    "max": runde(max(laeufe, default=None))},
    "getrennt": sum(z["art"] == "getrennt" for z in zeilen),
    "verbindungsfehler": sum(z["art"] == "verbindung_fehler" for z in zeilen),
    "lauf_haengt": sum(z["art"] == "lauf_haengt" for z in zeilen),
    "messrechner": {"cpu_system_max": max((r["cpu_system"] for r in res), default=None),
                    "ram_frei_min_mb": min((r["ram_frei_mb"] for r in res), default=None),
                    "loop_lag_max": max((r["loop_lag"] for r in res), default=None)},
}, ensure_ascii=False, indent=1))
