// Lastmessung Ansichtswechsel auf der statischen Seite: N Zuschauer in Headless-Chromium (je eigener Browserkontext)
// wechseln innerhalb weniger Sekunden von Live zur Rangliste und bleiben dort. Nur lesend.
//
//   node wechsel_statisch.mjs <seiten-url> <slug> <anzahl> <sekunden> <log> <chromium-exe> klick|direkt
//
// klick:  alle öffnen zuerst Live (gestaffelt, 300 ms), nach 40 s tippen alle innerhalb von 2,5 s auf „Ranglisten“.
// direkt: alle öffnen innerhalb von 2,5 s den Link ?t=<slug>&ansicht=rangliste (kalter Seitenaufruf).
// Protokolliert (JSON-Zeilen, ohne Personendaten): Klick/Seitenaufruf, erste Rangliste (Dauer), jede Anzeige (Ansicht,
// neuestes Resultat der AK, Prüfsumme der Ranglistenzeile), jede Supabase-Antwort mit Bytes, Fehler, Ressourcen.
import { appendFileSync } from "node:fs";
import os from "node:os";
import { chromium } from "playwright-core";

const [SEITE, SLUG, N, DAUER, LOG, EXE, MODUS] = [process.argv[2], process.argv[3], Number(process.argv[4]),
  Number(process.argv[5]), process.argv[6], process.argv[7], process.argv[8] || "klick"];
const log = (daten) => appendFileSync(LOG, `${JSON.stringify({ t: Date.now() / 1000, ...daten })}\n`);
const start = Date.now();
const ende = start + DAUER * 1000;
const WECHSEL = start + 40000;  // klick: Zeitpunkt des gemeinsamen Wechsels

const browser = await chromium.launch({ executablePath: EXE, args: [
  "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
  "--disable-backgrounding-occluded-windows", "--renderer-process-limit=12"] });
log({ art: "start", anzahl: N, seite: SEITE, modus: MODUS, wechsel: MODUS === "klick" ? WECHSEL / 1000 : start / 1000 });
const warte = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

async function sitzung(nr) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  let t0 = null;
  let erste = false;
  await page.exposeFunction("__ttAnzeige", (i) => {
    log({ art: "anzeige", sitzung: nr, ts: i.t / 1000, ansicht: i.ansicht, n: i.n, pruef: (i.pruef || "").slice(0, 12),
      erster: i.erster ? i.erster.split("|").slice(1).join("|") : "" });
    if (!erste && t0 !== null && i.ansicht === "rangliste" && i.n > 0) {
      erste = true;
      log({ art: "rangliste", sitzung: nr, dauer: i.t / 1000 - t0 / 1000 });
    }
  });
  await page.exposeFunction("__ttEreignis", (i) => log({ art: "seitenfehler", sitzung: nr, code: i.code }));
  page.on("requestfinished", async (r) => {
    if (!r.url().includes(".supabase.co/")) return;
    try {
      const s = await r.sizes();
      const antwort = await r.response();
      const u = new URL(r.url());
      log({ art: "req", sitzung: nr, teil: u.searchParams.has("pruefsumme") ? "zeilen" : u.pathname.endsWith("testtage")
        ? "pruefung" : "koepfe", bereich: u.searchParams.get("bereich"), status: antwort ? antwort.status() : 0,
        antwort: s.responseBodySize + s.responseHeadersSize, inhalt: s.responseBodySize,
        ms: Math.round(r.timing().responseEnd) });
    } catch { /* Seite geschlossen */ }
  });
  page.on("requestfailed", (r) => {
    if (r.url().includes(".supabase.co/")) log({ art: "req_fehler", sitzung: nr, text: r.failure()?.errorText });
  });
  page.on("pageerror", (e) => log({ art: "js_fehler", sitzung: nr, text: String(e.message).slice(0, 200) }));
  try {
    if (MODUS === "klick") {
      await warte(nr * 300);
      await page.goto(`${SEITE}?t=${encodeURIComponent(SLUG)}`, { timeout: 60000 });
      await page.waitForSelector(".tt-liste .tt-karte", { timeout: 60000 });
      log({ art: "live", sitzung: nr });
      await warte(WECHSEL + Math.random() * 2500 - Date.now());
      t0 = Date.now();
      log({ art: "klick", sitzung: nr });
      await page.click("[data-ansicht=rangliste]");
    } else {
      await warte(Math.random() * 2500);
      t0 = Date.now();
      log({ art: "klick", sitzung: nr });
      await page.goto(`${SEITE}?t=${encodeURIComponent(SLUG)}&ansicht=rangliste`, { timeout: 60000 });
    }
    await page.waitForSelector(".tt-rangliste .tt-rangkarte", { timeout: 120000 });
  } catch (e) {
    log({ art: "rangliste_fehler", sitzung: nr, text: String(e.message).slice(0, 120) });
  }
  while (Date.now() < ende) await warte(1000);
  log({ art: "ende", sitzung: nr });
  await ctx.close();
}

let cpuAlt = os.cpus().map((c) => c.times);
let lagT = Date.now();
const lagUhr = setInterval(() => { lagT = Date.now(); }, 100);
const ressourcen = setInterval(() => {
  const neu = os.cpus().map((c) => c.times);
  let leer = 0, total = 0;
  neu.forEach((c, i) => { const a = cpuAlt[i]; const d = (k) => c[k] - a[k];
    leer += d("idle"); total += d("user") + d("nice") + d("sys") + d("idle") + d("irq"); });
  cpuAlt = neu;
  log({ art: "ressourcen", cpu_system: Math.round(100 * (1 - leer / total)), ram_frei_mb: Math.round(os.freemem() / 2 ** 20),
    loop_lag: (Date.now() - lagT) / 1000 });
}, 10000);

await Promise.all(Array.from({ length: N }, (_, i) => sitzung(i)));
clearInterval(ressourcen); clearInterval(lagUhr);
await browser.close();
