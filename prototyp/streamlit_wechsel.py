"""Lastmessung Ansichtswechsel: N Zuschauer wechseln gleichzeitig von Live zur Rangliste in Streamlit (ohne Browser).

    python -B streamlit_wechsel.py <ws-url> <slug> <anzahl> <sekunden> <log> [staffel_s=0.05]

Jede Sitzung verbindet sich wie ein Browser, der einen Link aus der statischen Live-Ansicht öffnet
(`?t=<slug>&ansicht=rangliste`): zuerst die Seite per HTTP (Streamlit Cloud setzt dabei über Weiterleitungen das
Sitzungs-Cookie, ohne das der Websocket abgewiesen wird), dann der Websocket mit diesem Cookie. Kennt die App `ansicht` nicht (heutiger Stand von main), wählt die Sitzung nach dem
ersten Lauf „🏅 Ranglisten“ wie ein Klick (Widget-Zustand des Radios `bereich`, im Fragment). Danach führt sie die
angekündigte Fragment-Aktualisierung im Takt aus, stets mit dem gewählten Bereich (wie das Frontend: kein Stapeln).
Protokolliert: Verbindung, erste Rangliste (ab Verbindungsbeginn), jede Änderung der Rangliste (Hash), Laufdauer,
Trennungen, Fehler, Ressourcen des Messrechners. Aus zuschauer2.py (Streamlit-Lasttest 12.10.2026) abgeleitet.
"""
import asyncio
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import websockets
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

URL, SLUG, N, DAUER, LOG = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), Path(sys.argv[5])
STAFFEL = float(sys.argv[6]) if len(sys.argv) > 6 else 0.05
ORIGIN = re.sub(r"^ws", "http", URL.split("/~/")[0].split("/_stcore")[0])
ABFRAGE = f"t={SLUG}&ansicht=rangliste"
ZIEL = "🏅 Ranglisten"
_f = LOG.open("a", encoding="utf-8")


def protokoll(**daten):
    _f.write(json.dumps({"t": time.time(), **daten}, ensure_ascii=False) + "\n")
    _f.flush()


def seite_laden() -> tuple[str, int]:
    """Seite wie ein Browser abrufen (Weiterleitungen, Cookies); liefert Cookie-Kopf und Anzahl Bytes."""
    jar = http.cookiejar.CookieJar()
    oeffner = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with oeffner.open(urllib.request.Request(f"{ORIGIN}/?{ABFRAGE}", headers={"User-Agent": "Mozilla/5.0"}),
                      timeout=60) as antwort:
        n = len(antwort.read())
    host = ORIGIN.split("://", 1)[1]
    return "; ".join(f"{c.name}={c.value}" for c in jar if c.domain.lstrip(".") == host), n


def nachricht(radio_id=None, fragment=None, auto=False) -> bytes:
    msg = BackMsg()
    msg.rerun_script.query_string = ABFRAGE
    if radio_id:
        w = msg.rerun_script.widget_states.widgets.add()
        w.id = radio_id
        w.string_value = ZIEL
    if fragment:
        msg.rerun_script.fragment_id = fragment
    msg.rerun_script.is_auto_rerun = auto
    return msg.SerializeToString()


async def sitzung(nr: int, ende: float):
    await asyncio.sleep(nr * STAFFEL)  # praktisch gleichzeitiger Wechsel aller Zuschauer
    laeufe = abgeschlossen = 0
    erste_verbindung = True
    while time.time() < ende:
        t_verb = time.time()
        try:
            cookie, n = await asyncio.to_thread(seite_laden) if ORIGIN.startswith("https") else ("", 0)
            t_seite = time.time()
            ws = await websockets.connect(URL, subprotocols=["streamlit"], max_size=None, ping_interval=20,
                                          ping_timeout=60, open_timeout=60, origin=ORIGIN,
                                          additional_headers={"Cookie": cookie} if cookie else None)
        except Exception as exc:  # noqa: BLE001
            protokoll(art="verbindung_fehler", sitzung=nr, text=f"{type(exc).__name__} {str(exc)[:80]}")
            await asyncio.sleep(2)
            continue
        protokoll(art="verbunden", sitzung=nr, dauer=round(time.time() - t_verb, 3), seite=round(t_seite - t_verb, 3),
                  bytes=n, erste=erste_verbindung)
        radio_id, gewaehlt, fragment, intervall, naechste = None, False, None, None, None
        letzt, gesendet, laeuft = None, time.time(), True
        try:
            await ws.send(nachricht())
            while time.time() < ende:
                if not laeuft and radio_id and not gewaehlt:  # Klick auf „Ranglisten“
                    await ws.send(nachricht(radio_id, fragment))
                    gewaehlt, gesendet, laeuft = True, time.time(), True
                    protokoll(art="klick", sitzung=nr)
                    continue
                if not laeuft and naechste is not None and time.time() >= naechste:
                    await ws.send(nachricht(radio_id if gewaehlt else None, fragment, auto=True))
                    laeufe += 1
                    gesendet, laeuft = time.time(), True
                    naechste = gesendet + intervall
                    continue
                warte = 1.0 if naechste is None or laeuft else max(0.05, min(1.0, naechste - time.time()))
                try:
                    roh = await asyncio.wait_for(ws.recv(), timeout=warte)
                except asyncio.TimeoutError:
                    if laeuft and time.time() - gesendet > 60:
                        protokoll(art="lauf_haengt", sitzung=nr)
                        laeuft = False
                    continue
                fm = ForwardMsg()
                fm.ParseFromString(roh)
                art = fm.WhichOneof("type")
                if art == "auto_rerun" and fragment is None:
                    fragment, intervall = fm.auto_rerun.fragment_id, float(fm.auto_rerun.interval)
                    naechste = time.time() + intervall
                elif art == "script_finished":
                    if laeuft:
                        abgeschlossen += 1
                        protokoll(art="lauf", sitzung=nr, dauer=round(time.time() - gesendet, 3),
                                  ergebnis=int(fm.script_finished))
                    laeuft = False
                elif art == "delta" and fm.delta.WhichOneof("type") == "new_element":
                    el = fm.delta.new_element
                    typ = el.WhichOneof("type")
                    if typ == "radio" and "bereich" in el.radio.id:
                        radio_id = el.radio.id
                        wert = el.radio.options[el.radio.default] if el.radio.default < len(el.radio.options) else ""
                        if "Ranglisten" in (el.radio.raw_value or wert) and not gewaehlt:
                            gewaehlt = True  # App kennt &ansicht=: kein Klick nötig
                            protokoll(art="adresse_wirkt", sitzung=nr)
                    elif typ == "markdown" and "tt-liste tt-rangliste" in el.markdown.body:
                        h = hashlib.sha256(el.markdown.body.encode()).hexdigest()
                        if h != letzt:
                            protokoll(art="aenderung" if letzt else "erstanzeige", sitzung=nr,
                                      ab_verbindung=round(time.time() - t_verb, 3), n=el.markdown.body.count("tt-rangkarte"))
                            letzt = h
        except websockets.exceptions.ConnectionClosed as exc:
            protokoll(art="getrennt", sitzung=nr, code=getattr(exc.rcvd, "code", None))
            erste_verbindung = False
            await asyncio.sleep(1)
            continue
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    protokoll(art="ende", sitzung=nr, laeufe=laeufe, abgeschlossen=abgeschlossen, intervall=intervall)


def _system():
    """System-CPU-Zeiten (idle, kernel, user) und freier Arbeitsspeicher (Windows-API, ohne psutil)."""
    import ctypes
    from ctypes import wintypes
    i, k, u = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    f = lambda t: (t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7  # noqa: E731

    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    ms = MS()
    ms.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return f(i), f(k), f(u), ms.avail


async def monitor(ende: float):
    """Alle 2 s: eigene CPU (Anteil eines Kerns), System-CPU, freier RAM, Event-Loop-Verzögerung."""
    t0, c0, s0 = time.time(), sum(os.times()[:2]), _system()
    while time.time() < ende:
        soll = time.monotonic() + 2
        await asyncio.sleep(2)
        lag = time.monotonic() - soll
        t1, c1, s1 = time.time(), sum(os.times()[:2]), _system()
        idle, kern, user = (s1[j] - s0[j] for j in range(3))
        protokoll(art="ressourcen", cpu_eigen=round((c1 - c0) / (t1 - t0) * 100, 1),
                  cpu_system=round((1 - idle / max(1e-9, kern + user)) * 100, 1),
                  ram_frei_mb=round(s1[3] / 2**20), loop_lag=round(lag, 3))
        t0, c0, s0 = t1, c1, s1


async def main():
    ende = time.time() + DAUER
    protokoll(art="start", anzahl=N, url=URL, staffel=STAFFEL)
    await asyncio.gather(monitor(ende), *(sitzung(i, ende) for i in range(N)), return_exceptions=True)


asyncio.run(main())
