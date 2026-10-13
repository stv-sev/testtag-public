// Rückfalltest: statische Seite mit `rueckfall: true` und Streamlit ohne Secrets `live_url`/`statische_bereiche`
// (nur lesend).
//   node rueckfall_test.mjs <statisch-url> <streamlit-url> <slug> <chromium-exe>
// Erwartet: Umleitung vor jedem Supabase-Abruf auf Streamlit ?t=<slug>&ansicht=live (bzw. rangliste, startliste, zeitplan), dort die
// Ansicht wie bisher.
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
for (const [weg, ziel, bereich, sel] of [[`?t=${slug}&p=AK5-AK9%2B`, `?t=${slug}&ansicht=live`, "Live", ".tt-karte"],
  [`?t=${slug}&ansicht=rangliste&p=AK5-AK9%2B&art=technik`, `?t=${slug}&ansicht=rangliste`, "Ranglisten", ".tt-rangkarte"],
  [`?t=${slug}&ansicht=startliste&p=AK9-AK12`, `?t=${slug}&ansicht=startliste`, "Startliste", ".tt-startliste"],
  [`?t=${slug}&ansicht=zeitplan`, `?t=${slug}&ansicht=zeitplan`, "Zeitplan", ".tt-zeitplan"],
  ["", ""], ["?t=x%26y", ""]]) {
  vonStatisch.length = 0;
  await page.goto(`${statisch.replace(/\/+$/, "")}/${weg}`);
  await page.waitForURL((u) => u.href.startsWith(streamlit), { timeout: 30000 });
  await page.waitForSelector(".tt-kopf", { timeout: 60000 });
  const soll = `${streamlit.replace(/\/+$/, "")}/${ziel}`;
  pruefe(new URL(page.url()).search === new URL(soll).search, `Umleitung ${weg || "(ohne Testtag)"} → ${page.url()}`);
  pruefe(vonStatisch.length === 0, "kein Supabase-Abruf der statischen Seite vor der Umleitung");
  if (ziel) {
    await page.waitForSelector(sel, { timeout: 60000 });
    pruefe((await page.locator(".st-key-bereich label:has(input:checked)").textContent()).includes(bereich),
      `Streamlit zeigt ${bereich} wie bisher`);
    pruefe(await page.locator(".st-key-nav a.tt-pille, .tt-verweis").count() === 0, "kein Link zurück auf die statische Seite");
  }
}
await browser.close();
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
