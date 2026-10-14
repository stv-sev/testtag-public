/*
 * live.js – statische Ansicht eines laufenden Testtags: Live, Gerät, Ranglisten, Startliste und Zeitplan (ohne
 * Abhängigkeiten; Ranglistendarstellung in rangliste.js) sowie ohne `?t=` die Übersicht der öffentlichen Testtage
 * (Live, Demnächst, Archiv; archivierte öffnen das Streamlit-Archiv). Welche Ansichten hier angeboten werden, legt
 * config.js fest (`ansichten`); die übrigen führen in Streamlit. „Gerät“ gibt es, sobald Ranglisten angeboten werden.
 *
 * Liest wie quelle.py ausschliesslich über die Supabase Data API (PostgREST) mit dem Publishable Key – als
 * Abfrageparameter `apikey`, damit jede Anfrage ein einfacher CORS-GET ohne Vorabanfrage bleibt. Kein
 * Authorization-Kopf, kein Schreibweg, nichts im Browserspeicher.
 *
 * Datenfluss je Takt (Live und Ranglisten eines laufenden Testtags: 5 s; Startliste, Zeitplan oder vorbereiteter
 * Testtag: 30 s – deren Inhalt ändert sich selten; nur solange die Seite sichtbar ist):
 *   1. Änderungsabfrage `testtage` (id, Status, stand_am) – rund 160 B Nutzlast.
 *      Leer → Testtag unbekannt oder verborgen: alle geladenen Daten sofort verwerfen.
 *   2. Nur bei neuem stand_am/Status, nach einem Fehler oder alle 5 Minuten (Kontrolle): Kopfzeilen der
 *      angebotenen Bereiche (bereich, programm, ak, pruefsumme, stand_am, aktiv, hinweis – ohne Inhalt).
 *   3. Nur die für die gewählte Ansicht benötigten Zeilen, deren Prüfsumme sich geändert hat, in einer Anfrage
 *      (`pruefsumme=in.(…)`): Live die Live-Zeilen der Auswahl; Rangliste die Ranglistenzeile der AK und deren
 *      Live-Zeile (Einzelwertungen für die Details); Startliste die Zeilen der Programme; Zeitplan die gemeinsame
 *      Zeile. Jede Zeile wird geprüft (Schema, Prüfsumme über den kanonischen
 *      Inhalt wie publication_model.content_checksum); erst wenn alles stimmt, wird der Stand ersetzt.
 * Bei einem Fehler bleibt der letzte gültige Stand sichtbar, gekennzeichnet; neue Versuche mit wachsendem Abstand.
 *
 * Adresse: `?t=<slug>` Live, `&ansicht=geraet|rangliste|startliste|zeitplan`, `&p=<programm>&ak=<ak>` Auswahl (für alle
 * Ansichten; Startliste nur Programm, Zeitplan ohne Auswahl),
 * `&art=technik|athletik` Ebene der Rangliste (ohne: Gesamt). Ranglisten haben kein „Alle“: ohne passende Auswahl
 * gilt das erste Programm bzw. die erste AK; eine Live-Auswahl „Alle“ bleibt dabei erhalten.
 *
 * „Gerät“ (`&ansicht=geraet&g=<Gerät>&r=<Rotation>`, AK optional): die Riege, die in der gewählten Rotation am
 * gewählten Gerät eingeteilt ist, in Startreihenfolge – auch ohne Note; Linien S/T und Gerätetotal nur aus
 * veröffentlichten Resultaten, keine Geräteränge. Die Einteilung steht so in der Startlisten-Zeile
 * (`inhalt.einteilung`, vom Publisher aus den Codes des Excel-Blatts Startliste übernommen) – nie aus Noten oder der
 * Uhrzeit berechnet. Wählbar sind nur Rotationen 1 … `freigegeben` derselben geprüften Zeile (auch per Adresse); eine
 * nicht mehr zulässige Auswahl wird verworfen. Es gibt keine „aktuelle Rotation“: ohne Wahl wird keine Riege gezeigt.
 * Frühere Adressen `&ansicht=rangliste&art=technik&sicht=rotation` öffnen „Gerät“.
 */
"use strict";
(() => {
  const TAKT_LIVE = 5000;
  const TAKT_VORBEREITET = 30000;
  const KONTROLLE = 300000;
  const ZEITLIMIT = 8000;
  const MAX_PAUSE = 30000;
  const VERALTET_NACH = 12000;
  const TAKT_UEBERSICHT = 60000;
  const MAX_LIVE = 300;
  const LIVE = "resultate_live";
  const RANGLISTE = "resultate_rangliste";
  const STARTLISTE = "startliste";
  const ZEITPLAN = "zeitplan";
  const ANSICHTEN = ["live", "geraet", "rangliste", "startliste", "zeitplan"];
  const SCHEMA = "publikation-1";
  const ALLE = "Alle";
  const PROGRAMM_REIHENFOLGE = ["AK5-AK9+", "AK9-AK12"];
  const PROGRAMM_TEXT = { "AK5-AK9+": "AK5–AK9+", "AK9-AK12": "AK9–AK12" };
  const ATHLETIK_TEXT = { beweglichkeit: "Beweglichkeit", kraft: "Kraft" };
  const TITEL = { live: "Live", geraet: "Gerät", rangliste: "Ranglisten", startliste: "Startliste", zeitplan: "Zeitplan" };
  const SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const HEX64 = /^[0-9a-f]{64}$/;
  const STATUS = ["live", "vorbereitet", "archiviert"];
  const VERALTET = "Aktualisierung vorübergehend nicht möglich – letzter gültiger Stand wird angezeigt";
  const R = window.TT_RANGLISTE;

  const $ = (id) => document.getElementById(id);
  const el = { titel: $("titel"), badge: $("badge"), stand: $("stand"), warnung: $("warnung"), nav: $("nav"),
    unter: $("unter"), filter: $("filter"), programm: $("programm"), ak: $("ak"), arten: $("arten"), hinweise: $("hinweise"),
    inhalt: $("inhalt"), zurueck: $("zurueck"), filter2: $("filter2"), geraet: $("geraet"),
    rotation: $("rotation") };

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
    const ansichten = new Set(["live", ...(Array.isArray(c.ansichten) ? c.ansichten : [])
      .filter((a) => ANSICHTEN.includes(a) && a !== "geraet" && (a !== "rangliste" || R))]);
    if (ansichten.has("rangliste")) ansichten.add("geraet");  // „Gerät“ folgt dem Schalter der Ranglisten
    return { basis: url.origin, key, streamlit: /^(https:\/\/[^/?#@]+|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?)$/.test(streamlit) ? streamlit : "",
      rueckfall: c.rueckfall === true, ansichten };
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
  const auswahl = { programm: ALLE, ak: ALLE, geraet: null, rotation: null };  // rotation: nur vom Zuschauer gewählt
  let ansicht = "live";        // live | geraet | rangliste | startliste | zeitplan
  let taktMs = TAKT_LIVE;      // aktueller Abstand der Änderungsabfrage
  let art = "gesamt";          // Ebene der Rangliste

  function vergessen() {
    testtag = null; koepfe = new Map(); zeilen = new Map(); nachholen = true; angezeigt = "";
    el.inhalt.innerHTML = ""; el.hinweise.innerHTML = ""; el.stand.textContent = "";
    el.filter.hidden = true; el.nav.hidden = true; el.arten.hidden = true; el.filter2.hidden = true;
    for (const s of [el.programm, el.ak, el.geraet, el.rotation]) { s.innerHTML = ""; delete s.dataset.html; }
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

  // Bereiche der Kopfzeilen je angebotener Ansicht („Gerät“: Einteilung der Startliste und Live-Resultate).
  const BEREICHE = { live: [LIVE], geraet: [STARTLISTE, LIVE], rangliste: [RANGLISTE], startliste: [STARTLISTE], zeitplan: [ZEITPLAN] };
  const bereiche = () => [...new Set([...z.ansichten].flatMap((a) => BEREICHE[a]))];
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

  // Startliste: Programme mit veröffentlichter Startliste; gewähltes Programm oder alle.
  function startlistenAuswahl(kp) {
    const programme = programmeIn(kp, STARTLISTE);
    return { programme, programm: programme.includes(auswahl.programm) ? auswahl.programm : ALLE };
  }

  // Gerät: Programm mit Startliste (kein „Alle“), AK optional.
  function geraeteAuswahl(kp) {
    const programme = programmeIn(kp, STARTLISTE);
    const programm = programme.includes(auswahl.programm) ? auswahl.programm : programme[0];
    const aks = programm ? aksIn(kp, LIVE, programm) : [];
    return { programme, programm, aks, ak: aks.includes(auswahl.ak) ? auswahl.ak : ALLE };
  }

  function benoetigt(kp) {
    if (ansicht === "geraet") {
      const { programm, ak } = geraeteAuswahl(kp);
      if (!programm) return [];
      return [...kp.entries()].filter(([, k]) => k.programm === programm && (k.bereich === STARTLISTE
        || (k.bereich === LIVE && (ak === ALLE || k.ak === ak)))).map(([s]) => s);
    }
    if (ansicht === "startliste") {
      const { programm } = startlistenAuswahl(kp);
      return [...kp.entries()].filter(([, k]) => k.bereich === STARTLISTE && (programm === ALLE || k.programm === programm))
        .map(([s]) => s);
    }
    if (ansicht === "zeitplan") return [...kp.keys()].filter((s) => kp.get(s).bereich === ZEITPLAN);
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
    if (bereich === STARTLISTE) return { gruppen: inhalt.gruppen, einteilung: pruefeEinteilung(inhalt.einteilung) };
    if (bereich === ZEITPLAN) return { gruppen: inhalt.gruppen };
    const gruppen = inhalt.gruppen.map((g) => ({ liste: g.liste,
      eintraege: (Array.isArray(g.eintraege) ? g.eintraege : []).filter((e) => e && typeof e === "object") }));
    const struktur = inhalt.struktur && typeof inhalt.struktur === "object" && !Array.isArray(inhalt.struktur)
      ? inhalt.struktur : null;  // ältere Publikationen ohne struktur: Gliederung aus den Resultaten
    return { gruppen, struktur };
  }

  // Einteilung der Startlisten-Zeile; null = fehlt (ältere Publikation) oder widersprüchlich → nicht anzeigen.
  function pruefeEinteilung(e) {
    if (!e || typeof e !== "object" || !Array.isArray(e.geraete) || !Array.isArray(e.riegen)) return null;
    const geraete = e.geraete;
    if (!geraete.length || !geraete.every((g) => typeof g === "string" && g) || new Set(geraete).size !== geraete.length) return null;
    if (!Number.isInteger(e.rotationen) || e.rotationen < 1 || !Number.isInteger(e.freigegeben) || e.freigegeben < 0
        || e.freigegeben > e.rotationen) return null;
    const riegen = new Map();
    for (const r of e.riegen) {
      if (!r || typeof r !== "object" || !Number.isInteger(r.rotation) || r.rotation < 1 || r.rotation > e.rotationen
          || !geraete.includes(r.geraet) || !Array.isArray(r.teilnehmer) || !r.teilnehmer.every((t) => typeof t === "string")) return null;
      const k = `${r.rotation}|${r.geraet}`;
      if (riegen.has(k)) return null;
      riegen.set(k, r.teilnehmer);
    }
    return { geraete, rotationen: e.rotationen, freigegeben: e.freigegeben, riegen };
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
      naechster = taktFuer(tt);
      taktMs = naechster;
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
        ["ak", auswahl.ak === ALLE ? null : auswahl.ak], ["art", ansicht === "rangliste" && art !== "gesamt" ? art : null],
        ["sicht", null], ["g", ansicht === "geraet" && auswahl.geraet ? auswahl.geraet : null],
        ["r", ansicht === "geraet" && auswahl.rotation ? String(auswahl.rotation) : null]];
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
    nachTakt();
  }

  // 5 s nur für Live, Gerät und Ranglisten eines laufenden Testtags; Startliste, Zeitplan, vorbereitet: 30 s.
  const taktFuer = (tt) => (tt && tt.status === "live" && ["live", "geraet", "rangliste"].includes(ansicht)
    ? TAKT_LIVE : TAKT_VORBEREITET);

  // Nach einem Ansichtswechsel gilt deren Takt: ist die letzte Prüfung älter, sofort prüfen.
  function nachTakt() {
    if (!testtag || testtag.status === "archiviert") return;
    const soll = taktFuer(testtag);
    const alter = Date.now() - letzterErfolg;
    taktMs = soll;
    plane(alter >= soll ? 0 : soll - alter);  // fällt er in ein Nachladen, holt dieses ihn nach (taktOffen)
  }

  // ── Darstellung ───────────────────────────────────────────────────────────────────────────────────
  // Bereiche in Streamlit desselben Testtags (`&ansicht=`); Programm/AK kennt die Streamlit-Adresse nicht.
  function streamlitLink(ziel) {
    if (!z || !z.streamlit) return "#";
    return `${z.streamlit}/?t=${encodeURIComponent(slug)}${ziel ? `&ansicht=${encodeURIComponent(ziel)}` : ""}`;
  }

  // Umleitung in die Streamlit-Rangliste: Ebene mitgeben; „Gerät“ gibt es dort nicht – Streamlit zeigt dann einen
  // Hinweis und die Technik-Rangliste (Gerät/Rotation entfallen).
  function ranglistenZusatz(ziel) {
    const a = new URLSearchParams(location.search);
    if (ziel === "geraet") return "&art=technik&sicht=rotation";
    if (ziel !== "rangliste") return "";
    const ebene = ["technik", "athletik"].includes(a.get("art")) ? a.get("art") : null;
    if (!ebene) return "";
    return `&art=${ebene}${ebene === "technik" && a.get("sicht") === "rotation" ? "&sicht=rotation" : ""}`;
  }

  // Link innerhalb dieser Seite (gleicher Testtag, aktuelle Auswahl).
  function eigenerLink(ziel) {
    const u = new URL(location.href);
    for (const k of ["art", "sicht", "g", "r"]) u.searchParams.delete(k);
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
    if (ansicht !== "rangliste") el.arten.hidden = true;
    el.filter2.hidden = true;
    el.ak.closest("label").hidden = ansicht === "startliste";
    if (ansicht === "geraet") zeichneGeraet();
    else if (ansicht === "rangliste") zeichneRangliste();
    else if (ansicht === "startliste") zeichneStartliste();
    else if (ansicht === "zeitplan") zeichneZeitplan();
    else zeichneLive();
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

  // „Gerät“: eingeteilte Riege in Startreihenfolge (Einteilung aus der Startliste); Noten nur, soweit veröffentlicht.
  function zeichneGeraet() {
    el.hinweise.innerHTML = "";
    const { programme, programm, aks, ak } = geraeteAuswahl(koepfe);
    const info = (n, mehr) => ({ n, sicht: "geraet", ...mehr });
    const leer = (text, n = 0) => {
      el.filter2.hidden = true; el.hinweise.innerHTML = "";
      setzeInhalt(`<div class='tt-leer'>${h(text)}</div>`, info(n));
    };
    if (!programm) { el.filter.hidden = true; leer("Es ist noch keine Startliste veröffentlicht."); return; }
    el.filter.hidden = false;
    optionen(el.programm, programme, programm, programmText, false);
    optionen(el.ak, aks, ak, (a) => a, true);
    const schl = benoetigt(koepfe);
    if (!schl.every((s) => zeilen.has(s))) { leer("Wird geladen …", -1); return; }
    const start = zeilen.get(schluessel(STARTLISTE, programm, ""));
    const rot = start && start.einteilung;
    if (!rot) { leer("Die Einteilung nach Gerät wird mit der nächsten Publikation verfügbar."); return; }

    const geraete = rot.geraete;
    const geraet = geraete.includes(auswahl.geraet) ? auswahl.geraet : geraete[0];
    let verworfen = false;
    if (auswahl.rotation !== null && !(auswahl.rotation >= 1 && auswahl.rotation <= rot.freigegeben)) {
      auswahl.rotation = null; verworfen = true;  // Freigabe gesunken oder anderes Programm: nicht zulässig
    }
    if (auswahl.geraet !== null && geraet !== auswahl.geraet) { auswahl.geraet = null; verworfen = true; }
    if (verworfen) adresse(false);
    el.filter2.hidden = false;
    optionen(el.geraet, geraete, geraet, (g) => g, false);
    const html = [`<option value=""${auswahl.rotation === null ? " selected" : ""}>${rot.freigegeben ? "bitte wählen" : "keine freigegeben"}</option>`,
      ...Array.from({ length: rot.freigegeben }, (_, i) => `<option value="${i + 1}"${i + 1 === auswahl.rotation ? " selected" : ""}>`
        + `Rotation ${i + 1}</option>`)].join("");
    if (el.rotation.dataset.html !== html) { el.rotation.innerHTML = html; el.rotation.dataset.html = html; }
    el.rotation.value = auswahl.rotation === null ? "" : String(auswahl.rotation);
    el.rotation.disabled = !rot.freigegeben;
    el.hinweise.innerHTML = `<div>${h(programmText(programm))}: ${h(rot.freigegeben >= rot.rotationen ? "Alle Rotationen freigegeben."
      : rot.freigegeben ? `Resultate bis Rotation ${rot.freigegeben} von ${rot.rotationen} freigegeben.`
        : "Noch keine Rotation freigegeben.")}</div>`;
    if (!rot.freigegeben) {
      setzeInhalt("<div class='tt-leer'>Für dieses Programm ist noch keine Rotation freigegeben.</div>", info(0));
      return;
    }
    if (auswahl.rotation === null) {
      setzeInhalt("<div class='tt-leer'>Rotation wählen, um die an diesem Gerät eingeteilte Riege zu sehen.</div>", info(0));
      return;
    }

    // Riege so, wie die Startliste sie einteilt (Reihenfolge = Startposition am Gerät)
    const personen = new Map();
    for (const g of start.gruppen) {
      for (const t of (Array.isArray(g.teilnehmer) ? g.teilnehmer : [])) if (t && typeof t === "object") personen.set(t.teilnehmer, t);
    }
    const eingeteilt = (rot.riegen.get(`${auswahl.rotation}|${geraet}`) || []).map((k, i) => [i + 1, personen.get(k)])
      .filter(([, t]) => t);
    const turner = eingeteilt.filter(([, t]) => ak === ALLE || t.ak === ak);
    const riegen = [...new Set(eingeteilt.map(([, t]) => t.startgeraet).filter(Boolean))];
    const startgeraet = riegen.length === 1 ? riegen[0] : "";
    const resultate = new Map();  // nur veröffentlichte Technikresultate dieses Geräts (Publikation enthält nur Freigegebenes)
    for (const s of schl) {
      for (const r of zeilen.get(s).resultate || []) {
        if (r.bereich !== "technik" || r.geraet !== geraet || !(Number.isInteger(r.rotation) && r.rotation <= rot.freigegeben)) continue;
        if (!resultate.has(r.teilnehmer)) resultate.set(r.teilnehmer, []);
        resultate.get(r.teilnehmer).push(r);
      }
    }
    const kopf = `<div class='tt-anzahl'>Rotation ${h(auswahl.rotation)} · ${h(geraet)}`
      + `${startgeraet ? ` · Riege Startgerät ${h(startgeraet)}` : ""}${ak === ALLE ? "" : ` · ${h(ak)}`}</div>`;
    if (!turner.length) {
      setzeInhalt(`${kopf}<div class='tt-leer'>${eingeteilt.length
        ? "In dieser Riege turnt niemand dieser Altersklasse." : "In dieser Rotation ist an diesem Gerät keine Riege eingeteilt."}</div>`,
      info(0, { riege: startgeraet }));
      return;
    }
    const karten = turner.map(([nr, t]) => {
      const eigene = (resultate.get(t.teilnehmer) || []).slice()
        .sort((a, b) => (a.code < b.code ? -1 : a.code > b.code ? 1 : 0));
      const total = eigene.length ? R.summe(eigene.map((r) => r.T)) : null;
      const linien = eigene.map((r) => `<div class='tt-linie'><span>${h(r.anzeige)}</span><span class='tt-st'>S ${h(R.zahl(r.S))}</span>`
        + `<b class='tt-st'>T ${h(R.zahl(r.T))}</b></div>`).join("");
      const inhalt = eigene.length
        ? `<div class='tt-dz tt-total'><span>Gerätetotal ${h(geraet)}</span><b>${h(R.zahl(total))}</b></div><div class='tt-linien'>${linien}</div>`
        : "<div class='tt-dhinweis'>Noch kein veröffentlichtes Resultat an diesem Gerät.</div>";
      const meta = [t.verein, t.ak].filter(Boolean).map(h).join(" · ");
      return `<div class='tt-karte tt-rangkarte'><details data-k='${h(`rot|${geraet}|${auswahl.rotation}|${t.teilnehmer}`)}'><summary>`
        + `<div class='tt-karte-kopf'><div class='tt-nr' title='Startposition ${h(nr)}'>${h(nr)}</div>`
        + `<div class='tt-text'><strong>${h(t.vorname)} ${h(t.name)}</strong><small>${meta}</small></div>`
        + `<div class='tt-wert'><div class='tt-note'>${h(R.zahl(total))}</div></div><i class='tt-pfeil'>▾</i></div>`
        + `</summary><div class='tt-details'>${inhalt}</div></details></div>`;
    });
    setzeInhalt(`${kopf}<div class='tt-liste tt-rangliste'>${karten.join("")}</div><div class='tt-anzahl'>In Startreihenfolge, `
      + "ohne Rang. – = noch kein veröffentlichtes Resultat. Turner antippen für die Linien; T = Linienpunkte.</div>",
    info(turner.length, { riege: startgeraet, mitNote: turner.filter(([, t]) => resultate.has(t.teilnehmer)).length }));
  }

  // Wie app._startliste: je Programm die Gruppen nach Startgerät/Riege (leere Gruppen entfallen).
  function zeichneStartliste() {
    el.hinweise.innerHTML = "";
    const { programme, programm } = startlistenAuswahl(koepfe);
    el.filter.hidden = !programme.length;
    if (programme.length) optionen(el.programm, programme, programm, programmText, true);
    const schl = benoetigt(koepfe);
    if (!schl.every((s) => zeilen.has(s))) { setzeInhalt("<div class='tt-leer'>Wird geladen …</div>", { n: -1 }); return; }
    const listen = schl.map((s) => [koepfe.get(s).programm, zeilen.get(s)]).sort(([a], [b]) => vergleicheProgramm(a, b))
      .map(([p, zl]) => [p, zl.gruppen.filter((g) => Array.isArray(g.teilnehmer) && g.teilnehmer.length)])
      .filter(([, gruppen]) => gruppen.length);
    if (!listen.length) { setzeInhalt("<div class='tt-leer'>Es ist noch keine Startliste veröffentlicht.</div>", { n: 0 }); return; }
    const teile = [];
    for (const [p, gruppen] of listen) {
      teile.push(`<div class='tt-programm'>${h(programmText(p))}</div>`);
      for (const g of gruppen) teile.push(startlisteTabelle(g));
    }
    setzeInhalt(teile.join(""), { n: listen.reduce((n, [, gs]) => n + gs.reduce((m, g) => m + g.teilnehmer.length, 0), 0),
      pruef: schl.map((s) => zeilen.get(s).pruefsumme.slice(0, 8)).join(",") });
  }

  // Wie app.startliste_tabelle: Pos., Turner, Jg., AK, Verein.
  function startlisteTabelle(g) {
    const zeilenHtml = g.teilnehmer.filter((t) => t && typeof t === "object").map((t) => `<tr><td class='tt-pos'>${h(t.position ?? "")}</td>`
      + `<td class='tt-turner'>${h(t.vorname ?? "")} ${h(t.name ?? "")}</td><td>${h(t.jahrgang ?? "")}</td>`
      + `<td>${h(t.ak ?? "")}</td><td>${h(t.verein ?? "")}</td></tr>`).join("");
    return `<div class='tt-tabelle'><div class='tt-titel'>Startgerät / Riege: ${h(g.startgeraet)}</div>`
      + "<table class='tt-startliste'><thead><tr><th>Pos.</th><th>Turner</th><th>Jg.</th><th>AK</th><th>Verein</th></tr></thead>"
      + `<tbody>${zeilenHtml}</tbody></table></div>`;
  }

  // Wie app._zeitplan: gemeinsamer Tagesablauf in der Reihenfolge des Publishers (leere Blöcke entfallen).
  function zeichneZeitplan() {
    el.hinweise.innerHTML = ""; el.filter.hidden = true;
    const schl = benoetigt(koepfe);
    if (!schl.every((s) => zeilen.has(s))) { setzeInhalt("<div class='tt-leer'>Wird geladen …</div>", { n: -1 }); return; }
    const bloecke = schl.length ? zeilen.get(schl[0]).gruppen.filter((b) => Array.isArray(b.zeilen) && b.zeilen.length) : [];
    if (!bloecke.length) { setzeInhalt("<div class='tt-leer'>Es ist noch kein Zeitplan veröffentlicht.</div>", { n: 0 }); return; }
    const teile = [];
    for (const b of bloecke) {
      teile.push(`<div class='tt-tabelle tt-zeitplan'><div class='tt-titel'>${h(programmText(b.programm ?? ""))}</div>`);
      for (const zl of b.zeilen.filter((x) => x && typeof x === "object")) {
        teile.push(`<div class='tt-zeile'><span class='tt-uhr'>${h(zl.zeit)}</span><span>${h(zl.programmpunkt)}`
          + `${zl.hinweis ? `<small>${h(zl.hinweis)}</small>` : ""}</span></div>`);
      }
      teile.push("</div>");
    }
    setzeInhalt(teile.join(""), { n: bloecke.length, pruef: zeilen.get(schl[0]).pruefsumme.slice(0, 8) });
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
    const veraltet = testtag.status !== "archiviert" && (Date.now() - letzterErfolg > Math.max(VERALTET_NACH, taktMs + 7000)
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
      + `<a href="${h(streamlitLink(ansicht === "live" ? null : ansicht))}">Archiv</a>.</div>`, { n: 0 });
  }

  function zeigeWarten() {
    setzeInhalt("<div class='tt-leer'>Die Daten sind zurzeit nicht erreichbar. Die Seite versucht es automatisch "
      + "erneut.</div>", { n: -1 });
  }

  function melde(art, code) {
    if (typeof window.__ttEreignis === "function") window.__ttEreignis({ t: Date.now(), art, code });
  }

  // ── Übersicht ohne ?t= (wie app.uebersicht): nur, was die öffentlichen Leserechte liefern ───────────────
  let uebersichtFehler = 0;
  let uebersichtTimer = null;
  let uebersichtGezeigt = false;
  const KURZ_BADGE = { live: ["live", "LIVE"], vorbereitet: ["demnaechst", "Demnächst"], archiviert: ["archiv", "Archiv"] };

  async function uebersicht() {
    clearTimeout(uebersichtTimer);
    let naechster = TAKT_UEBERSICHT;
    try {
      const r = await hole(z, "testtage", [["select", "id,slug,titel,status,stand_am"]]);
      const tage = [];
      for (const x of r) { try { tage.push(testtagAusZeile(x)); } catch { /* ungültige Zeile: wie quelle.py auslassen */ } }
      // wie quelle.sortiere_testtage: Live, vorbereitet, Archiv; je Gruppe zuletzt aktualisiert zuerst
      tage.sort((a, b) => Date.parse(b.stand_am) - Date.parse(a.stand_am) || (a.slug < b.slug ? 1 : a.slug > b.slug ? -1 : 0));
      tage.sort((a, b) => STATUS.indexOf(a.status) - STATUS.indexOf(b.status));
      uebersichtFehler = 0;
      el.warnung.hidden = true;
      zeigeUebersicht(tage);
    } catch (e) {
      uebersichtFehler += 1;
      naechster = Math.min(MAX_PAUSE, TAKT_LIVE * 2 ** (uebersichtFehler - 1));
      melde("fehler", e && e.code ? e.code : "UNBEKANNT");
      if (!uebersichtGezeigt) zeigeWarten();
      else { el.warnung.hidden = false; el.warnung.className = "tt-warnung"; el.warnung.textContent = `${VERALTET}. Die Seite versucht es automatisch erneut.`; }
    } finally {
      if (document.visibilityState !== "hidden") uebersichtTimer = setTimeout(uebersicht, naechster);
    }
  }

  function zeigeUebersicht(tage) {
    uebersichtGezeigt = true;
    el.titel.textContent = "Testtage";
    el.unter.textContent = "Turnsport Aargau · Live-Resultate, Ranglisten, Startlisten";
    el.unter.hidden = false;
    document.title = "Testtage";
    if (!tage.length) { setzeInhalt("<div class='tt-leer'>Zurzeit ist kein Testtag veröffentlicht.</div>", { n: 0, uebersicht: true }); return; }
    const teile = [];
    for (const [status, ueberschrift] of [["live", "Live"], ["vorbereitet", "Demnächst"], ["archiviert", "Archiv"]]) {
      const gruppe = tage.filter((t) => t.status === status);
      if (!gruppe.length) continue;
      teile.push(`<h2 class='tt-ueberschrift'>${ueberschrift}</h2>`);
      for (const t of gruppe) {
        // laufende und bevorstehende Testtage hier, archivierte im Streamlit-Archiv
        const ziel = status === "archiviert" ? (z.streamlit ? `${z.streamlit}/?t=${encodeURIComponent(t.slug)}` : "#")
          : `?t=${encodeURIComponent(t.slug)}`;
        const [klasse, text] = KURZ_BADGE[status];
        teile.push(`<a class='tt-testtag' href="${h(ziel)}">${h(t.titel)}</a>`
          + `<div class='tt-meta'><span class="tt-badge ${klasse}">${klasse === "live" ? "<span class='tt-punkt'></span>" : ""}`
          + `${text}</span> &nbsp;Stand ${h(zeitText(t.stand_am, true))} · Adresse ?t=${h(t.slug)}</div>`);
      }
    }
    setzeInhalt(teile.join(""), { n: tage.length, uebersicht: true, slugs: tage.map((t) => t.slug).join(",") });
  }

  function ausAdresse() {
    const a = new URLSearchParams(location.search);
    const alt = a.get("ansicht") === "rangliste" && a.get("art") === "technik" && a.get("sicht") === "rotation";
    ansicht = alt && z.ansichten.has("geraet") ? "geraet" : z.ansichten.has(a.get("ansicht")) ? a.get("ansicht") : "live";
    auswahl.programm = a.get("p") || ALLE;
    auswahl.ak = a.get("ak") || ALLE;
    art = R && R.ARTEN.includes(a.get("art")) ? a.get("art") : "gesamt";
    auswahl.geraet = a.get("g") || null;
    auswahl.rotation = /^[1-9]\d?$/.test(a.get("r") || "") ? Number(a.get("r")) : null;  // Grenze prüft zeichneRotation
  }

  // ── Start ─────────────────────────────────────────────────────────────────────────────────────────
  const start = new URLSearchParams(location.search);
  const gewuenscht = start.get("ansicht") === "rangliste" && start.get("art") === "technik" && start.get("sicht") === "rotation"
    ? "geraet" : start.get("ansicht") || "live";  // frühere Adresse von „Gerät & Rotation“
  el.zurueck.href = location.pathname;  // Übersicht dieser Seite (nicht mehr die Streamlit-Übersicht)
  for (const a of document.querySelectorAll("[data-ziel]")) a.href = streamlitLink(a.dataset.ziel);
  for (const a of document.querySelectorAll("[data-ansicht]")) {  // hier nicht angeboten: Pille führt nach Streamlit
    if (z && a.dataset.ansicht === "geraet" && !z.ansichten.has("geraet")) { a.remove(); continue; }  // Streamlit kennt „Gerät“ nicht
    if (z && !z.ansichten.has(a.dataset.ansicht)) {
      a.dataset.ziel = a.dataset.ansicht; delete a.dataset.ansicht; a.href = streamlitLink(a.dataset.ziel);
    }
  }
  const nurStreamlit = !!z && ANSICHTEN.includes(gewuenscht) && !z.ansichten.has(gewuenscht);
  if (z && z.streamlit && (z.rueckfall || nurStreamlit)) {
    // Rückfall oder hier nicht angebotene Ansicht: bisherige Anzeige in Streamlit, vor jedem Datenabruf
    const ziel = ANSICHTEN.includes(gewuenscht) ? gewuenscht : "live";
    location.replace(slug && SLUG.test(slug) ? streamlitLink(ziel === "geraet" ? "rangliste" : ziel) + ranglistenZusatz(ziel)
      : `${z.streamlit}/`);
    return;
  }
  if (!z) {
    el.zurueck.hidden = true;
    el.inhalt.innerHTML = "<div class='tt-leer'>Für diese Ansicht ist noch kein Zugang hinterlegt.</div>";
    return;
  }
  if (!slug) {  // Übersicht der öffentlichen Testtage
    el.zurueck.hidden = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") uebersicht(); else clearTimeout(uebersichtTimer);
    });
    window.addEventListener("online", uebersicht);
    uebersicht();
    return;
  }
  if (!SLUG.test(slug)) {
    el.inhalt.innerHTML = `<div class='tt-leer'>Die Adresse nennt keinen gültigen Testtag. `
      + `<a href="${h(location.pathname)}">Alle Testtage</a></div>`;
    return;
  }
  ausAdresse();
  adresse(false);  // frühere Adresse von „Gerät & Rotation“ still auf &ansicht=geraet umschreiben
  el.programm.addEventListener("change", () => {
    auswahl.programm = el.programm.value; auswahl.ak = ALLE; adresse(false); auswahlGeaendert();
  });
  el.ak.addEventListener("change", () => { auswahl.ak = el.ak.value; adresse(false); auswahlGeaendert(); });
  el.geraet.addEventListener("change", () => { auswahl.geraet = el.geraet.value; adresse(false); zeichne(); });
  el.rotation.addEventListener("change", () => {
    auswahl.rotation = /^[1-9]\d?$/.test(el.rotation.value) ? Number(el.rotation.value) : null;
    adresse(false); zeichne();
  });
  el.arten.addEventListener("click", (e) => {
    const b = e.target.closest("[data-art]");
    if (!b || b.dataset.art === art) return;
    art = b.dataset.art; adresse(false); auswahlGeaendert();
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
    nachTakt();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { if (!laeuft) takt(); } else { clearTimeout(timer); timer = null; }
  });
  window.addEventListener("online", () => { if (!laeuft) takt(); });
  window.addEventListener("offline", zeigeStatus);
  setInterval(zeigeStatus, 1000);
  takt();
})();
