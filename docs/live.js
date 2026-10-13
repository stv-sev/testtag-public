/*
 * live.js – statische Ansicht eines Testtags: Live und Ranglisten (ohne Abhängigkeiten; Ranglistendarstellung in
 * rangliste.js). Startliste, Zeitplan und Archiv bleiben in Streamlit.
 *
 * Liest wie quelle.py ausschliesslich über die Supabase Data API (PostgREST) mit dem Publishable Key – als
 * Abfrageparameter `apikey`, damit jede Anfrage ein einfacher CORS-GET ohne Vorabanfrage bleibt. Kein
 * Authorization-Kopf, kein Schreibweg, nichts im Browserspeicher.
 *
 * Datenfluss je Takt (Live: 5 s, vorbereitet: 30 s; nur solange die Seite sichtbar ist):
 *   1. Änderungsabfrage `testtage` (id, Status, stand_am) – rund 160 B Nutzlast.
 *      Leer → Testtag unbekannt oder verborgen: alle geladenen Daten sofort verwerfen.
 *   2. Nur bei neuem stand_am/Status, nach einem Fehler oder alle 5 Minuten (Kontrolle): Kopfzeilen der
 *      Live- und Ranglistenzeilen (bereich, programm, ak, pruefsumme, stand_am, aktiv, hinweis – ohne Inhalt).
 *   3. Nur die für die gewählte Ansicht benötigten Zeilen, deren Prüfsumme sich geändert hat, in einer Anfrage
 *      (`pruefsumme=in.(…)`): Live die Live-Zeilen der Auswahl; Rangliste die Ranglistenzeile der AK und deren
 *      Live-Zeile (Einzelwertungen für die Details). Jede Zeile wird geprüft (Schema, Prüfsumme über den kanonischen
 *      Inhalt wie publication_model.content_checksum); erst wenn alles stimmt, wird der Stand ersetzt.
 * Bei einem Fehler bleibt der letzte gültige Stand sichtbar, gekennzeichnet; neue Versuche mit wachsendem Abstand.
 *
 * Adresse: `?t=<slug>` Live, `&ansicht=rangliste` Ranglisten, `&p=<programm>&ak=<ak>` Auswahl (beide Ansichten),
 * `&art=technik|athletik` Ebene der Rangliste (ohne: Gesamt). Ranglisten haben kein „Alle“: ohne passende Auswahl
 * gilt das erste Programm bzw. die erste AK; eine Live-Auswahl „Alle“ bleibt dabei erhalten.
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
  const RANGLISTE = "resultate_rangliste";
  const SCHEMA = "publikation-1";
  const ALLE = "Alle";
  const PROGRAMM_REIHENFOLGE = ["AK5-AK9+", "AK9-AK12"];
  const PROGRAMM_TEXT = { "AK5-AK9+": "AK5–AK9+", "AK9-AK12": "AK9–AK12" };
  const ATHLETIK_TEXT = { beweglichkeit: "Beweglichkeit", kraft: "Kraft" };
  const TITEL = { live: "Live", rangliste: "Ranglisten" };
  const SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const HEX64 = /^[0-9a-f]{64}$/;
  const STATUS = ["live", "vorbereitet", "archiviert"];
  const VERALTET = "Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt";
  const R = window.TT_RANGLISTE;

  const $ = (id) => document.getElementById(id);
  const el = { titel: $("titel"), badge: $("badge"), stand: $("stand"), warnung: $("warnung"), nav: $("nav"),
    filter: $("filter"), programm: $("programm"), ak: $("ak"), arten: $("arten"), hinweise: $("hinweise"),
    inhalt: $("inhalt"), zurueck: $("zurueck") };

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
  const schluessel = (bereich, programm, ak) => `${bereich}\u0000${programm}\u0000${ak}`;

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
    const streamlit = String(c.streamlitUrl || "").replace(/\/+$/, "");
    return { basis: url.origin, key, streamlit: /^(https:\/\/[^/?#@]+|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?)$/.test(streamlit) ? streamlit : "",
      rueckfall: c.rueckfall === true, rangliste: c.rangliste === true && !!R };
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
  let koepfe = new Map();      // Schlüssel → {bereich, programm, ak, pruefsumme, stand_am, hinweis} (nur aktive Zeilen)
  let zeilen = new Map();      // Schlüssel → geprüfter Inhalt: Live {pruefsumme, resultate}, Rangliste {pruefsumme, gruppen, struktur}
  let nachholen = true;        // Kopfzeilen beim nächsten Takt neu laden
  let letzteKontrolle = 0;
  let letzterErfolg = 0;       // letzte vollständig erfolgreiche Prüfung (ms)
  let fehlerFolge = 0;
  let timer = null;
  let laeuft = false;
  let angezeigt = "";          // zuletzt gezeichnetes HTML der Liste
  let auswahlOffen = false;    // Auswahl während eines Takts geändert: sofort nachladen
  let taktOffen = false;       // Takt fiel in ein gezieltes Nachladen: danach sofort nachholen
  const auswahl = { programm: ALLE, ak: ALLE };
  let ansicht = "live";        // live | rangliste
  let art = "gesamt";          // Ebene der Rangliste

  function vergessen() {
    testtag = null; koepfe = new Map(); zeilen = new Map(); nachholen = true; angezeigt = "";
    el.inhalt.innerHTML = ""; el.hinweise.innerHTML = ""; el.stand.textContent = "";
    el.filter.hidden = true; el.nav.hidden = true; el.arten.hidden = true;
    for (const s of [el.programm, el.ak]) { s.innerHTML = ""; delete s.dataset.html; }
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

  const bereiche = () => (z.rangliste ? [LIVE, RANGLISTE] : [LIVE]);
  const bereichFilter = (b) => (b.length === 1 ? `eq.${b[0]}` : `in.(${b.join(",")})`);

  async function ladeKoepfe(id) {
    const r = await hole(z, "veroeffentlichung", [
      ["select", "bereich,programm,ak,pruefsumme,stand_am,aktiv:inhalt->aktiv,schema:inhalt->>schema_version,"
        + "hinweis:inhalt->>hinweis"],
      ["oeffentliche_testtag_id", `eq.${id}`], ["bereich", bereichFilter(bereiche())]]);
    const neu = new Map();
    for (const k of r) {
      if (!bereiche().includes(k.bereich) || typeof k.programm !== "string" || typeof k.ak !== "string"
          || !HEX64.test(String(k.pruefsumme)) || Number.isNaN(new Date(k.stand_am).getTime())) throw new QuellenFehler("FORMAT");
      const s = schluessel(k.bereich, k.programm, k.ak);
      if (neu.has(s)) throw new QuellenFehler("DOPPELT");
      if (k.schema !== SCHEMA) throw new QuellenFehler("SCHEMA");
      if (k.aktiv === false) continue;  // Tombstone: nie anzeigen
      neu.set(s, { bereich: k.bereich, programm: k.programm, ak: k.ak, pruefsumme: k.pruefsumme, stand_am: k.stand_am,
        hinweis: k.hinweis || "" });
    }
    return neu;
  }

  const kopfListe = (kp, bereich) => [...kp.values()].filter((k) => k.bereich === bereich);
  const programmeIn = (kp, bereich) => [...new Set(kopfListe(kp, bereich).map((k) => k.programm).filter(Boolean))]
    .sort(vergleicheProgramm);
  const aksIn = (kp, bereich, programm) => [...new Set(kopfListe(kp, bereich).filter((k) => programm === ALLE
    || k.programm === programm).map((k) => k.ak).filter(Boolean))].sort(vergleicheAk);

  // Rangliste: konkrete Auswahl (kein „Alle“) – gewählte, sonst erste vorhandene.
  function ranglistenAuswahl(kp) {
    const programme = programmeIn(kp, RANGLISTE);
    const programm = programme.includes(auswahl.programm) ? auswahl.programm : programme[0];
    const aks = programm ? aksIn(kp, RANGLISTE, programm) : [];
    return { programme, programm, aks, ak: aks.includes(auswahl.ak) ? auswahl.ak : aks[0] };
  }

  // Live: gewählte Werte, sofern vorhanden, sonst „Alle“ (die Auswahl selbst bleibt für die Rangliste erhalten).
  function liveAuswahl(kp) {
    const programme = programmeIn(kp, LIVE);
    const programm = programme.includes(auswahl.programm) ? auswahl.programm : ALLE;
    const aks = aksIn(kp, LIVE, programm);
    return { programme, programm, aks, ak: aks.includes(auswahl.ak) ? auswahl.ak : ALLE };
  }

  function benoetigt(kp) {
    if (ansicht === "rangliste") {
      const { programm, ak } = ranglistenAuswahl(kp);
      if (!programm || !ak) return [];
      return [schluessel(RANGLISTE, programm, ak), schluessel(LIVE, programm, ak)].filter((s) => kp.has(s));
    }
    const { programm, ak } = liveAuswahl(kp);
    return [...kp.entries()].filter(([, k]) => k.bereich === LIVE && (programm === ALLE || k.programm === programm)
      && (ak === ALLE || k.ak === ak)).map(([s]) => s);
  }

  function pruefeInhalt(bereich, inhalt) {
    if (!Array.isArray(inhalt.gruppen) || !inhalt.gruppen.every((g) => g && typeof g === "object")) throw new QuellenFehler("FORMAT");
    if (bereich === LIVE) {
      const resultate = [];
      for (const g of inhalt.gruppen) for (const x of (Array.isArray(g.resultate) ? g.resultate : [])) if (x && typeof x === "object") resultate.push(x);
      return { resultate: sortiere(resultate) };
    }
    const gruppen = inhalt.gruppen.map((g) => ({ liste: g.liste,
      eintraege: (Array.isArray(g.eintraege) ? g.eintraege : []).filter((e) => e && typeof e === "object") }));
    const struktur = inhalt.struktur && typeof inhalt.struktur === "object" && !Array.isArray(inhalt.struktur)
      ? inhalt.struktur : null;  // ältere Publikationen ohne struktur: Gliederung aus den Resultaten
    return { gruppen, struktur };
  }

  // Lädt fehlende/geänderte Zeilen der Ansicht in einer Anfrage; ersetzt erst, wenn alle geprüft sind.
  async function ladeZeilen(id, kp) {
    const fehlend = benoetigt(kp).filter((s) => zeilen.get(s)?.pruefsumme !== kp.get(s).pruefsumme);
    const neu = new Map([...zeilen].filter(([s, zl]) => kp.get(s)?.pruefsumme === zl.pruefsumme));
    if (fehlend.length) {
      const summen = [...new Set(fehlend.map((s) => kp.get(s).pruefsumme))];
      const b = [...new Set(fehlend.map((s) => kp.get(s).bereich))];
      const r = await hole(z, "veroeffentlichung", [["select", "bereich,programm,ak,pruefsumme,inhalt"],
        ["oeffentliche_testtag_id", `eq.${id}`], ["bereich", bereichFilter(b)], ["pruefsumme", `in.(${summen.join(",")})`]]);
      for (const zl of r) {
        const s = schluessel(zl.bereich, zl.programm, zl.ak);
        if (!fehlend.includes(s) || kp.get(s).pruefsumme !== zl.pruefsumme) continue;  // inzwischen geändert
        let inhalt;
        try { inhalt = typeof zl.inhalt === "string" ? JSON.parse(zl.inhalt) : zl.inhalt; } catch { throw new QuellenFehler("JSON"); }
        if (!inhalt || typeof inhalt !== "object" || inhalt.schema_version !== SCHEMA) throw new QuellenFehler("SCHEMA");
        if (await sha256(kanonisch(inhalt)) !== zl.pruefsumme) throw new QuellenFehler("PRUEFSUMME");
        neu.set(s, { pruefsumme: zl.pruefsumme, ...pruefeInhalt(zl.bereich, inhalt) });
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
    if (laeuft) { taktOffen = true; return; }  // nie verlieren: sonst plant niemand den nächsten Takt
    laeuft = true;
    taktOffen = false;
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

  // Adresse nachführen: Ansicht, Auswahl, Ebene (neuer Verlaufseintrag nur beim Ansichtswechsel).
  function adresse(neuerEintrag) {
    try {
      const u = new URL(location.href);
      const werte = [["ansicht", ansicht === "live" ? null : ansicht], ["p", auswahl.programm === ALLE ? null : auswahl.programm],
        ["ak", auswahl.ak === ALLE ? null : auswahl.ak], ["art", ansicht === "rangliste" && art !== "gesamt" ? art : null]];
      for (const [k, v] of werte) { if (v === null) u.searchParams.delete(k); else u.searchParams.set(k, v); }
      if (u.href !== location.href) history[neuerEintrag ? "pushState" : "replaceState"](null, "", u);
    } catch { /* ohne Adresszeile weiter */ }
  }

  // Auswahl- oder Ansichtswechsel: fehlende Zeilen sofort gezielt nachladen.
  async function auswahlGeaendert() {
    zeichne();
    if (!testtag || testtag.status === "archiviert" || benoetigt(koepfe).every((s) => zeilen.has(s))) return;
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
      zeigeStatus();
      if (taktOffen) { taktOffen = false; plane(0); }
    }
  }

  function zeigeAnsicht(neu, neuerEintrag) {
    if (neu === ansicht) return;
    ansicht = neu;
    angezeigt = "";
    adresse(neuerEintrag);
    auswahlGeaendert();
  }

  // ── Darstellung ───────────────────────────────────────────────────────────────────────────────────
  // Bereiche in Streamlit desselben Testtags (`&ansicht=`); Programm/AK kennt die Streamlit-Adresse nicht.
  function streamlitLink(ziel) {
    if (!z || !z.streamlit) return "#";
    return `${z.streamlit}/?t=${encodeURIComponent(slug)}${ziel ? `&ansicht=${encodeURIComponent(ziel)}` : ""}`;
  }

  // Link innerhalb dieser Seite (gleicher Testtag, aktuelle Auswahl).
  function eigenerLink(ziel) {
    const u = new URL(location.href);
    u.searchParams.delete("art");
    if (ziel === "live") u.searchParams.delete("ansicht"); else u.searchParams.set("ansicht", ziel);
    return u.pathname + u.search;
  }

  function badge(status) {
    const [klasse, text] = status === "live" ? ["live", "Live – wird etwa alle 5 Sekunden aktualisiert"]
      : status === "archiviert" ? ["archiv", "Archiv – abgeschlossener Testtag, keine Aktualisierung"]
        : ["demnaechst", "Vorbereitet – noch nicht gestartet"];
    return `<span class="tt-badge ${klasse}">${klasse === "live" ? "<span class='tt-punkt'></span>" : ""}${h(text)}</span>`;
  }

  function optionen(select, werte, wert, text, mitAlle) {
    const html = (mitAlle ? [ALLE, ...werte] : werte).map((w) => `<option value="${h(w)}"${w === wert ? " selected" : ""}>`
      + `${h(w === ALLE ? ALLE : text(w))}</option>`).join("");
    if (select.dataset.html !== html) { select.innerHTML = html; select.dataset.html = html; }
    select.value = wert;
  }

  function navigation() {
    el.nav.hidden = false;
    for (const a of el.nav.querySelectorAll("[data-ansicht]")) {
      const aktiv = a.dataset.ansicht === ansicht;
      a.classList.toggle("aktiv", aktiv);
      if (aktiv) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      a.href = eigenerLink(a.dataset.ansicht);
    }
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
    // geöffnete Details bleiben bei einer Aktualisierung offen (gleicher Turner, gleiche Ebene)
    const offen = new Set([...el.inhalt.querySelectorAll("details[open][data-k]")].map((d) => d.dataset.k));
    angezeigt = html;
    el.inhalt.innerHTML = html;
    for (const d of el.inhalt.querySelectorAll("details[data-k]")) if (offen.has(d.dataset.k)) d.open = true;
    if (typeof window.__ttAnzeige === "function") {  // nur für Messungen (Lasttest), sonst ohne Wirkung
      requestAnimationFrame(() => window.__ttAnzeige({ t: Date.now(), ansicht, ...info }));
    }
  }

  function zeichne() {
    if (!testtag || testtag.status === "archiviert") return;
    el.titel.textContent = testtag.titel;
    document.title = `${testtag.titel} – ${TITEL[ansicht]}`;
    el.badge.innerHTML = badge(testtag.status);
    navigation();
    if (ansicht === "rangliste") zeichneRangliste(); else zeichneLive();
  }

  function zeichneLive() {
    el.arten.hidden = true;
    const { programme, programm, aks, ak } = liveAuswahl(koepfe);
    if (!programme.length) {
      el.filter.hidden = true; el.hinweise.innerHTML = "";
      setzeInhalt("<div class='tt-leer'>Es sind noch keine Live-Resultate veröffentlicht.</div>", { n: 0 });
      return;
    }
    el.filter.hidden = false;
    optionen(el.programm, programme, programm, programmText, true);
    optionen(el.ak, aks, ak, (a) => a, true);

    // Rotationsfreigabe je Programm (Hinweis des Publishers, z. B. „Resultate bis Rotation 2 von 4 freigegeben“)
    const schl = benoetigt(koepfe);
    const hinweise = new Map();
    for (const s of schl) {
      const k = koepfe.get(s);
      if (k.hinweis) (hinweise.get(k.programm) || hinweise.set(k.programm, new Set()).get(k.programm)).add(k.hinweis);
    }
    el.hinweise.innerHTML = [...hinweise].sort(([a], [b]) => vergleicheProgramm(a, b))
      .map(([p, t]) => `<div>${h(programmText(p))}: ${h([...t].join(" / "))}</div>`).join("");

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

  // Wie app._ranglisten: Programm, AK, Ebene; Reihenfolge, Ränge und Totale unverändert aus der Publikation.
  function zeichneRangliste() {
    el.hinweise.innerHTML = "";
    const { programme, programm, aks, ak } = ranglistenAuswahl(koepfe);
    if (!programme.length) {
      el.filter.hidden = true; el.arten.hidden = true;
      setzeInhalt("<div class='tt-leer'>Es ist noch keine Rangliste veröffentlicht.</div>", { n: 0 });
      return;
    }
    el.filter.hidden = false; el.arten.hidden = false;
    optionen(el.programm, programme, programm, programmText, false);
    optionen(el.ak, aks, ak, (a) => a, false);
    for (const b of el.arten.querySelectorAll("[data-art]")) b.setAttribute("aria-checked", String(b.dataset.art === art));
    const sr = schluessel(RANGLISTE, programm, ak);
    const sl = schluessel(LIVE, programm, ak);
    if (!zeilen.has(sr) || (koepfe.has(sl) && !zeilen.has(sl))) {
      setzeInhalt("<div class='tt-leer'>Wird geladen …</div>", { n: -1 });
      return;
    }
    const gruppe = zeilen.get(sr).gruppen.find((g) => g.liste === art);
    const eintraege = gruppe ? gruppe.eintraege : [];
    if (!eintraege.length) {
      setzeInhalt("<div class='tt-leer'>Für diese Auswahl ist noch keine Rangliste veröffentlicht.</div>", { n: 0 });
      return;
    }
    const alle = zeilen.has(sl) ? zeilen.get(sl).resultate : [];
    setzeInhalt(R.liste(eintraege, art, alle, zeilen.get(sr).struktur, koepfe.get(sr).hinweis, true),
      { n: eintraege.length, erster: kurz(alle[0]), pruef: zeilen.get(sr).pruefsumme });
  }

  function kurz(r) {
    return r ? `${r.ergebnis_id}|${r.geaendert_am}|${r.S ?? ""}|${r.T ?? ""}|${r.punkte ?? ""}` : "";
  }

  function zeigeStatus() {
    if (!testtag) { el.warnung.hidden = true; return; }
    const zeitpunkte = [...koepfe.values()].map((k) => k.stand_am);
    const datenstand = zeitpunkte.length ? zeitpunkte.reduce((a, b) => (Date.parse(a) > Date.parse(b) ? a : b))
      : testtag.stand_am;
    el.stand.textContent = testtag.status === "archiviert" ? "" : `Datenstand ${zeitText(datenstand, true)} · geprüft `
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
    document.title = "Testtag";
    setzeInhalt("<div class='tt-leer'>Dieser Testtag ist nicht verfügbar.</div>", { n: 0, verborgen: true });
  }

  function zeigeArchiv() {
    koepfe = new Map(); zeilen = new Map();
    el.titel.textContent = testtag.titel;
    document.title = testtag.titel;
    el.badge.innerHTML = badge("archiviert");
    el.filter.hidden = true; el.nav.hidden = true; el.arten.hidden = true; el.hinweise.innerHTML = "";
    el.stand.textContent = "";
    setzeInhalt(`<div class='tt-leer'>Dieser Testtag ist abgeschlossen. Ranglisten und Resultate stehen im `
      + `<a href="${h(streamlitLink(ansicht === "rangliste" ? "rangliste" : null))}">Archiv</a>.</div>`, { n: 0 });
  }

  function zeigeWarten() {
    setzeInhalt("<div class='tt-leer'>Die Daten sind zurzeit nicht erreichbar. Die Seite versucht es automatisch "
      + "erneut.</div>", { n: -1 });
  }

  function melde(art, code) {
    if (typeof window.__ttEreignis === "function") window.__ttEreignis({ t: Date.now(), art, code });
  }

  function ausAdresse() {
    const a = new URLSearchParams(location.search);
    ansicht = a.get("ansicht") === "rangliste" ? "rangliste" : "live";
    auswahl.programm = a.get("p") || ALLE;
    auswahl.ak = a.get("ak") || ALLE;
    art = R && R.ARTEN.includes(a.get("art")) ? a.get("art") : "gesamt";
  }

  // ── Start ─────────────────────────────────────────────────────────────────────────────────────────
  const gewuenscht = new URLSearchParams(location.search).get("ansicht") || "live";
  if (z && z.streamlit) el.zurueck.href = `${z.streamlit}/`;
  for (const a of document.querySelectorAll("[data-ziel]")) a.href = streamlitLink(a.dataset.ziel);
  if (z && !z.rangliste) {  // Ranglisten (noch) in Streamlit: Pille führt dorthin
    for (const a of document.querySelectorAll("[data-ansicht=rangliste]")) {
      delete a.dataset.ansicht; a.dataset.ziel = "rangliste"; a.href = streamlitLink("rangliste");
    }
  }
  const nurStreamlit = ["startliste", "zeitplan", ...(z && z.rangliste ? [] : ["rangliste"])].includes(gewuenscht);
  if (z && z.streamlit && (z.rueckfall || nurStreamlit)) {
    // Rückfall oder hier nicht angebotene Ansicht: bisherige Anzeige in Streamlit, vor jedem Datenabruf
    location.replace(slug && SLUG.test(slug) ? streamlitLink(nurStreamlit || gewuenscht === "rangliste" ? gewuenscht : "live")
      : `${z.streamlit}/`);
    return;
  }
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
  ausAdresse();
  el.programm.addEventListener("change", () => {
    auswahl.programm = el.programm.value; auswahl.ak = ALLE; adresse(false); auswahlGeaendert();
  });
  el.ak.addEventListener("change", () => { auswahl.ak = el.ak.value; adresse(false); auswahlGeaendert(); });
  el.arten.addEventListener("click", (e) => {
    const b = e.target.closest("[data-art]");
    if (!b || b.dataset.art === art) return;
    art = b.dataset.art; adresse(false); zeichne();
  });
  el.nav.addEventListener("click", (e) => {
    const a = e.target.closest("[data-ansicht]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;  // neuer Tab: normaler Link
    e.preventDefault();
    zeigeAnsicht(a.dataset.ansicht, true);
  });
  window.addEventListener("popstate", () => {
    const vorher = ansicht;
    ausAdresse();
    if (vorher !== ansicht) angezeigt = "";
    auswahlGeaendert();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { if (!laeuft) takt(); } else { clearTimeout(timer); timer = null; }
  });
  window.addEventListener("online", () => { if (!laeuft) takt(); });
  window.addEventListener("offline", zeigeStatus);
  setInterval(zeigeStatus, 1000);
  takt();
})();
