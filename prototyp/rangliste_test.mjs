// Funktionstest der statischen Ranglisten, Startliste und des Zeitplans in Headless-Chromium gegen eine nachgebildete Supabase-Data-API (nur
// synthetische Stände aus rangliste_daten.py; keine Anfrage verlässt den Rechner).
//   node rangliste_test.mjs <seiten-url> <staende.json> <chromium-exe> <bildordner> <gezeichnet.json>
// Prüft: Wechsel Live ⇄ Rangliste ohne Neuladen (Auswahl bleibt, Zurück-Taste), alle Ranglisten aller Stände
// (Vergleich mit Streamlit danach durch rangliste_daten.py vergleichen), Korrektur während der Anzeige (offene Details
// bleiben offen), Unterbruch, Prüfsummenfehler, verborgener Testtag, Archiv, Smartphone-/Desktopbreite,
// Startliste/Zeitplan (Wechsel, Programmfilter, Zurück-Taste, 30-s-Takt, Änderung, Unterbruch, verborgen, Archiv),
// Übersicht ohne ?t= (Gruppen, Reihenfolge, Ziele, verborgene Testtage), Streamlit-Links, Schalter `ansichten` und
// rueckfall:true.
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "playwright-core";

const [seite, datei, exe, bilder, ausgabe] = process.argv.slice(2);
const CONFIG_JS = process.env.CONFIG_JS || new URL("../docs/config.js", import.meta.url);  // Vorlage für die Schaltertests
const daten = JSON.parse(readFileSync(datei, "utf8"));
const { id: ID, slug: SLUG, titel: TITEL } = daten.testtag;
const STREAMLIT = "https://testtag.streamlit.app";
const programmText = (p) => String(p ?? "").replace(/-(?=AK)/g, "–");  // wie live.js / quelle.programm_text
const staende = Object.fromEntries(daten.staende.map((s) => [s.name, s]));

// ── nachgebildete Supabase (PostgREST-Ausschnitt, den live.js verwendet) ───────────────────────────────
// Übersicht: was RLS für anon liefert (verborgene Testtage fehlen dort bereits)
const UEBERSICHT = [
  { id: "5e7a0c1d-0000-4000-8000-0000000000b1", slug: "systemtest-demnaechst", titel: "Bald – synthetisch", status: "vorbereitet", stand_am: "2026-10-09T07:00:00Z" },
  { id: "5e7a0c1d-0000-4000-8000-0000000000a1", slug: "systemtest-archiv", titel: "Früher – synthetisch", status: "archiviert", stand_am: "2026-09-01T10:00:00Z" },
  { id: "5e7a0c1d-0000-4000-8000-0000000000a2", slug: "systemtest-archiv-neu", titel: "Neuer – synthetisch", status: "archiviert", stand_am: "2026-09-20T10:00:00Z" },
];
const sb = { stand: staende.r2, sichtbar: true, status: "live", kaputt: false, netz: true, verzoegerung: 0, anfragen: [] };
function filter(wert, ausdruck) {
  if (ausdruck.startsWith("eq.")) return String(wert) === ausdruck.slice(3);
  if (ausdruck.startsWith("in.(")) return ausdruck.slice(4, -1).split(",").includes(String(wert));
  throw new Error(`Filter ${ausdruck}`);
}
function spalte(zeile, ausdruck) {
  const m = /^(\w+)(->>?)(\w+)$/.exec(ausdruck);
  if (!m) return zeile[ausdruck];
  const w = zeile[m[1]]?.[m[3]];
  if (m[2] === "->") return w ?? null;
  return w === undefined || w === null ? null : typeof w === "string" ? w : JSON.stringify(w);
}
function antwort(url) {
  const u = new URL(url);
  const tabelle = u.pathname.split("/").pop();
  const p = u.searchParams;
  sb.anfragen.push({ tabelle, teil: p.has("pruefsumme") ? "zeilen" : tabelle === "testtage" ? "pruefung" : "koepfe",
    bereich: p.get("bereich"), key: p.get("apikey") });
  if (tabelle === "testtage") {
    const eigener = sb.sichtbar ? [{ id: ID, slug: SLUG, titel: TITEL, status: sb.status, stand_am: sb.stand.stand_am }] : [];
    if (!p.has("slug")) return [...UEBERSICHT, ...eigener];
    return p.get("slug") === `eq.${SLUG}` ? eigener : [];
  }
  if (tabelle !== "veroeffentlichung" || !sb.sichtbar) return [];
  let zeilen = sb.stand.zeilen;
  for (const [k, v] of p) if (!["select", "apikey"].includes(k)) zeilen = zeilen.filter((z) => filter(z[k], v));
  const felder = p.get("select").split(",").map((f) => { const [a, b] = f.includes(":") ? f.split(":") : [f, f]; return [a, b]; });
  return zeilen.map((z) => {
    const o = Object.fromEntries(felder.map(([a, b]) => [a, spalte(z, b)]));
    if (sb.kaputt && o.inhalt) o.inhalt = JSON.parse(JSON.stringify(o.inhalt).replace(/Lea/g, "Lia"));  // Übertragungsfehler
    return o;
  });
}

const fehler = [];
const pruefe = (bedingung, text) => { console.log(`${bedingung ? "ok  " : "FEHL"} ${text}`); if (!bedingung) fehler.push(text); };
const browser = await chromium.launch({ executablePath: exe });

async function kontext(viewport, mobil, config) {
  const ctx = await browser.newContext({ viewport, isMobile: mobil, hasTouch: mobil });
  await ctx.route(/\.supabase\.co\//, async (r) => {
    const req = r.request();
    if (req.method() !== "GET" || req.headers().authorization) { fehler.push(`unzulässige Anfrage ${req.method()}`); return r.abort(); }
    if (!sb.netz) return r.abort("internetdisconnected");  // Unterbruch (setOffline wirkt nicht auf umgeleitete Anfragen)
    if (sb.verzoegerung && new URL(req.url()).searchParams.has("pruefsumme")) await warte(sb.verzoegerung);
    await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(antwort(req.url())) });
  });
  await ctx.route(/testtag\.streamlit\.app/, (r) => r.fulfill({ status: 200, contentType: "text/html", body: "<title>Streamlit</title>Streamlit" }));
  if (config) {
    const basis = readFileSync(CONFIG_JS, "utf8");
    await ctx.route(/\/config\.js$/, (r) => r.fulfill({ status: 200, contentType: "text/javascript",
      body: basis.replace(/rueckfall: \w+/, `rueckfall: ${config.rueckfall}`)
        .replace(/ansichten: \[[^\]]*\]/, `ansichten: ${JSON.stringify(config.ansichten)}`) }));
  }
  const page = await ctx.newPage();
  page.on("pageerror", (e) => fehler.push(`JS-Fehler ${e.message}`));
  return { ctx, page };
}
const adresse = (extra = "") => `${seite}?t=${SLUG}${extra}`;
const bereit = (page) => page.waitForFunction(() => {
  const i = document.getElementById("inhalt");
  return i && !i.textContent.includes("Wird geladen") && i.querySelector(".tt-rangliste, .tt-liste, .tt-leer, .tt-tabelle");
}, null, { timeout: 15000 });
const breite = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const warte = (ms) => new Promise((r) => setTimeout(r, ms));
const gezeichnet = {};

// ── 0. Übersicht ohne ?t= ────────────────────────────────────────────────────────────────────────────────
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  sb.stand = staende.r8; sb.status = "live"; sb.sichtbar = true;
  await page.goto(seite);
  await page.waitForSelector(".tt-testtag");
  pruefe(JSON.stringify(await page.locator(".tt-ueberschrift").allTextContents()) === JSON.stringify(["Live", "Demnächst", "Archiv"]),
    "Übersicht: Gruppen Live, Demnächst, Archiv");
  const karten = await page.$$eval(".tt-testtag", (as) => as.map((a) => [a.textContent, a.getAttribute("href")]));
  pruefe(JSON.stringify(karten) === JSON.stringify([[TITEL, `?t=${SLUG}`], ["Bald – synthetisch", "?t=systemtest-demnaechst"],
    ["Neuer – synthetisch", `${STREAMLIT}/?t=systemtest-archiv-neu`], ["Früher – synthetisch", `${STREAMLIT}/?t=systemtest-archiv`]]),
    `Übersicht: Reihenfolge und Ziele (statisch bzw. Streamlit-Archiv) ${JSON.stringify(karten.map((k) => k[1]))}`);
  pruefe(await page.locator("#zurueck").isHidden() && await page.locator("#nav").isHidden() && await page.title() === "Testtage",
    "Übersicht ohne Zurück-Link und Navigation");
  pruefe(await breite(page), "Übersicht ohne waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/mobil-uebersicht.png`, fullPage: true });
  await page.click(`.tt-testtag[href="?t=${SLUG}"]`);
  await page.waitForSelector(".tt-liste .tt-karte");
  pruefe(new URL(page.url()).search === `?t=${SLUG}`, "Übersicht → Live dieses Testtags");
  await page.click("#zurueck");
  await page.waitForSelector(".tt-testtag");
  pruefe(new URL(page.url()).search === "", "„← Alle Testtage“ führt zur Übersicht dieser Seite");
  sb.sichtbar = false;  // verborgen: fehlt in der Übersicht, direkter Slug zeigt nichts
  await page.reload();
  await page.waitForSelector(".tt-testtag");
  pruefe(!(await page.content()).includes(TITEL), "verborgener Testtag erscheint nicht in der Übersicht");
  await page.goto(adresse("&ansicht=startliste"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("nicht verfügbar"));
  pruefe(!(await page.content()).includes("Synthaler") && await page.locator("#nav").isHidden(), "verborgen, direkter Slug: keine Personendaten");
  sb.sichtbar = true;
  await ctx.close();
}

// ── 1. Smartphone: Wechsel Live ⇄ Rangliste, Korrektur, Unterbruch, Prüfsumme, verborgen, Archiv ───────────
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  let neueTabs = 0;
  ctx.on("page", () => { neueTabs += 1; });
  sb.stand = staende.r8;
  await page.goto(adresse());
  await page.waitForSelector(".tt-karte");
  await page.selectOption("#programm", "AK5-AK9+");
  await page.selectOption("#ak", "AK 7");
  await bereit(page);
  pruefe(await page.$eval("[data-ansicht=rangliste]", (a) => a.getAttribute("href")).then((h) => h.includes("ansicht=rangliste")
    && h.includes("ak=AK+7")), "Live: Pille Ranglisten verweist auf diese Seite mit Auswahl");
  pruefe(await page.locator("[data-ziel]").count() === 0 && await page.locator("[data-ansicht]").count() === 5,
    "Live: alle fünf Pillen auf dieser Seite");
  await page.evaluate(() => { window.__ohneNeuladen = true; });
  const vorWechsel = sb.anfragen.length;
  const t0 = Date.now();
  await page.click("[data-ansicht=rangliste]");
  await page.waitForSelector(".tt-rangliste");
  const wechselMs = Date.now() - t0;
  const neu = sb.anfragen.slice(vorWechsel);
  pruefe(await page.evaluate(() => window.__ohneNeuladen === true), `Wechsel ohne Neuladen der Seite (${wechselMs} ms)`);
  pruefe(neu.length === 1 && neu[0].teil === "zeilen" && neu[0].bereich === "eq.resultate_rangliste",
    `Wechsel lädt nur die Ranglistenzeile der AK (${JSON.stringify(neu.map((a) => `${a.teil}:${a.bereich}`))})`);
  const u = new URL(page.url());
  pruefe(u.searchParams.get("ansicht") === "rangliste" && u.searchParams.get("p") === "AK5-AK9+" && u.searchParams.get("ak") === "AK 7",
    `Adresse ${u.search}`);
  pruefe(await page.inputValue("#programm") === "AK5-AK9+" && await page.inputValue("#ak") === "AK 7", "Auswahl Programm/AK bleibt erhalten");
  pruefe(await page.locator("[data-ansicht=rangliste].aktiv").count() === 1, "Pille Ranglisten aktiv");
  pruefe(await page.locator("#programm option", { hasText: "Alle" }).count() === 0, "Rangliste ohne „Alle“");
  pruefe((await page.title()).endsWith("– Ranglisten"), "Seitentitel Ranglisten");
  await page.goBack();
  await page.waitForSelector(".tt-liste .tt-karte");
  pruefe(!page.url().includes("ansicht=") && await page.inputValue("#ak") === "AK 7", "Zurück-Taste: Live mit derselben Auswahl");
  await page.goForward();
  await page.waitForSelector(".tt-rangliste");
  pruefe(page.url().includes("ansicht=rangliste"), "Vorwärts: wieder Rangliste");
  // Ebenen
  for (const art of ["technik", "athletik", "gesamt"]) {
    await page.click(`[data-art=${art}]`);
    await page.waitForFunction((a) => document.querySelector(`[data-art=${a}]`).getAttribute("aria-checked") === "true", art);
    pruefe((art === "gesamt") !== page.url().includes(`art=${art}`), `Ebene ${art} (Adresse ${new URL(page.url()).search})`);
  }
  // Details öffnen (Gesamt, dann Technik mit Gerät), Bildschirmfotos
  const lea = page.locator(".tt-rangkarte", { hasText: "Lea" });
  await lea.locator("summary").first().click();
  pruefe(await lea.locator(".tt-details").isVisible(), "Details erst nach Antippen sichtbar");
  pruefe(await breite(page), "Smartphone: keine waagrechte Scrollbar (Gesamt offen)");
  await page.screenshot({ path: `${bilder}/mobil-rangliste-gesamt.png`, fullPage: true });
  await page.click("[data-art=technik]");
  await lea.locator("summary").first().click();
  await lea.locator("details.tt-geraet summary").first().click();
  pruefe(await breite(page), "Smartphone: keine waagrechte Scrollbar (Technik, Gerät offen)");
  await page.screenshot({ path: `${bilder}/mobil-rangliste-technik.png`, fullPage: true });
  // Korrektur während der Anzeige: neuer Stand, offene Details bleiben offen
  const leaText = async () => (await lea.innerHTML()).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  pruefe((await leaText()).includes("Synth Boden vorwärts S 5 T 7.5"), "vor Korrektur: Boden vorwärts T 7.5");
  const vorKorrektur = sb.anfragen.length;
  sb.stand = staende.korrektur;
  await page.waitForFunction(() => document.body.textContent.includes("S 6T 9") || document.body.textContent.includes("S 6 T 9"), null, { timeout: 12000 });
  pruefe((await leaText()).includes("Synth Boden vorwärts S 6 T 9"), "Korrektur sichtbar (Boden vorwärts S 6, T 9)");
  pruefe(await lea.locator("details[open] details.tt-geraet[open]").count() === 1, "geöffnete Details bleiben bei der Aktualisierung offen");
  const kor = sb.anfragen.slice(vorKorrektur);
  pruefe(kor.filter((a) => a.teil === "zeilen").length === 1, `Korrektur: geänderte Zeilen in einer Anfrage (${kor.map((a) => a.teil).join(",")})`);
  await page.click("[data-art=athletik]");
  await page.screenshot({ path: `${bilder}/mobil-rangliste-athletik.png`, fullPage: true });
  // Unterbruch
  await ctx.setOffline(true); sb.netz = false;
  await page.waitForSelector("#warnung:not([hidden])", { timeout: 25000 });
  pruefe((await page.textContent("#warnung")).includes("letzter gültiger Stand") && await page.locator(".tt-rangkarte").count() > 0,
    "offline: Hinweis, letzter gültiger Stand bleibt sichtbar");
  await page.screenshot({ path: `${bilder}/mobil-rangliste-offline.png` });
  await ctx.setOffline(false); sb.netz = true;
  await page.waitForSelector("#warnung", { state: "hidden", timeout: 15000 });
  pruefe(true, "wieder online: Hinweis verschwindet");
  // Prüfsummenfehler: neuer Stand kommt verfälscht an → alter gültiger Stand bleibt, Hinweis
  sb.kaputt = true; sb.stand = staende.r8;
  await page.waitForSelector("#warnung:not([hidden])", { timeout: 25000 });
  pruefe((await page.content()).includes("Lea") && !(await page.content()).includes("Lia"), "Prüfsummenfehler: verfälschter Inhalt nie angezeigt, letzter Stand bleibt");
  sb.kaputt = false;
  await page.waitForSelector("#warnung", { state: "hidden", timeout: 20000 });
  await page.click("[data-art=technik]");
  await page.waitForFunction(() => document.body.textContent.includes("T 7.5"), null, { timeout: 10000 });
  pruefe(true, "nach Behebung: neuer Stand geladen");
  // verborgen
  const namen = ["Lea", "Synthaler", "Mia"];
  sb.sichtbar = false;
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("nicht verfügbar"), null, { timeout: 12000 });
  const html = await page.content();
  pruefe(namen.every((n) => !html.includes(n)) && await page.locator("#nav").isHidden() && await page.locator("#arten").isHidden(),
    "verborgen: keine Personendaten mehr im DOM, keine Navigation");
  await page.screenshot({ path: `${bilder}/mobil-rangliste-verborgen.png` });
  sb.sichtbar = true;
  await page.waitForSelector(".tt-rangliste", { timeout: 40000 });
  pruefe(true, "wieder öffentlich: Rangliste erscheint wieder");
  // Archiv
  sb.status = "archiviert";
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("abgeschlossen"), null, { timeout: 12000 });
  pruefe(await page.getAttribute("#inhalt a", "href") === `${STREAMLIT}/?t=${SLUG}&ansicht=rangliste`, "Archiv: Link ins Streamlit-Archiv (Ranglisten)");
  pruefe(!(await page.content()).includes("Lea"), "Archiv: keine Personendaten der statischen Seite");
  sb.status = "live";
  pruefe(neueTabs === 0, "alles im selben Tab");
  await ctx.close();
}

// ── 1b. Wechsel, während der Takt fällig wird (langsame Antwort): Aktualisierung läuft danach weiter ──────────
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  sb.stand = staende.r8; sb.status = "live"; sb.sichtbar = true;
  await page.goto(adresse("&p=AK5-AK9%2B&ak=AK+7&art=technik"));  // Ebene Technik gilt nach dem Wechsel
  await page.waitForSelector(".tt-liste .tt-karte");
  sb.verzoegerung = 7000;  // länger als der 5-s-Takt
  await page.click("[data-ansicht=rangliste]");
  await page.waitForSelector(".tt-rangliste", { timeout: 20000 });
  sb.verzoegerung = 0;
  sb.stand = staende.korrektur;
  const t0 = Date.now();
  const ok = await page.waitForFunction(() => document.body.innerHTML.includes("T 9"), null, { timeout: 20000 }).then(() => true, () => false);
  pruefe(ok, `Takt während des Wechsels fällig: nächste Publikation trotzdem angezeigt (${ok ? Date.now() - t0 : "–"} ms)`);
  await ctx.close();
}

// ── 1d. Ansicht „Gerät“ und gestufte Gesamtdetails ────────────────────────────────────────────────────────
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  sb.stand = staende.r8; sb.status = "live"; sb.sichtbar = true; sb.netz = true;
  const text = async () => (await page.innerHTML("#inhalt")).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  const params = () => new URL(page.url()).searchParams;
  const rotationen = () => page.locator("#rotation option").allTextContents();
  // gestuft: nach dem Öffnen nur Total Technik, Total Athletik, Gesamt; Stufen zu Beginn zu
  await page.goto(adresse("&ansicht=rangliste&p=AK5-AK9%2B&ak=AK+7"));
  await bereit(page);
  const lea = page.locator(".tt-rangkarte", { hasText: "Lea" });
  await lea.locator("summary").first().click();
  const sichtbar = await lea.locator(".tt-details > *").evaluateAll((xs) => xs.filter((x) => x.offsetParent !== null)
    .map((x) => (x.tagName === "DETAILS" ? x.querySelector("summary").innerText : x.innerText).replace(/\s+/g, " ").trim()));
  pruefe(JSON.stringify(sichtbar) === JSON.stringify(["Total Technik 16.5", "Total Athletik 8", "Gesamt 24.5"]),
    `Gesamt: nur drei Zeilen nach dem Öffnen (${JSON.stringify(sichtbar)})`);
  pruefe(await lea.locator(".tt-stufe .tt-linie").first().isHidden(), "Gesamt: Geräte/Linien erst nach weiterem Antippen");
  await lea.locator("details.tt-total summary", { hasText: "Total Technik" }).click();
  await lea.locator(".tt-stufe details.tt-geraet summary", { hasText: "Boden" }).click();
  pruefe(await lea.locator(".tt-linie", { hasText: "Synth Boden vorwärts" }).first().isVisible(), "Total Technik → Boden → Linien S/T");
  await lea.locator("details.tt-total summary", { hasText: "Total Athletik" }).click();
  await lea.locator(".tt-stufe details.tt-geraet summary", { hasText: "Total Beweglichkeit" }).click();
  pruefe(await lea.locator(".tt-linie", { hasText: "Bücke" }).first().isVisible()
    && (await lea.locator(".tt-linie", { hasText: "Bücke" }).first().innerText()).replace(/\s+/g, " ").includes("Bücke 0"),
  "Total Athletik → Total Beweglichkeit → Einzeltests (Nullwert 0)");
  pruefe(await breite(page), "Smartphone: Gesamt gestuft geöffnet ohne waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/mobil-gesamt-gestuft.png`, fullPage: true });

  sb.stand = staende.r2;
  // Pillen: Live · Gerät · Ranglisten · Startliste · Zeitplan
  const pillen = (await page.locator("#nav .tt-pille").allTextContents()).map((t) => t.trim());
  pruefe(JSON.stringify(pillen) === JSON.stringify(["⚡ Live", "🤸 Gerät", "🏅 Ranglisten", "📋 Startliste", "🕐 Zeitplan"]),
    `Pillen in der Reihenfolge Live · Gerät · Ranglisten · Startliste · Zeitplan (${pillen.join(" · ")})`);
  // Gerät: Rotation 5 per Adresse bei Freigabe 2 → verworfen, nichts angezeigt
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&g=Boden&r=5"));
  await bereit(page);
  pruefe(!params().has("r") && await page.inputValue("#rotation") === ""
    && JSON.stringify(await rotationen()) === JSON.stringify(["bitte wählen", "Rotation 1", "Rotation 2"]),
  `Freigabe 2: nur Rotation 1–2 wählbar, r=5 aus der Adresse verworfen (${new URL(page.url()).search})`);
  pruefe((await text()).includes("Rotation wählen") && !(await text()).includes("Lea"), "ohne gewählte Rotation keine Riege (keine „aktuelle Rotation“)");
  pruefe(JSON.stringify(await page.locator("#geraet option").allTextContents())
    === JSON.stringify(["Boden", "Pferd", "Ringe", "Beweglichkeit", "Sprung", "Barren", "Trampolin", "Reck"]),
  "Geräte: alle Stationen der Startliste in Stationsfolge, auch Beweglichkeit");
  pruefe((await page.textContent("#hinweise")).includes("Resultate bis Rotation 2 von 8 freigegeben."), "Hinweis Freigabe (keine laufende Rotation)");
  pruefe(await page.locator("[data-ansicht=geraet].aktiv").count() === 1 && await page.locator("#arten").isHidden()
    && await page.inputValue("#ak") === "Alle", "Pille Gerät aktiv, keine Ranglistenebenen, AK optional (Alle)");
  await page.selectOption("#rotation", "1");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Riege Startgerät"));
  pruefe(params().get("r") === "1" && params().get("g") === "Boden" && params().get("ansicht") === "geraet"
    && !params().has("art") && !params().has("sicht"), `Adresse ${new URL(page.url()).search}`);
  pruefe((await text()).startsWith("Rotation 1 · Boden · Riege Startgerät Boden") && await page.locator("#inhalt .tt-rangkarte").count() === 1
    && await page.locator("#inhalt .tt-rang").count() === 0, "Boden, Rotation 1: Riege Boden, ohne Rang");
  await page.locator(".tt-rangkarte summary").first().click();
  pruefe((await text()).includes("11.5 ▾ Gerätetotal Boden 11.5 Synth Boden vorwärts S 5 T 7.5 Synth Boden rückwärts S 4 T 4"),
    `Linien S/T und Gerätetotal (${(await text()).slice(0, 220)})`);
  await page.selectOption("#geraet", "Pferd");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Pferd · Riege"));
  const pferd = await page.locator("#inhalt .tt-rangkarte .tt-text strong").allTextContents();
  const noten = await page.locator("#inhalt .tt-rangkarte .tt-note").allTextContents();
  pruefe(pferd.length === 2 && pferd[0].startsWith("Max ") && pferd[1].startsWith("Nina "),
    `Pferd, Rotation 1: Riege Pferd in Startreihenfolge (${pferd.join(", ")})`);
  // alle vorgesehenen Linien: noch nicht gewertet mit „–“
  const nina = page.locator(".tt-rangkarte", { hasText: "Nina" });
  await nina.locator("summary").click();
  const ninaText = (await nina.innerText()).replace(/\s+/g, " ");
  pruefe(ninaText.includes("Gerätetotal Pferd –") && ninaText.includes("Synth Scheren S – T –"),
    `ohne Note: vorgesehene Linie mit „–“ (${ninaText.slice(0, 110)})`);
  await nina.locator("summary").click();
  pruefe(JSON.stringify(noten) === JSON.stringify(["6", "–"]), `auch ohne Note gelistet (${noten.join(", ")})`);
  await page.selectOption("#ak", "AK 6");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("· AK 6"));
  pruefe((await page.locator("#inhalt .tt-rangkarte .tt-text strong").allTextContents()).every((n) => n.startsWith("Nina")), "AK-Filter: nur AK 6");
  await page.selectOption("#ak", "Alle");
  await page.selectOption("#rotation", "2");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Rotation 2 · Pferd · Riege Startgerät Boden"));
  pruefe((await page.locator("#inhalt .tt-note").allTextContents()).join() === "5", "Pferd, Rotation 2: Riege Boden (Lea 5)");
  await page.selectOption("#geraet", "Reck");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("keine Riege eingeteilt"));
  pruefe(true, "Reck, Rotation 2: keine Riege (Startgerät Trampolin leer)");
  pruefe(await breite(page), "Smartphone: Gerät ohne waagrechte Scrollbar");
  // Beweglichkeit als Gerät: Riege der Rotation 4 (Startgerät Boden) mit Total Beweglichkeit und Einzeltests
  sb.stand = staende.r8;
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&g=Beweglichkeit&r=4"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Rotation 4 · Beweglichkeit · Riege Startgerät Boden"));
  await page.locator(".tt-rangkarte", { hasText: "Lea" }).locator("summary").click();
  const bew = (await page.locator(".tt-rangkarte", { hasText: "Lea" }).innerText()).replace(/\s+/g, " ");
  pruefe(bew.includes("Total Beweglichkeit 0") && bew.includes("Bücke 0") && !bew.includes(" S ") && !bew.includes("T "),
    `Beweglichkeit: Total und Einzeltests statt S/T (${bew.slice(0, 120)})`);
  pruefe(bew.includes("Arm-Rumpf-Winkel rücklings –"), "Beweglichkeit: alle vorgesehenen Tests, fehlende mit „–“");
  // Linie nur für bestimmte AK: Nina (AK 6) am Boden ohne „Synth Boden rückwärts“, Lea (AK 7) mit
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&g=Boden&r=8"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Rotation 8 · Boden"));
  const ninaBoden = page.locator(".tt-rangkarte", { hasText: "Nina" });
  await ninaBoden.locator("summary").click();
  const nb = (await ninaBoden.innerText()).replace(/\s+/g, " ");
  pruefe(nb.includes("Synth Boden vorwärts") && !nb.includes("Synth Boden rückwärts"),
    `für AK 6 nicht vorgesehene Linie fehlt (${nb.slice(0, 110)})`);
  // Sprung AK9–AK12: AK 9 „Sprung 1/2“; AK 10–12 genau eine gewertete Alternative oder beide „(Alternative)“
  const sprung = [];
  for (let r = 1; r <= 7; r++) {
    await page.goto(adresse(`&ansicht=geraet&p=AK9-AK12&g=Sprung&r=${r}`));
    await page.waitForFunction((rr) => document.getElementById("inhalt").textContent.includes(`Rotation ${rr} · Sprung`), r);
    for (const karte of await page.locator(".tt-rangkarte").all()) {
      await karte.locator("summary").click();
      sprung.push((await karte.innerText()).replace(/\s+/g, " "));
    }
  }
  const okSprung = sprung.every((t) => (t.includes("AK 9") ? t.includes("Sprung 1") && t.includes("Sprung 2")
    : (t.match(/\(Alternative\)/g) || []).length === 2 || ["Tsukahara", "Yurchenko"].filter((x) => t.includes(x)).length === 1));
  pruefe(sprung.length > 0 && okSprung, `Sprung: ${sprung.length} Turner, Linien je AK und Alternativen korrekt`);
  sb.stand = staende.r2;
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&g=Beweglichkeit&r=1"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Rotation 1 · Beweglichkeit"));
  pruefe((await text()).includes("Bücke –") && !/Bücke \d/.test(await text()),
    "Beweglichkeit, Freigabe 2: Tests vorgesehen („–“), keine Punkte aus nicht freigegebenen Rotationen");
  // Freigabe sinkt: Rotation 8 gewählt, neue Publikation mit Freigabe 2 → Auswahl verworfen
  sb.stand = staende.r8;
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&g=Boden&r=8"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Rotation 8 · Boden · Riege Startgerät Pferd"));
  pruefe((await rotationen()).length === 9, "Freigabe 8: Rotation 1–8 wählbar");
  await page.screenshot({ path: `${bilder}/mobil-geraet-rotation.png`, fullPage: true });
  sb.stand = staende.r2;
  await page.waitForFunction(() => document.getElementById("rotation").value === "" && document.getElementById("inhalt").textContent.includes("Rotation wählen"),
    null, { timeout: 15000 });
  pruefe(!params().has("r") && !(await text()).includes("Max"), "Freigabe sinkt 8 → 2: Rotation 8 verworfen (auch aus der Adresse)");
  // Freigabe 0 und ältere Publikation ohne Einteilung
  sb.stand = staende.r0;
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&r=1"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("noch keine Rotation freigegeben"));
  pruefe(await page.isDisabled("#rotation") && !params().has("r") && !(await text()).includes("Lea"), "Freigabe 0: keine Rotation wählbar");
  sb.stand = staende.ohne_struktur;
  await page.goto(adresse("&ansicht=geraet&p=AK5-AK9%2B&r=1"));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("mit der nächsten Publikation verfügbar"));
  pruefe(!(await text()).includes("Lea"), "ältere Publikation ohne Einteilung: Hinweis statt geratener Einteilung");
  // Korrektur während der Anzeige: offene Karte bleibt offen
  sb.stand = staende.r8;
  // frühere Adresse „Gerät & Rotation“ öffnet „Gerät“ und wird still umgeschrieben
  await page.goto(adresse("&ansicht=rangliste&p=AK5-AK9%2B&art=technik&sicht=rotation&g=Boden&r=1"));
  await page.waitForSelector("#inhalt .tt-rangkarte");
  pruefe(params().get("ansicht") === "geraet" && !params().has("art") && !params().has("sicht") && params().get("r") === "1",
    `frühere Adresse → ${new URL(page.url()).search}`);
  await page.locator(".tt-rangkarte summary").first().click();
  sb.stand = staende.korrektur;
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("T 9"), null, { timeout: 15000 });
  pruefe(await page.locator("#inhalt details[open]").count() === 1, "Korrektur sichtbar, geöffnete Karte bleibt offen");
  // Wechsel Gerät ⇄ Ranglisten über die Pillen, Wechsel zu Live ohne Rotationsparameter
  await page.click("[data-ansicht=rangliste]");
  await page.waitForSelector(".tt-rangliste .tt-rang");
  pruefe(params().get("ansicht") === "rangliste" && !params().has("r") && await page.locator("#filter2").isHidden()
    && await page.locator("#arten").isVisible(), "Ranglisten: Ebenen statt Gerät/Rotation");
  const vor = sb.anfragen.length;
  await page.click("[data-ansicht=geraet]");
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Riege Startgerät"));
  const neu = sb.anfragen.slice(vor);
  pruefe(neu.length <= 1 && neu.every((a) => a.teil === "zeilen"), `Wechsel lädt höchstens eine Anfrage (${neu.map((a) => `${a.teil}:${a.bereich}`).join(",")})`);
  pruefe(params().get("r") === "1" && params().get("ansicht") === "geraet", "gewählte Rotation bleibt beim Wechsel erhalten");
  await page.click("[data-ansicht=live]");
  await page.waitForSelector(".tt-liste .tt-karte");
  pruefe(["art", "sicht", "g", "r"].every((k) => !params().has(k)) && await page.locator("#filter2").isHidden(), "Live: ohne Rotationsauswahl");
  await ctx.close();
}

// ── 1c. Startliste und Zeitplan: Wechsel, Filter, Zurück, 30-s-Takt, Änderung, Unterbruch, verborgen, Archiv ──
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  sb.stand = staende.r8; sb.status = "live"; sb.sichtbar = true; sb.netz = true;
  await page.goto(adresse("&p=AK9-AK12"));
  await page.waitForSelector(".tt-liste .tt-karte");
  await page.evaluate(() => { window.__ohneNeuladen = true; });
  let vor = sb.anfragen.length;
  await page.click("[data-ansicht=startliste]");
  await page.waitForSelector(".tt-startliste");
  let neu = sb.anfragen.slice(vor);
  pruefe(neu.length === 1 && neu[0].teil === "zeilen" && neu[0].bereich === "eq.startliste",
    `Live → Startliste: eine Anfrage (${JSON.stringify(neu.map((a) => `${a.teil}:${a.bereich}`))})`);
  pruefe(await page.evaluate(() => window.__ohneNeuladen === true) && page.url().includes("ansicht=startliste")
    && page.url().includes("p=AK9-AK12"), `Startliste ohne Neuladen, Adresse ${new URL(page.url()).search}`);
  pruefe(await page.inputValue("#programm") === "AK9-AK12" && await page.locator("#ak").isHidden()
    && await page.locator(".tt-programm").allTextContents().then((t) => t.join() === "AK9–AK12"), "Programmfilter übernommen, nur AK9–AK12, ohne AK-Feld");
  await page.selectOption("#programm", "Alle");
  await bereit(page);
  pruefe(await page.locator(".tt-programm").allTextContents().then((t) => t.join("|") === "AK5–AK9+|AK9–AK12") && !page.url().includes("p="),
    "Startliste „Alle“: beide Programme wie Streamlit");
  pruefe(await breite(page), "Smartphone: Startliste ohne waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/mobil-startliste.png`, fullPage: true });
  vor = sb.anfragen.length;
  await page.click("[data-ansicht=zeitplan]");
  await page.waitForSelector(".tt-zeitplan");
  neu = sb.anfragen.slice(vor);
  pruefe(neu.length === 1 && neu[0].bereich === "eq.zeitplan" && await page.locator("#filter").isHidden(), "Startliste → Zeitplan: eine Anfrage, ohne Filter");
  pruefe(await breite(page), "Smartphone: Zeitplan ohne waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/mobil-zeitplan.png`, fullPage: true });
  // 30-s-Takt: in 25 s höchstens eine Änderungsabfrage
  vor = sb.anfragen.length;
  await warte(25000);
  const abfragen = sb.anfragen.slice(vor).filter((a) => a.teil === "pruefung").length;
  pruefe(abfragen <= 1, `Zeitplan: Änderungsabfrage im 30-s-Takt (${abfragen} in 25 s)`);
  pruefe(await page.locator("#warnung").isHidden(), "kein Veraltet-Hinweis zwischen zwei 30-s-Prüfungen");
  await page.goBack();
  await page.waitForSelector(".tt-startliste");
  await page.goBack();
  await page.waitForSelector(".tt-liste .tt-karte");
  pruefe(!page.url().includes("ansicht="), "Zurück-Taste: Zeitplan → Startliste → Live");
  vor = sb.anfragen.length;
  await warte(6000);
  pruefe(sb.anfragen.slice(vor).some((a) => a.teil === "pruefung"), "zurück auf Live: wieder 5-s-Takt");
  // Änderung der Startliste während der Anzeige (neuer Stand)
  await page.click("[data-ansicht=startliste]");
  await page.waitForSelector(".tt-startliste");
  sb.stand = staende.gross;
  const t0 = Date.now();
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("Synthname"), null, { timeout: 45000 });
  pruefe(true, `Startliste aktualisiert nach Publikation (${Math.round((Date.now() - t0) / 1000)} s, Takt 30 s)`);
  // Unterbruch
  sb.netz = false; await ctx.setOffline(true);
  await page.waitForSelector("#warnung:not([hidden])", { timeout: 80000 });
  pruefe(await page.locator(".tt-startliste").count() > 0, "Startliste offline: Hinweis, letzter Stand bleibt");
  sb.netz = true; await ctx.setOffline(false);
  await page.waitForSelector("#warnung", { state: "hidden", timeout: 40000 });
  pruefe(true, "Startliste wieder online");
  // verborgen
  sb.sichtbar = false;
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("nicht verfügbar"), null, { timeout: 45000 });
  pruefe(!(await page.content()).includes("Synthname") && await page.locator("#nav").isHidden(), "Startliste verborgen: keine Namen mehr im DOM");
  sb.sichtbar = true;
  await ctx.close();
}
for (const ansicht of ["startliste", "zeitplan"]) {  // Archiv: Link ins Streamlit-Archiv derselben Ansicht
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true);
  sb.status = "archiviert";
  await page.goto(adresse(`&ansicht=${ansicht}`));
  await page.waitForFunction(() => document.getElementById("inhalt").textContent.includes("abgeschlossen"));
  pruefe(await page.getAttribute("#inhalt a", "href") === `${STREAMLIT}/?t=${SLUG}&ansicht=${ansicht}`, `Archiv aus ${ansicht}: Link ins Streamlit-Archiv`);
  sb.status = "live";
  await ctx.close();
}

// ── 2. Alle Ranglisten aller Stände zeichnen (Vergleich mit Streamlit danach) ────────────────────────────
{
  const { ctx, page } = await kontext({ width: 1280, height: 900 }, false);
  for (const s of daten.staende) {
    sb.stand = s;
    gezeichnet[s.name] = {};
    const optionenOk = [];
    for (const k of Object.keys(s.listen)) {
      const [p, ak, art] = k.split("|");
      await page.goto(adresse(`&ansicht=rangliste&p=${encodeURIComponent(p)}&ak=${encodeURIComponent(ak)}&art=${art}`));
      await bereit(page);
      gezeichnet[s.name][k] = await page.innerHTML("#inhalt");
      const programme = [...new Set(Object.keys(s.listen).map((x) => x.split("|")[0]))].map(programmText);
      const aks = [...new Set(Object.keys(s.listen).filter((x) => x.startsWith(`${p}|`)).map((x) => x.split("|")[1]))];
      optionenOk.push(JSON.stringify(await page.locator("#programm option").allTextContents()) === JSON.stringify(programme)
        && JSON.stringify(await page.locator("#ak option").allTextContents()) === JSON.stringify(aks)
        && await page.inputValue("#ak") === ak);
    }
    pruefe(optionenOk.every(Boolean), `${s.name}: ${optionenOk.length} Ranglisten gezeichnet, Auswahlfelder wie Streamlit`);
    for (const k of Object.keys(s.ansichten)) {
      const [a, p] = k.split("|");
      await page.goto(adresse(`&ansicht=${a}${p && p !== "Alle" ? `&p=${encodeURIComponent(p)}` : ""}`));
      await bereit(page);
      gezeichnet[s.name][`ansicht:${k}`] = await page.innerHTML("#inhalt");
    }
  }
  await page.goto(adresse("&ansicht=startliste"));
  await bereit(page);
  pruefe(await breite(page), "Desktop: Startliste ohne waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/desktop-startliste.png`, fullPage: true });
  // Desktop-Bildschirmfotos und Wechsel von Live mit „Alle“ (erste AK, Live-Auswahl bleibt)
  sb.stand = staende.korrektur;
  await page.goto(adresse());
  await page.waitForSelector(".tt-karte");
  await page.click("[data-ansicht=rangliste]");
  await page.waitForSelector(".tt-rangliste");
  const erste = Object.keys(staende.korrektur.listen)[0].split("|");
  pruefe(await page.inputValue("#programm") === erste[0] && await page.inputValue("#ak") === erste[1] && !page.url().includes("&p="),
    `Live „Alle“ → Rangliste zeigt erstes Programm/erste AK (${erste[0]} ${erste[1]}), Adresse ohne Auswahl`);
  await page.selectOption("#programm", "AK9-AK12");
  await bereit(page);
  pruefe(page.url().includes("p=AK9-AK12") && !page.url().includes("ak="), "Programmwechsel: erste AK, Adresse nur mit Programm");
  await page.locator(".tt-rangkarte summary").first().click();
  pruefe(await breite(page), "Desktop: keine waagrechte Scrollbar");
  await page.screenshot({ path: `${bilder}/desktop-rangliste.png`, fullPage: true });
  await page.click("[data-ansicht=live]");
  await page.waitForSelector(".tt-liste .tt-karte");
  pruefe(await page.inputValue("#programm") === "AK9-AK12" && await page.inputValue("#ak") === "Alle", "Rangliste → Live: Programm bleibt, AK Alle");
  await ctx.close();
}

// ── 3. Schalter: Ansichten in Streamlit (ansichten ohne rangliste bzw. ohne startliste/zeitplan) und Rückfall ───
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true, { rueckfall: false, ansichten: ["rangliste"] });
  sb.stand = staende.korrektur;
  await page.goto(adresse());
  await page.waitForSelector(".tt-karte");
  for (const z of ["startliste", "zeitplan"]) {
    pruefe(await page.getAttribute(`[data-ziel=${z}]`, "href") === `${STREAMLIT}/?t=${SLUG}&ansicht=${z}`, `ansichten ohne ${z}: Pille führt nach Streamlit`);
  }
  const vor = sb.anfragen.length;
  await page.goto(adresse("&ansicht=zeitplan"));
  await page.waitForURL((x) => x.href.startsWith(STREAMLIT));
  pruefe(page.url() === `${STREAMLIT}/?t=${SLUG}&ansicht=zeitplan` && sb.anfragen.length === vor, "&ansicht=zeitplan leitet ohne Datenabruf nach Streamlit");
  await ctx.close();
}
{
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true, { rueckfall: false, ansichten: ["startliste", "zeitplan"] });
  sb.stand = staende.korrektur;
  await page.goto(adresse());
  await page.waitForSelector(".tt-karte");
  pruefe(await page.getAttribute("nav a:has-text('Ranglisten')", "href") === `${STREAMLIT}/?t=${SLUG}&ansicht=rangliste`,
    "Ranglisten nicht in ansichten – Pille führt in Streamlit-Rangliste");
  pruefe(await page.locator("nav a:has-text('Gerät')").count() === 0, "Ranglisten nicht in ansichten – keine Pille Gerät");
  const vor = sb.anfragen.length;
  await page.goto(adresse("&ansicht=rangliste"));
  await page.waitForURL((x) => x.href.startsWith(STREAMLIT));
  pruefe(page.url() === `${STREAMLIT}/?t=${SLUG}&ansicht=rangliste` && sb.anfragen.length === vor,
    "Ranglisten nicht in ansichten – &ansicht=rangliste leitet ohne Datenabruf nach Streamlit");
  await ctx.close();
}
for (const [extra, ziel] of [["&ansicht=rangliste&ak=AK+7", "rangliste"], ["&ansicht=startliste&p=AK9-AK12", "startliste"],
  ["&ansicht=zeitplan", "zeitplan"], ["", "live"]]) {
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true, { rueckfall: true, ansichten: ["rangliste", "startliste", "zeitplan"] });
  const vor = sb.anfragen.length;
  await page.goto(adresse(extra));
  await page.waitForURL((x) => x.href.startsWith(STREAMLIT));
  pruefe(page.url() === `${STREAMLIT}/?t=${SLUG}&ansicht=${ziel}` && sb.anfragen.length === vor, `Rückfall → Streamlit ${ziel} ohne Datenabruf`);
  await ctx.close();
}
// Rückfall aus „Gerät“: Streamlit erhält Technik + sicht=rotation (dort Hinweis), Gerät/Rotation entfallen
for (const [extra, zusatz, config] of [
  ["&ansicht=geraet&p=AK5-AK9%2B&g=Boden&r=2", "&art=technik&sicht=rotation", { rueckfall: true, ansichten: ["rangliste", "startliste", "zeitplan"] }],
  ["&ansicht=geraet&g=Boden&r=2", "&art=technik&sicht=rotation", { rueckfall: false, ansichten: ["startliste", "zeitplan"] }],
  ["&ansicht=rangliste&p=AK5-AK9%2B&art=technik&sicht=rotation&g=Boden&r=2", "&art=technik&sicht=rotation", { rueckfall: true, ansichten: ["rangliste", "startliste", "zeitplan"] }],
  ["&ansicht=rangliste&art=technik&sicht=rotation&g=Boden&r=2", "&art=technik&sicht=rotation", { rueckfall: false, ansichten: ["startliste", "zeitplan"] }],
  ["&ansicht=rangliste&art=athletik&sicht=rotation", "&art=athletik", { rueckfall: true, ansichten: ["rangliste"] }],
  ["&ansicht=rangliste&art=<x>&sicht=rotation", "", { rueckfall: true, ansichten: ["rangliste"] }]]) {
  const { ctx, page } = await kontext({ width: 390, height: 844 }, true, config);
  const vor = sb.anfragen.length;
  await page.goto(adresse(extra));
  await page.waitForURL((x) => x.href.startsWith(STREAMLIT));
  pruefe(page.url() === `${STREAMLIT}/?t=${SLUG}&ansicht=rangliste${zusatz}` && sb.anfragen.length === vor,
    `Rückfall ${extra} → Streamlit-Rangliste${zusatz || " (ohne Ebene)"} ohne Datenabruf`);
  await ctx.close();
}
await browser.close();
pruefe(sb.anfragen.every((a) => a.key && a.key.startsWith("sb_publishable_")), `nur Publishable Key (${sb.anfragen.length} Anfragen)`);
writeFileSync(ausgabe, JSON.stringify(gezeichnet));
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
