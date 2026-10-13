"""Auswertung eines Lasttests der statischen Live-Ansicht (gleiche Kennzahlen wie der Streamlit-Lasttest).

    python -B auswerten.py <zuschauerlog> <publog> [--json <datei>]

Verzögerung = Supabase-Bestätigung der Publikation (Antwort auf PATCH testtage.stand_am) → erste neu gezeichnete
Live-Liste je Sitzung. Ausgefallen = keine Anzeige vor der nächsten Publikation. Ausgewertet werden Publikationen
ab 10 s nach dem Start bis 20 s vor dem Ende (wie im Streamlit-Lasttest).
"""
import json
import statistics
import sys
from collections import Counter, defaultdict


def p95(w):
    w = sorted(w)
    return w[min(len(w) - 1, int(round(0.95 * (len(w) - 1))))]


def main(argv):
    z = [json.loads(zeile) for zeile in open(argv[0], encoding="utf-8")]
    pubs_alle = [json.loads(zeile) for zeile in open(argv[1], encoding="utf-8")]
    start = next(x["t"] for x in z if x["art"] == "start")
    n = next(x["anzahl"] for x in z if x["art"] == "start")
    ende = max(x["t"] for x in z if x["art"] == "ende")
    pubs = [p for p in pubs_alle if p.get("supabase") and start + 10 <= p["supabase"] <= ende - 20]
    anzeigen = defaultdict(list)
    for x in z:
        if x["art"] == "anzeige" and x["n"] > 0:
            anzeigen[x["sitzung"]].append(x)
    verz, je_sitzung, fehlend, falsch = [], defaultdict(list), 0, 0
    for i, p in enumerate(pubs):
        t = p["supabase"]
        naechste = pubs[i + 1]["supabase"] if i + 1 < len(pubs) else ende
        for s in range(n):
            treffer = [a for a in anzeigen[s] if t - 0.5 <= a["ts"] < naechste]
            if not treffer:
                fehlend += 1
                continue
            a = treffer[0]
            verz.append(a["ts"] - t)
            je_sitzung[s].append(a["ts"] - t)
            if a["erster"].split("|")[1] != str(p["wert"]):  # S des neuesten Resultats = publizierter Wert
                falsch += 1
    reqs = [x for x in z if x["art"] == "req"]
    bytes_total = sum(r["antwort"] for r in reqs)
    nach_teil = defaultdict(lambda: [0, 0])
    for r in reqs:
        nach_teil[r["teil"]][0] += 1
        nach_teil[r["teil"]][1] += r["antwort"]
    erst = [x["dauer"] for x in z if x["art"] == "erstanzeige"]
    # stationärer Betrieb: ab Ende der Startphase (letzte Erstanzeige) bis zum Ende
    stat_start = max((x["t"] for x in z if x["art"] == "erstanzeige"), default=start)
    stat = [r for r in reqs if r["t"] >= stat_start]
    stat_dauer = ende - stat_start
    stat_pubs = sum(1 for p in pubs_alle if p.get("supabase") and stat_start <= p["supabase"] <= ende)
    status = Counter(r["status"] for r in reqs)
    res = [x for x in z if x["art"] == "ressourcen"]
    erg = {
        "sitzungen": n, "dauer_s": round(ende - start), "publikationen": len(pubs), "paare": len(pubs) * n,
        "median_s": round(statistics.median(verz), 2) if verz else None,
        "p95_s": round(p95(verz), 2) if verz else None, "max_s": round(max(verz), 2) if verz else None,
        "sitzung_median_min_max": [round(min(statistics.median(v) for v in je_sitzung.values()), 2),
                                   round(max(statistics.median(v) for v in je_sitzung.values()), 2)] if je_sitzung else None,
        "sitzung_p95_min_max": [round(min(p95(v) for v in je_sitzung.values()), 2),
                                round(max(p95(v) for v in je_sitzung.values()), 2)] if je_sitzung else None,
        "ausgefallen": fehlend, "falscher_wert": falsch,
        "req_fehler": sum(1 for x in z if x["art"] == "req_fehler"),
        "seitenfehler": dict(Counter(x["code"] for x in z if x["art"] == "seitenfehler")),
        "js_fehler": sum(1 for x in z if x["art"] == "js_fehler"),
        "abstuerze": sum(1 for x in z if x["art"] == "absturz"),
        "http_status": {str(k): v for k, v in sorted(status.items())},
        "erstanzeige_median_max_s": [round(statistics.median(erst), 2), round(max(erst), 2)] if erst else None,
        "erstanzeige_ok": len(erst),
        "requests": len(reqs), "bytes": bytes_total,
        "nach_teil": {k: {"requests": v[0], "bytes": v[1]} for k, v in sorted(nach_teil.items())},
        "stationaer": {"dauer_s": round(stat_dauer), "publikationen": stat_pubs, "requests": len(stat),
                       "bytes": sum(r["antwort"] for r in stat),
                       "nach_teil": {t: {"requests": sum(1 for r in stat if r["teil"] == t),
                                         "bytes": sum(r["antwort"] for r in stat if r["teil"] == t)}
                                     for t in ("pruefung", "koepfe", "zeilen")}},
        "generator": {"cpu_median": statistics.median(x["cpu_system"] for x in res) if res else None,
                      "cpu_max": max(x["cpu_system"] for x in res) if res else None,
                      "ram_frei_min_mb": min(x["ram_frei_mb"] for x in res) if res else None,
                      "loop_lag_max_s": max(x["loop_lag"] for x in res) if res else None},
    }
    print(json.dumps(erg, ensure_ascii=False, indent=1))
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w", encoding="utf-8") as f:
            json.dump(erg, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
