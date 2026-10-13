// Navigationstest statische Seite (Live, Ranglisten, Startliste, Zeitplan) ⇄ Streamlit (Übersicht, Archiv, alte Links)
// in Headless-Chromium (nur lesend, synthetischer Testtag). Streamlit läuft mit den Secrets `live_url` und
// `statische_bereiche = ["rangliste", "startliste", "zeitplan"]`.
//   node navigation_test.mjs <statisch-url> <streamlit-url> <slug> <chromium-exe> <bildordner>
// Prüft in Smartphone- und Desktopbreite: alle vier Ansichten ohne Neuladen mit gleicher Auswahl und Zurück-Taste,
// direkte Links, Übersicht → Live, alte ?t=-Links (je Ansicht) → statische Seite, unbekannter Testtag, gleicher Tab,
// keine waagrechte Scrollbar; Bildschirmfotos.
import { chromium } from "playwright-core";

const [statisch, streamlit, slug, exe, bilder] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: exe });
const fehler = [];
const pruefe = (bedingung, text) => { console.log(`${bedingung ? "ok  " : "FEHL"} ${text}`); if (!bedingung) fehler.push(text); };
const ohneSlash = (u) => u.replace(/\/+$/, "");
const live = `${ohneSlash(statisch)}/?t=${encodeURIComponent(slug)}`;
const statischAnsicht = (a) => (a === "live" ? live : `${live}&ansicht=${a}`);
const st = (ansicht) => `${ohneSlash(streamlit)}/?t=${encodeURIComponent(slug)}${ansicht ? `&ansicht=${ansicht}` : ""}`;
const pfad = (u) => { const x = new URL(u, statisch); return `${x.origin}${x.pathname.replace(/\/+$/, "")}${x.search}`; };
const gleich = (a, b) => pfad(a) === pfad(b);
const stBereit = (page) => page.waitForSelector(".tt-kopf", { timeout: 60000 });
const ohneScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const MERKMAL = { live: ".tt-liste .tt-karte", rangliste: ".tt-rangliste .tt-rangkarte", startliste: ".tt-startliste",
  zeitplan: ".tt-zeitplan" };
const TEXT = { live: "Live-Resultate öffnen", rangliste: "Ranglisten öffnen", startliste: "Startliste öffnen",
  zeitplan: "Zeitplan öffnen" };

for (const [name, viewport] of [["mobil", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 900 }]]) {
  console.log(`── ${name} ${viewport.width}×${viewport.height}`);
  const ctx = await browser.newContext({ viewport, isMobile: name === "mobil", hasTouch: name === "mobil" });
  const page = await ctx.newPage();
  let neueTabs = 0;
  ctx.on("page", () => { neueTabs += 1; });

  // 1. Statisch: Live → Ranglisten → Startliste → Zeitplan ohne Neuladen, Auswahl bleibt, Zurück-Taste
  await page.goto(`${live}&p=AK5-AK9%2B`);
  await page.waitForSelector(MERKMAL.live, { timeout: 30000 });
  pruefe(await page.locator("#nav [data-ziel]").count() === 0, "alle vier Pillen auf der statischen Seite");
  pruefe(gleich(await page.getAttribute("#zurueck", "href"), `${ohneSlash(statisch)}/`), "statisch ← Alle Testtage: eigene Übersicht");
  await page.evaluate(() => { window.__gleicheSeite = true; });
  for (const a of ["rangliste", "startliste", "zeitplan"]) {
    await page.click(`[data-ansicht=${a}]`);
    await page.waitForSelector(MERKMAL[a], { timeout: 30000 });
    pruefe(gleich(page.url(), `${live}&p=AK5-AK9%2B&ansicht=${a}`) && await page.evaluate(() => window.__gleicheSeite === true),
      `→ ${a} ohne Neuladen, Adresse ${new URL(page.url()).search}`);
    pruefe(await ohneScroll(page), `${a} ohne waagrechtes Scrollen`);
    await page.screenshot({ path: `${bilder}/${name}-${a}.png` });
  }
  for (const a of ["startliste", "rangliste", "live"]) {
    await page.goBack();
    await page.waitForSelector(MERKMAL[a], { timeout: 30000 });
  }
  pruefe(gleich(page.url(), `${live}&p=AK5-AK9%2B`) && await page.inputValue("#programm") === "AK5-AK9+",
    "Zurück-Taste bis Live, Programm bleibt");

  // 2. Direkte Links je Ansicht (neuer Seitenaufruf)
  for (const a of ["rangliste", "startliste", "zeitplan"]) {
    await page.goto(statischAnsicht(a));
    await page.waitForSelector(MERKMAL[a], { timeout: 30000 });
    pruefe((await page.getAttribute(`[data-ansicht=${a}]`, "class")).includes("aktiv"), `direkter Link ${a}`);
  }

  // 3. Statische Übersicht → Testtag; Streamlit: Übersicht → Live-Resultate; alte Links je Ansicht → statische Seite
  await page.click("#zurueck");
  await page.waitForSelector(".tt-testtag", { timeout: 30000 });
  pruefe(gleich(page.url(), `${ohneSlash(statisch)}/`) && await page.locator(`.tt-testtag[href="?t=${slug}"]`).count() === 1,
    `statisch ← Alle Testtage: eigene Übersicht mit dem Testtag (${page.url()})`);
  await page.screenshot({ path: `${bilder}/${name}-statisch-uebersicht.png`, fullPage: true });
  await page.click(`.tt-testtag[href="?t=${slug}"]`);
  await page.waitForSelector(MERKMAL.live, { timeout: 30000 });
  pruefe(gleich(page.url(), live), "statische Übersicht → Live");
  await page.goto(`${ohneSlash(streamlit)}/`);
  await stBereit(page);
  const pille = page.locator(`.tt-meta a.tt-pille[href*="t=${slug}"]`);
  await pille.waitFor({ timeout: 60000 });
  await page.screenshot({ path: `${bilder}/${name}-streamlit-uebersicht.png` });
  await pille.click();
  await page.waitForSelector(MERKMAL.live, { timeout: 30000 });
  pruefe(gleich(page.url(), live), `Übersicht → Live: ${page.url()}`);
  for (const a of ["live", "rangliste", "startliste", "zeitplan"]) {
    await page.goto(st(a === "live" ? null : a));
    await stBereit(page);
    await page.waitForSelector(".tt-verweis", { timeout: 60000 });
    const links = await page.$$eval(".st-key-nav a.tt-pille", (as) => as.map((x) => x.getAttribute("href")));
    pruefe((await page.textContent(".tt-verweis")).includes(TEXT[a]) && links.length === 4
      && await page.locator(".tt-karte, .tt-startliste, .tt-zeitplan, .stRadio").count() === 0,
      `Streamlit ?t=…${a === "live" ? "" : `&ansicht=${a}`}: Hinweis „${TEXT[a]}“, 4 Links, keine Daten`);
    if (a === "startliste") await page.screenshot({ path: `${bilder}/${name}-streamlit-hinweis.png` });
    pruefe(gleich(await page.getAttribute("a.tt-alle", "href"), `${ohneSlash(statisch)}/`), "Streamlit ← Alle Testtage: statische Übersicht");
    await page.click(".tt-verweis a.tt-pille");
    await page.waitForSelector(MERKMAL[a], { timeout: 30000 });
    pruefe(gleich(page.url(), statischAnsicht(a)), `Hinweis → ${page.url()}`);
  }

  // 4. Unbekannter Testtag
  await page.goto(`${ohneSlash(statisch)}/?t=gibt-es-nicht&ansicht=startliste`);
  await page.waitForSelector(".tt-leer:has-text('nicht verfügbar')", { timeout: 30000 });
  pruefe(await page.locator("#nav").isHidden() && await page.locator("#filter").isHidden(), "statisch unbekannt: keine Navigation, kein Filter");
  await page.goto(`${ohneSlash(streamlit)}/?t=gibt-es-nicht&ansicht=startliste`);
  await page.waitForSelector("text=Dieser Testtag ist nicht verfügbar.", { timeout: 60000 });
  pruefe(await page.locator(".tt-pille, .tt-karte").count() === 0, "Streamlit unbekannt: kein Link, keine Daten");
  pruefe(neueTabs === 0, `alles im selben Tab (neue Tabs: ${neueTabs})`);
  await ctx.close();
}
await browser.close();
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
