// Lasttest der statischen Live-Ansicht: N gleichzeitige Zuschauer in Headless-Chromium (je eigener Browserkontext
// = eigene Verbindungen, eigener Cache) gegen die ausgelieferte Seite. Nur lesend.
//
//   node lasttest.mjs <seiten-url> <slug> <anzahl> <sekunden> <log> <chromium-exe>
//
// Protokolliert (JSON-Zeilen): jede neue Anzeige der Live-Liste je Sitzung (Zeitpunkt nach dem Zeichnen, erster
// Eintrag), jede Supabase-Antwort mit übertragenen Bytes (Kopf + komprimierter Inhalt), fehlgeschlagene Anfragen,
// Fehlermeldungen der Seite und Ressourcen des Lastgenerators. Keine Personendaten im Protokoll.
import { appendFileSync } from "node:fs";
import os from "node:os";
import { chromium } from "playwright-core";

const [SEITE, SLUG, N, DAUER, LOG, EXE] = [process.argv[2], process.argv[3], Number(process.argv[4]),
  Number(process.argv[5]), process.argv[6], process.argv[7]];
const log = (daten) => appendFileSync(LOG, `${JSON.stringify({ t: Date.now() / 1000, ...daten })}\n`);
const ende = Date.now() + DAUER * 1000;

const browser = await chromium.launch({ executablePath: EXE, args: [
  "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
  "--disable-backgrounding-occluded-windows", "--renderer-process-limit=12"] });
log({ art: "start", anzahl: N, seite: SEITE });

async function sitzung(nr) {
  await new Promise((r) => setTimeout(r, nr * 300));  // gestaffelter Start wie echte Zuschauer
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.exposeFunction("__ttAnzeige", (i) => log({ art: "anzeige", sitzung: nr, ts: i.t / 1000, n: i.n,
    erster: i.erster ? i.erster.split("|").slice(1).join("|") : "", verborgen: !!i.verborgen }));
  await page.exposeFunction("__ttEreignis", (i) => log({ art: "seitenfehler", sitzung: nr, code: i.code }));
  page.on("requestfinished", async (r) => {
    if (!r.url().includes(".supabase.co/")) return;
    try {
      const s = await r.sizes();
      const antwort = await r.response();
      const u = new URL(r.url());
      log({ art: "req", sitzung: nr, tabelle: u.pathname.split("/").pop(),
        teil: u.searchParams.has("pruefsumme") ? "zeilen" : u.pathname.endsWith("testtage") ? "pruefung" : "koepfe",
        status: antwort ? antwort.status() : 0, antwort: s.responseBodySize + s.responseHeadersSize,
        inhalt: s.responseBodySize, anfrage: s.requestHeadersSize + s.requestBodySize,
        ms: Math.round(r.timing().responseEnd) });
    } catch { /* Seite geschlossen */ }
  });
  page.on("requestfailed", (r) => {
    if (r.url().includes(".supabase.co/")) log({ art: "req_fehler", sitzung: nr, text: r.failure()?.errorText });
  });
  page.on("pageerror", (e) => log({ art: "js_fehler", sitzung: nr, text: String(e.message).slice(0, 200) }));
  page.on("crash", () => log({ art: "absturz", sitzung: nr }));
  const t0 = Date.now();
  try {
    await page.goto(`${SEITE}?t=${encodeURIComponent(SLUG)}`, { timeout: 60000 });
    await page.waitForSelector(".tt-karte", { timeout: 60000 });
    log({ art: "erstanzeige", sitzung: nr, dauer: (Date.now() - t0) / 1000 });
  } catch (e) {
    log({ art: "erstanzeige_fehler", sitzung: nr, text: String(e.message).slice(0, 120) });
  }
  while (Date.now() < ende) await new Promise((r) => setTimeout(r, 1000));
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
