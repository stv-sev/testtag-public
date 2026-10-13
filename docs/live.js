/*
 * live.js – statische Live-Ansicht eines Testtags (Prototyp, ohne Abhängigkeiten).
 *
 * Liest wie quelle.py ausschliesslich über die Supabase Data API (PostgREST) mit dem Publishable Key – als
 * Abfrageparameter `apikey`, damit jede Anfrage ein einfacher CORS-GET ohne Vorabanfrage bleibt. Kein
 * Authorization-Kopf, kein Schreibweg, nichts im Browserspeicher.
 *
 * Datenfluss je Takt (Live: 5 s, vorbereitet: 30 s; nur solange die Seite sichtbar ist):
 *   1. Änderungsabfrage `testtage` (id, Status, stand_am) – rund 160 B Nutzlast.
 *      Leer → Testtag unbekannt oder verborgen: alle geladenen Daten sofort verwerfen.
 *   2. Nur bei neuem stand_am/Status, nach einem Fehler oder alle 5 Minuten (Kontrolle): Kopfzeilen der
 *      Live-Zeilen (programm, ak, pruefsumme, stand_am, aktiv, hinweis – ohne Inhalt).
 *   3. Nur die für die gewählte Ansicht (Programm/AK) benötigten Zeilen, deren Prüfsumme sich geändert hat, in
 *      einer Anfrage (`pruefsumme=in.(…)`). Jede Zeile wird geprüft (Schema, Prüfsumme über den kanonischen
 *      Inhalt wie publication_model.content_checksum); erst wenn alles stimmt, wird der Stand ersetzt.
 * Bei einem Fehler bleibt der letzte gültige Stand sichtbar, gekennzeichnet; neue Versuche mit wachsendem Abstand.
 */
"use strict";
(() => {
  const TAKT_LIVE = 5000;
  const TAKT_VORBEREITET = 30000;
  const KONTROLLE = 300000;
  const ZEITLIMIT = 8000;
  const MAX_PAUSE = 30000;
  const VERALTET_NACH = 12000;
  const MAX_LIVE = 300;
  const LIVE = "resultate_live";
  const SCHEMA = "publikation-1";
  const ALLE = "Alle";
  const PROGRAMM_REIHENFOLGE = ["AK5-AK9+", "AK9-AK12"];
  const PROGRAMM_TEXT = { "AK5-AK9+": "AK5–AK9+", "AK9-AK12": "AK9–AK12" };
  const ATHLETIK_TEXT = { beweglichkeit: "Beweglichkeit", kraft: "Kraft" };
  const SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const HEX64 = /^[0-9a-f]{64}$/;
  const STATUS = ["live", "vorbereitet", "archiviert"];
  const VERALTET = "Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt";

  const $ = (id) => document.getElementById(id);
  const el = { titel: $("titel"), badge: $("badge"), stand: $("stand"), warnung: $("warnung"), nav: $("nav"),
    filter: $("filter"), programm: $("programm"), ak: $("ak"), hinweise: $("hinweise"), inhalt: $("inhalt"),
    zurueck: $("zurueck") };

  // ── Hilfen ────────────────────────────────────────────────────────────────────────────────────────
  const h = (t) => String(t ?? "").replace(/[&<>"']/g, (z) => `&#${z.charCodeAt(0)};`);
  const zahl = (w) => (w === null || w === undefined ? "–" : String(w));
  const programmText = (p) => PROGRAMM_TEXT[p] || p;
  const zeitFormat = new Intl.DateTimeFormat("de-CH", { timeZone: "Europe/Zurich", hour: "2-digit",
    minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  const datumFormat = new Intl.DateTimeFormat("de-CH", { timeZone: "Europe/Zurich", day: "2-digit",
    month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const zeitText = (iso, mitDatum) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : (mitDatum ? datumFormat : zeitFormat).format(d).replace(",", "");
  };
  const akRang = (ak) => { const m = /\d+/.exec(ak || ""); return m ? Number(m[0]) : 999; };
  const vergleicheAk = (a, b) => akRang(a) - akRang(b) || (a < b ? -1 : a > b ? 1 : 0);
  const programmRang = (p) => { const i = PROGRAMM_REIHENFOLGE.indexOf(p); return i < 0 ? 99 : i; };
  const vergleicheProgramm = (a, b) => programmRang(a) - programmRang(b) || (a < b ? -1 : a > b ? 1 : 0);
  const schluessel = (programm, ak) => `${programm}\u0000${ak}`;

  class QuellenFehler extends Error {
    constructor(code) { super(code); this.code = code; }
  }

  // ── Zugang (nur Publishable Key) ────────────────────────────────────────────────────────────────────
  function zugang() {
    const c = window.TT_CONFIG || {};
    let url;
    try { url = new URL(String(c.supabaseUrl || "")); } catch { return null; }
    const key = String(c.publishableKey || "");
    if (url.protocol !== "https:" || (url.pathname !== "/" && url.pathname !== "") || url.search || url.username
        || !key.startsWith("sb_publishable_") || key.length <= 15 || /\s/.test(key)) return null;
    return { basis: url.origin, key, streamlit: String(c.streamlitUrl || "").replace(/\/+$/, "") };
  }

  async function hole(z, tabelle, parameter) {
    const url = new URL(`${z.basis}/rest/v1/${tabelle}`);
    for (const [k, v] of parameter) url.searchParams.append(k, v);
    url.searchParams.append("apikey", z.key);
    const abbruch = new AbortController();
    const uhr = setTimeout(() => abbruch.abort(), ZEITLIMIT);
    let antwort;
    try {
      antwort = await fetch(url, { cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { Accept: "application/json" }, signal: abbruch.signal });
      if (!antwort.ok) throw new QuellenFehler(`HTTP_${antwort.status}`);
      const daten = await antwort.json();
      if (!Array.isArray(daten) || !daten.every((d) => d && typeof d === "object")) throw new QuellenFehler("FORMAT");
      return daten;
    } catch (e) {
      if (e instanceof QuellenFehler) throw e;
      throw new QuellenFehler(e && e.name === "AbortError" ? "ZEITUEBERSCHREITUNG" : "NETZ");
    } finally {
      clearTimeout(uhr);
    }
  }

  // ── Prüfsumme wie publication_model.content_checksum (kanonisches JSON ohne geaendert_am) ─────────────
  function pyZahl(x) {
    if (!Number.isFinite(x)) throw new QuellenFehler("FORMAT");
    if (Number.isInteger(x) && Math.abs(x) < 1e16) return String(x);  // Publisher: ganzzahlig immer als int
    const a = Math.abs(x);
    if (a < 1e-4 || a >= 1e16) {  // Python repr wechselt hier in die Exponentialform (1e-05)
      const [m, e] = x.toExponential().split("e");
      const n = Number(e);
      return `${m}e${n < 0 ? "-" : "+"}${String(Math.abs(n)).padStart(2, "0")}`;
    }
    return String(x);
  }

  function kanonisch(w) {
    if (w === null || typeof w === "boolean") return JSON.stringify(w);
    if (typeof w === "number") return pyZahl(w);
    if (typeof w === "string") return JSON.stringify(w);
    if (Array.isArray(w)) return `[${w.map(kanonisch).join(",")}]`;
    const keys = Object.keys(w).filter((k) => k !== "geaendert_am").sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${kanonisch(w[k])}`).join(",")}}`;
  }

  async function sha256(text) {
    const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  // ── Zustand (nur im Speicher) ───────────────────────────────────────────────────────────────────────
  const z = zugang();
  const slug = (new URLSearchParams(location.search).get("t") || "").trim();
  let testtag = null;          // {id, slug, titel, status, stand_am}
  let koepfe = new Map();      // Schlüssel → {programm, ak, pruefsumme, stand_am, hinweis} (nur aktive Zeilen)
  let zeilen = new Map();      // Schlüssel → {pruefsumme, resultate}  geprüfter Inhalt
  let nachholen = true;        // Kopfzeilen beim nächsten Takt neu laden
  let letzteKontrolle = 0;
  let letzterErfolg = 0;       // letzte vollständig erfolgreiche Prüfung (ms)
  let fehlerFolge = 0;
  let timer = null;
  let laeuft = false;
  let angezeigt = "";          // zuletzt gezeichnetes HTML der Liste
  let auswahlOffen = false;    // Auswahl während eines Takts geändert: sofort nachladen
  const auswahl = { programm: ALLE, ak: ALLE };

  function vergessen() {
    testtag = null; koepfe = new Map(); zeilen = new Map(); nachholen = true; angezeigt = "";
    el.inhalt.innerHTML = ""; el.hinweise.innerHTML = ""; el.stand.textContent = "";
    el.filter.hidden = true; el.nav.hidden = true; el.programm.innerHTML = ""; el.ak.innerHTML = "";
  }

  function testtagAusZeile(r) {
    if (!r || !STATUS.includes(r.status) || !SLUG.test(String(r.slug)) || !UUID.test(String(r.id).toLowerCase())
        || Number.isNaN(new Date(r.stand_am).getTime())) throw new QuellenFehler("FORMAT");
    return { id: String(r.id).toLowerCase(), slug: r.slug, titel: String(r.titel || r.slug), status: r.status,
      stand_am: String(r.stand_am) };
  }

  async function findeTesttag() {
    const r = await hole(z, "testtage", [["select", "id,slug,titel,status,stand_am"], ["slug", `eq.${slug}`]]);
    if (r.length > 1) throw new QuellenFehler("TESTTAG_MEHRDEUTIG");
    return r.length ? testtagAusZeile(r[0]) : null;
  }

  async function ladeKoepfe(id) {
    const r = await hole(z, "veroeffentlichung", [
      ["select", "programm,ak,pruefsumme,stand_am,aktiv:inhalt->aktiv,schema:inhalt->>schema_version,"
        + "hinweis:inhalt->>hinweis"],
      ["oeffentliche_testtag_id", `eq.${id}`], ["bereich", `eq.${LIVE}`]]);
    const neu = new Map();
    for (const k of r) {
      if (typeof k.programm !== "string" || typeof k.ak !== "string" || !HEX64.test(String(k.pruefsumme))
          || Number.isNaN(new Date(k.stand_am).getTime())) throw new QuellenFehler("FORMAT");
      const s = schluessel(k.programm, k.ak);
      if (neu.has(s)) throw new QuellenFehler("DOPPELT");
      if (k.schema !== SCHEMA) throw new QuellenFehler("SCHEMA");
      if (k.aktiv === false) continue;  // Tombstone: nie anzeigen
      neu.set(s, { programm: k.programm, ak: k.ak, pruefsumme: k.pruefsumme, stand_am: k.stand_am,
        hinweis: k.hinweis || "" });
    }
    return neu;
  }

  function benoetigt(kp) {
    return [...kp.entries()].filter(([, k]) => (auswahl.programm === ALLE || k.programm === auswahl.programm)
      && (auswahl.ak === ALLE || k.ak === auswahl.ak)).map(([s]) => s);
  }

  // Lädt fehlende/geänderte Zeilen der Auswahl in einer Anfrage; ersetzt erst, wenn alle geprüft sind.
  async function ladeZeilen(id, kp) {
    const fehlend = benoetigt(kp).filter((s) => zeilen.get(s)?.pruefsumme !== kp.get(s).pruefsumme);
    const neu = new Map([...zeilen].filter(([s, zl]) => kp.get(s)?.pruefsumme === zl.pruefsumme));
    if (fehlend.length) {
      const summen = [...new Set(fehlend.map((s) => kp.get(s).pruefsumme))];
      const r = await hole(z, "veroeffentlichung", [["select", "programm,ak,pruefsumme,inhalt"],
        ["oeffentliche_testtag_id", `eq.${id}`], ["bereich", `eq.${LIVE}`], ["pruefsumme", `in.(${summen.join(",")})`]]);
      for (const zl of r) {
        const s = schluessel(zl.programm, zl.ak);
        if (!fehlend.includes(s) || kp.get(s).pruefsumme !== zl.pruefsumme) continue;  // inzwischen geändert
        const inhalt = typeof zl.inhalt === "string" ? JSON.parse(zl.inhalt) : zl.inhalt;
        if (!inhalt || typeof inhalt !== "object" || inhalt.schema_version !== SCHEMA) throw new QuellenFehler("SCHEMA");
        if (await sha256(kanonisch(inhalt)) !== zl.pruefsumme) throw new QuellenFehler("PRUEFSUMME");
        if (!Array.isArray(inhalt.gruppen)) throw new QuellenFehler("FORMAT");
        const resultate = [];
        for (const g of inhalt.gruppen) for (const x of (g && g.resultate) || []) if (x && typeof x === "object") resultate.push(x);
        neu.set(s, { pruefsumme: zl.pruefsumme, resultate });
      }
    }
    const vollstaendig = benoetigt(kp).every((s) => neu.has(s));
    return { neu, vollstaendig };
  }

  // ── Ablauf ────────────────────────────────────────────────────────────────────────────────────────
  function plane(ms) {
    clearTimeout(timer);
    timer = null;
    if (document.visibilityState === "hidden" || ms === null) return;
    timer = setTimeout(takt, ms * (0.9 + Math.random() * 0.2));  // Zuschauer nicht im Gleichschritt
  }

  async function takt() {
    if (laeuft) return;
    laeuft = true;
    clearTimeout(timer);
    let naechster = TAKT_LIVE;
    try {
      const tt = await findeTesttag();
      if (!tt) {  // unbekannt oder verborgen: nichts Früheres weiter zeigen
        vergessen();
        zeigeNichtVerfuegbar();
        letzterErfolg = Date.now(); fehlerFolge = 0;
        naechster = TAKT_VORBEREITET;
        return;
      }
      if (testtag && testtag.id !== tt.id) vergessen();
      const geaendert = !testtag || testtag.stand_am !== tt.stand_am || testtag.status !== tt.status;
      testtag = tt;
      if (tt.status === "archiviert") { zeigeArchiv(); naechster = null; return; }
      if (geaendert || nachholen || Date.now() - letzteKontrolle > KONTROLLE) {
        nachholen = true;
        const kp = await ladeKoepfe(tt.id);
        const { neu, vollstaendig } = await ladeZeilen(tt.id, kp);
        if (!testtag || testtag.id !== tt.id) return;  // inzwischen verworfen
        koepfe = kp; zeilen = neu; letzteKontrolle = Date.now();
        nachholen = !vollstaendig;  // eine Zeile änderte sich während des Ladens: im nächsten Takt nachholen
      }
      letzterErfolg = Date.now(); fehlerFolge = 0;
      naechster = tt.status === "live" ? TAKT_LIVE : TAKT_VORBEREITET;
      zeichne();
    } catch (e) {
      fehlerFolge += 1;
      naechster = Math.min(MAX_PAUSE, TAKT_LIVE * 2 ** (fehlerFolge - 1));
      melde("fehler", e && e.code ? e.code : "UNBEKANNT");
      if (!testtag) zeigeWarten();
    } finally {
      laeuft = false;
      zeigeStatus();
      plane(auswahlOffen && naechster !== null ? 0 : naechster);
      auswahlOffen = false;
    }
  }

  // Filterwechsel: fehlende Zeilen der neuen Auswahl sofort gezielt nachladen.
  async function auswahlGeaendert() {
    auswahl.programm = el.programm.value;
    auswahl.ak = el.ak.value;
    try {
      const u = new URL(location.href);
      for (const [k, v] of [["p", auswahl.programm], ["ak", auswahl.ak]]) {
        if (v === ALLE) u.searchParams.delete(k); else u.searchParams.set(k, v);
      }
      history.replaceState(null, "", u);
    } catch { /* ohne Adresszeile weiter */ }
    zeichne();
    if (!testtag || benoetigt(koepfe).every((s) => zeilen.has(s))) return;
    if (laeuft) { nachholen = true; auswahlOffen = true; return; }  // laufender Takt kannte die Auswahl nicht
    laeuft = true;
    try {
      const id = testtag.id;
      const { neu, vollstaendig } = await ladeZeilen(id, koepfe);
      if (testtag && testtag.id === id) { zeilen = neu; if (!vollstaendig) nachholen = true; }
    } catch (e) {
      nachholen = true;
      melde("fehler", e && e.code ? e.code : "UNBEKANNT");
    } finally {
      laeuft = false;
      zeichne();
    }
  }

  // ── Darstellung ───────────────────────────────────────────────────────────────────────────────────
  function streamlitLink() {
    return z && z.streamlit ? `${z.streamlit}/?t=${encodeURIComponent(slug)}` : "#";
  }

  function badge(status) {
    const [klasse, text] = status === "live" ? ["live", "Live – wird etwa alle 5 Sekunden aktualisiert"]
      : status === "archiviert" ? ["archiv", "Archiv – abgeschlossener Testtag, keine Aktualisierung"]
        : ["demnaechst", "Vorbereitet – noch nicht gestartet"];
    return `<span class="tt-badge ${klasse}">${klasse === "live" ? "<span class='tt-punkt'></span>" : ""}${h(text)}</span>`;
  }

  function optionen(select, werte, wert, text) {
    const html = [ALLE, ...werte].map((w) => `<option value="${h(w)}"${w === wert ? " selected" : ""}>`
      + `${h(w === ALLE ? ALLE : text(w))}</option>`).join("");
    if (select.dataset.html !== html) { select.innerHTML = html; select.dataset.html = html; }
    select.value = wert;
  }

  function karte(nr, r) {
    let geraet, wert, chips;
    if (r.bereich === "technik") {
      geraet = r.geraet; wert = zahl(r.T);
      chips = `<span class="tt-chip">${h(r.anzeige)}</span><span class="tt-chip">S ${h(zahl(r.S))}</span>`
        + `<span class="tt-chip tt-end">T ${h(wert)}</span>`;
    } else {
      geraet = ATHLETIK_TEXT[r.bereich] || r.bereich; wert = zahl(r.punkte);
      chips = `<span class="tt-chip">${h(r.test)}</span><span class="tt-chip tt-end">Punkte ${h(wert)}</span>`;
    }
    const meta = [r.verein, r.programm ? programmText(r.programm) : "", r.ak].filter(Boolean).map(h).join(" · ");
    return `<div class="tt-karte"><div class="tt-karte-kopf"><div class="tt-nr">${nr}</div>`
      + `<div class="tt-text"><strong>${h(r.vorname)} ${h(r.name)}</strong><small>${meta}</small></div>`
      + `<div class="tt-wert"><span class="tt-geraet">${h(geraet)}</span><div class="tt-note">${h(wert)}</div>`
      + `<span class="tt-zeit">${h(zeitText(r.geaendert_am))}</span></div></div>`
      + `<div class="tt-chips">${chips}</div></div>`;
  }

  // Neueste Änderung zuerst, bei Gleichstand nach Ergebnis-ID (wie publication_model.sort_live).
  function sortiere(resultate) {
    const zeit = new Map(resultate.map((r) => [r, Date.parse(r.geaendert_am)]));
    return resultate.slice().sort((a, b) => (zeit.get(b) - zeit.get(a))
      || (a.ergebnis_id < b.ergebnis_id ? -1 : a.ergebnis_id > b.ergebnis_id ? 1 : 0));
  }

  function setzeInhalt(html, info) {
    if (html === angezeigt) return;
    angezeigt = html;
    el.inhalt.innerHTML = html;
    if (typeof window.__ttAnzeige === "function") {  // nur für Messungen (Lasttest), sonst ohne Wirkung
      requestAnimationFrame(() => window.__ttAnzeige({ t: Date.now(), ...info }));
    }
  }

  function zeichne() {
    if (!testtag) return;
    el.titel.textContent = testtag.titel;
    document.title = `${testtag.titel} – Live`;
    el.badge.innerHTML = badge(testtag.status);
    el.nav.hidden = false;
    const programme = [...new Set([...koepfe.values()].map((k) => k.programm).filter(Boolean))].sort(vergleicheProgramm);
    if (!programme.length) {
      el.filter.hidden = true; el.hinweise.innerHTML = "";
      setzeInhalt("<div class='tt-leer'>Es sind noch keine Live-Resultate veröffentlicht.</div>", { n: 0 });
      return;
    }
    if (auswahl.programm !== ALLE && !programme.includes(auswahl.programm)) auswahl.programm = ALLE;
    const aks = [...new Set([...koepfe.values()].filter((k) => auswahl.programm === ALLE
      || k.programm === auswahl.programm).map((k) => k.ak).filter(Boolean))].sort(vergleicheAk);
    if (auswahl.ak !== ALLE && !aks.includes(auswahl.ak)) auswahl.ak = ALLE;
    el.filter.hidden = false;
    optionen(el.programm, programme, auswahl.programm, programmText);
    optionen(el.ak, aks, auswahl.ak, (a) => a);

    // Rotationsfreigabe je Programm (Hinweis des Publishers, z. B. „Resultate bis Rotation 2 von 4 freigegeben“)
    const hinweise = new Map();
    for (const s of benoetigt(koepfe)) {
      const k = koepfe.get(s);
      if (k.hinweis) (hinweise.get(k.programm) || hinweise.set(k.programm, new Set()).get(k.programm)).add(k.hinweis);
    }
    el.hinweise.innerHTML = [...hinweise].sort(([a], [b]) => vergleicheProgramm(a, b))
      .map(([p, t]) => `<div>${h(programmText(p))}: ${h([...t].join(" / "))}</div>`).join("");

    const schl = benoetigt(koepfe);
    if (!schl.every((s) => zeilen.has(s))) {
      setzeInhalt("<div class='tt-leer'>Wird geladen …</div>", { n: -1 });
      return;
    }
    const resultate = sortiere(schl.flatMap((s) => zeilen.get(s).resultate));
    if (!resultate.length) {
      setzeInhalt("<div class='tt-leer'>Für diese Auswahl sind noch keine Resultate veröffentlicht.</div>", { n: 0 });
      return;
    }
    const teile = [`<div class="tt-anzahl">${resultate.length} Resultate – neueste Änderung zuerst</div><div class="tt-liste">`];
    resultate.slice(0, MAX_LIVE).forEach((r, i) => teile.push(karte(i + 1, r)));
    teile.push("</div>");
    if (resultate.length > MAX_LIVE) teile.push(`<div class="tt-anzahl">Die neuesten ${MAX_LIVE} Resultate werden angezeigt.</div>`);
    setzeInhalt(teile.join(""), { n: resultate.length, erster: kurz(resultate[0]) });
  }

  function kurz(r) {
    return r ? `${r.ergebnis_id}|${r.geaendert_am}|${r.S ?? ""}|${r.T ?? ""}|${r.punkte ?? ""}` : "";
  }

  function zeigeStatus() {
    if (!testtag) { el.warnung.hidden = true; return; }
    const zeitpunkte = [...koepfe.values()].map((k) => k.stand_am);
    const datenstand = zeitpunkte.length ? zeitpunkte.reduce((a, b) => (Date.parse(a) > Date.parse(b) ? a : b))
      : testtag.stand_am;
    el.stand.textContent = `Datenstand ${zeitText(datenstand, true)} · geprüft `
      + `${letzterErfolg ? zeitText(new Date(letzterErfolg).toISOString()) : "–"}`;
    const veraltet = testtag.status !== "archiviert" && (Date.now() - letzterErfolg > VERALTET_NACH
      || (nachholen && fehlerFolge > 0));
    el.warnung.hidden = !veraltet;
    if (veraltet) {
      const offline = navigator.onLine === false;
      el.warnung.className = `tt-warnung${offline ? " offline" : ""}`;
      el.warnung.textContent = `${offline ? "Keine Internetverbindung. " : ""}${VERALTET} (Stand `
        + `${letzterErfolg ? zeitText(new Date(letzterErfolg).toISOString()) : "–"}). Die Seite versucht es `
        + "automatisch erneut.";
    }
  }

  function zeigeNichtVerfuegbar() {
    el.titel.textContent = "Testtag";
    el.badge.innerHTML = "";
    setzeInhalt("<div class='tt-leer'>Dieser Testtag ist nicht verfügbar.</div>", { n: 0, verborgen: true });
  }

  function zeigeArchiv() {
    koepfe = new Map(); zeilen = new Map();
    el.titel.textContent = testtag.titel;
    el.badge.innerHTML = badge("archiviert");
    el.filter.hidden = true; el.nav.hidden = true; el.hinweise.innerHTML = ""; el.stand.textContent = "";
    setzeInhalt(`<div class='tt-leer'>Dieser Testtag ist abgeschlossen. Ranglisten und Resultate stehen im `
      + `<a href="${h(streamlitLink())}">Archiv</a>.</div>`, { n: 0 });
  }

  function zeigeWarten() {
    setzeInhalt("<div class='tt-leer'>Die Daten sind zurzeit nicht erreichbar. Die Seite versucht es automatisch "
      + "erneut.</div>", { n: -1 });
  }

  function melde(art, code) {
    if (typeof window.__ttEreignis === "function") window.__ttEreignis({ t: Date.now(), art, code });
  }

  // ── Start ─────────────────────────────────────────────────────────────────────────────────────────
  if (z && z.streamlit) el.zurueck.href = `${z.streamlit}/`;
  for (const a of document.querySelectorAll("[data-ziel]")) a.href = streamlitLink();
  if (!z) {
    el.zurueck.hidden = true;
    el.inhalt.innerHTML = "<div class='tt-leer'>Für diese Ansicht ist noch kein Zugang hinterlegt.</div>";
    return;
  }
  if (!slug || !SLUG.test(slug)) {
    el.inhalt.innerHTML = `<div class='tt-leer'>${slug ? "Die Adresse nennt keinen gültigen Testtag." : "Kein Testtag gewählt."} `
      + `<a href="${h(z.streamlit)}/">Alle Testtage</a></div>`;
    return;
  }
  const start = new URLSearchParams(location.search);
  auswahl.programm = start.get("p") || ALLE;
  auswahl.ak = start.get("ak") || ALLE;
  el.programm.addEventListener("change", () => { el.ak.value = ALLE; auswahlGeaendert(); });
  el.ak.addEventListener("change", auswahlGeaendert);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { if (!laeuft) takt(); } else { clearTimeout(timer); timer = null; }
  });
  window.addEventListener("online", () => { if (!laeuft) takt(); });
  window.addEventListener("offline", zeigeStatus);
  setInterval(zeigeStatus, 1000);
  takt();
})();
