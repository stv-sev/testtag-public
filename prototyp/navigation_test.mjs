// Navigationstest statische Live-Ansicht ⇄ Streamlit in Headless-Chromium (nur lesend, synthetischer Testtag).
//   node navigation_test.mjs <statisch-url> <streamlit-url> <slug> <chromium-exe> <bildordner>
// Prüft in Smartphone- und Desktopbreite: Links in beiden Richtungen (gleicher Tab, gleicher Testtag, Bereich über
// &ansicht=), Übersicht → Live, alte ?t=-Links, unbekannter Testtag, keine waagrechte Scrollbar; Bildschirmfotos.
import { chromium } from "playwright-core";

const [statisch, streamlit, slug, exe, bilder] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: exe });
const fehler = [];
const pruefe = (bedingung, text) => { console.log(`${bedingung ? "ok  " : "FEHL"} ${text}`); if (!bedingung) fehler.push(text); };
const ohneSlash = (u) => u.replace(/\/+$/, "");
const live = `${ohneSlash(statisch)}/?t=${encodeURIComponent(slug)}`;
const st = (ansicht) => `${ohneSlash(streamlit)}/?t=${encodeURIComponent(slug)}${ansicht ? `&ansicht=${ansicht}` : ""}`;
const pfad = (u) => { const x = new URL(u); return `${x.origin}${x.pathname.replace(/\/+$/, "")}${x.search}`; };
const gleich = (a, b) => pfad(a) === pfad(b);
const stBereit = (page) => page.waitForSelector(".tt-kopf", { timeout: 60000 });

async function breite(page) {
  return page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, fenster: window.innerWidth }));
}

for (const [name, viewport] of [["mobil", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 900 }]]) {
  console.log(`── ${name} ${viewport.width}×${viewport.height}`);
  const ctx = await browser.newContext({ viewport, isMobile: name === "mobil", hasTouch: name === "mobil" });
  const page = await ctx.newPage();
  let neueTabs = 0;
  ctx.on("page", () => { neueTabs += 1; });

  // 1. Statische Live-Ansicht mit Filter
  await page.goto(`${live}&p=AK5-AK9%2B`);
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  const ziele = await page.$$eval("[data-ziel]", (as) => as.map((a) => [a.dataset.ziel, a.href]));
  for (const [ziel, href] of ziele) pruefe(gleich(href, st(ziel)), `statisch → ${ziel}: ${href}`);
  pruefe(gleich(await page.getAttribute("#zurueck", "href"), `${ohneSlash(streamlit)}/`), "statisch ← Alle Testtage");
  let b = await breite(page);
  pruefe(b.scroll <= b.fenster, `statisch ohne waagrechtes Scrollen (${b.scroll}/${b.fenster})`);
  await page.screenshot({ path: `${bilder}/${name}-1-statisch-live.png` });

  // 2. → Ranglisten in Streamlit (gleicher Tab)
  await page.click("[data-ziel=rangliste]");
  await page.waitForURL((u) => u.href.startsWith(ohneSlash(streamlit)), { timeout: 30000 });
  await stBereit(page);
  await page.waitForSelector(".tt-rangliste, .tt-leer", { timeout: 60000 });
  pruefe(gleich(page.url(), st("rangliste")), `Streamlit-Adresse ${page.url()}`);
  pruefe(await page.locator(".tt-rangliste .tt-rangkarte").count() > 0, "Rangliste des Testtags sichtbar");
  pruefe((await page.locator(".st-key-bereich label:has(input:checked)").textContent()).includes("Ranglisten"),
    "Pille Ranglisten aktiv");
  const liveHref = await page.getAttribute(".st-key-nav a.tt-pille", "href");
  pruefe(gleich(liveHref, live), `Streamlit → Live: ${liveHref}`);
  b = await breite(page);
  pruefe(b.scroll <= b.fenster, `Streamlit ohne waagrechtes Scrollen (${b.scroll}/${b.fenster})`);
  await page.screenshot({ path: `${bilder}/${name}-2-streamlit-rangliste.png` });

  // 3. Streamlit-intern Startliste, Zeitplan – Adresse folgt
  for (const [text, ansicht, sel] of [["Startliste", "startliste", ".tt-startliste"], ["Zeitplan", "zeitplan", ".tt-zeitplan"]]) {
    await page.click(`.st-key-bereich label:has-text("${text}")`);
    await page.waitForSelector(sel, { timeout: 30000 });
    await page.waitForFunction((a) => location.search.includes(`ansicht=${a}`), ansicht, { timeout: 10000 }).catch(() => {});
    pruefe(gleich(page.url(), st(ansicht)), `Streamlit ${text}: ${page.url()}`);
  }
  await page.screenshot({ path: `${bilder}/${name}-3-streamlit-zeitplan.png` });

  // 4. ← Live (gleicher Tab)
  await page.click(".st-key-nav a.tt-pille");
  await page.waitForURL((u) => u.href.startsWith(ohneSlash(statisch)), { timeout: 30000 });
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  pruefe(gleich(page.url(), live), `zurück auf Live: ${page.url()}`);

  // 5. Übersicht → Live-Resultate; Übersicht → Testtag → Hinweis → Live
  await page.goto(`${ohneSlash(streamlit)}/`);
  await stBereit(page);
  const pille = page.locator(`.tt-meta a.tt-pille[href*="t=${slug}"]`);
  await pille.waitFor({ timeout: 60000 });
  await page.screenshot({ path: `${bilder}/${name}-4-streamlit-uebersicht.png` });
  await pille.click();
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  pruefe(gleich(page.url(), live), `Übersicht → Live: ${page.url()}`);
  await page.goto(st(null));  // bestehender ?t=-Link
  await stBereit(page);
  await page.waitForSelector(".tt-verweis", { timeout: 60000 });
  pruefe(await page.locator(".tt-karte").count() === 0, "alter ?t=-Link: Hinweis, keine Personendaten in Streamlit");
  await page.screenshot({ path: `${bilder}/${name}-5-streamlit-hinweis.png` });
  await page.click(".tt-verweis a.tt-pille");
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  pruefe(gleich(page.url(), live), `Hinweis → Live: ${page.url()}`);
  await page.click("#zurueck");
  await stBereit(page);
  pruefe(gleich(page.url(), `${ohneSlash(streamlit)}/`), `statisch ← Alle Testtage: ${page.url()}`);

  // 6. Unbekannter Testtag
  await page.goto(`${ohneSlash(statisch)}/?t=gibt-es-nicht`);
  await page.waitForSelector(".tt-leer:has-text('nicht verfügbar')", { timeout: 30000 });
  pruefe(await page.locator("#nav").isHidden(), "statisch unbekannt: keine Navigation");
  await page.goto(`${ohneSlash(streamlit)}/?t=gibt-es-nicht&ansicht=rangliste`);
  await page.waitForSelector("text=Dieser Testtag ist nicht verfügbar.", { timeout: 60000 });
  pruefe(await page.locator(".tt-pille, .tt-karte").count() === 0, "Streamlit unbekannt: kein Link, keine Daten");
  pruefe(neueTabs === 0, `alles im selben Tab (neue Tabs: ${neueTabs})`);
  await ctx.close();
}
await browser.close();
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
