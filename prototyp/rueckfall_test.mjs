// Rückfalltest: statische Seite mit `rueckfall: true` und Streamlit ohne Secret `live_url` (nur lesend).
//   node rueckfall_test.mjs <statisch-url> <streamlit-url> <slug> <chromium-exe>
// Erwartet: Umleitung vor jedem Supabase-Abruf auf Streamlit ?t=<slug>&ansicht=live, dort Live wie bisher.
import { chromium } from "playwright-core";

const [statisch, streamlit, slug, exe] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: exe });
const fehler = [];
const pruefe = (b, text) => { console.log(`${b ? "ok  " : "FEHL"} ${text}`); if (!b) fehler.push(text); };
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
const vonStatisch = [];
page.on("request", (r) => {
  if (r.url().includes("supabase.co") && page.url().startsWith(statisch.replace(/\/+$/, ""))) vonStatisch.push(r.url());
});
for (const [weg, ziel] of [[`?t=${slug}&p=AK5-AK9%2B`, `?t=${slug}&ansicht=live`], ["", ""], ["?t=x%26y", ""]]) {
  vonStatisch.length = 0;
  await page.goto(`${statisch.replace(/\/+$/, "")}/${weg}`);
  await page.waitForURL((u) => u.href.startsWith(streamlit), { timeout: 30000 });
  await page.waitForSelector(".tt-kopf", { timeout: 60000 });
  const soll = `${streamlit.replace(/\/+$/, "")}/${ziel}`;
  pruefe(new URL(page.url()).search === new URL(soll).search, `Umleitung ${weg || "(ohne Testtag)"} → ${page.url()}`);
  pruefe(vonStatisch.length === 0, "kein Supabase-Abruf der statischen Seite vor der Umleitung");
  if (ziel) {
    await page.waitForSelector(".tt-karte", { timeout: 60000 });
    pruefe((await page.locator(".st-key-bereich label:has(input:checked)").textContent()).includes("Live"),
      "Streamlit zeigt Live wie bisher");
    pruefe(await page.locator(".st-key-nav a.tt-pille, .tt-verweis").count() === 0, "kein Link zurück auf die statische Seite");
  }
}
await browser.close();
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
