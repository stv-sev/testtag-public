// Navigationstest statische Seite (Live, Ranglisten) ⇄ Streamlit (Startliste, Zeitplan) in Headless-Chromium (nur
// lesend, synthetischer Testtag). Streamlit läuft mit den Secrets `live_url` und `statische_rangliste = true`.
//   node navigation_test.mjs <statisch-url> <streamlit-url> <slug> <chromium-exe> <bildordner>
// Prüft in Smartphone- und Desktopbreite: Links in beiden Richtungen (gleicher Tab, gleicher Testtag, Bereich über
// &ansicht=), Live ⇄ Rangliste ohne Neuladen mit gleicher Auswahl, Übersicht → Live, alte ?t=-Links, unbekannter
// Testtag, keine waagrechte Scrollbar; Bildschirmfotos.
import { chromium } from "playwright-core";

const [statisch, streamlit, slug, exe, bilder] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: exe });
const fehler = [];
const pruefe = (bedingung, text) => { console.log(`${bedingung ? "ok  " : "FEHL"} ${text}`); if (!bedingung) fehler.push(text); };
const ohneSlash = (u) => u.replace(/\/+$/, "");
const live = `${ohneSlash(statisch)}/?t=${encodeURIComponent(slug)}`;
const rangliste = `${live}&ansicht=rangliste`;
const st = (ansicht) => `${ohneSlash(streamlit)}/?t=${encodeURIComponent(slug)}${ansicht ? `&ansicht=${ansicht}` : ""}`;
const pfad = (u) => { const x = new URL(u, statisch); return `${x.origin}${x.pathname.replace(/\/+$/, "")}${x.search}`; };
const gleich = (a, b) => pfad(a) === pfad(b);
const stBereit = (page) => page.waitForSelector(".tt-kopf", { timeout: 60000 });
const ohneScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

for (const [name, viewport] of [["mobil", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 900 }]]) {
  console.log(`── ${name} ${viewport.width}×${viewport.height}`);
  const ctx = await browser.newContext({ viewport, isMobile: name === "mobil", hasTouch: name === "mobil" });
  const page = await ctx.newPage();
  let neueTabs = 0;
  ctx.on("page", () => { neueTabs += 1; });

  // 1. Statische Live-Ansicht mit Filter
  await page.goto(`${live}&p=AK5-AK9%2B`);
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  for (const ziel of ["startliste", "zeitplan"]) {
    pruefe(gleich(await page.getAttribute(`[data-ziel=${ziel}]`, "href"), st(ziel)), `statisch → Streamlit ${ziel}`);
  }
  pruefe(gleich(await page.getAttribute("#zurueck", "href"), `${ohneSlash(streamlit)}/`), "statisch ← Alle Testtage");
  pruefe(await ohneScroll(page), "statisch Live ohne waagrechtes Scrollen");
  await page.screenshot({ path: `${bilder}/${name}-1-statisch-live.png` });

  // 2. → Ranglisten auf derselben Seite (Auswahl bleibt)
  await page.evaluate(() => { window.__gleicheSeite = true; });
  await page.click("[data-ansicht=rangliste]");
  await page.waitForSelector(".tt-rangliste .tt-rangkarte", { timeout: 30000 });
  pruefe(await page.evaluate(() => window.__gleicheSeite === true), "Live → Ranglisten ohne Neuladen");
  pruefe(gleich(page.url(), `${live}&p=AK5-AK9%2B&ansicht=rangliste`), `Adresse ${page.url()}`);
  pruefe(await page.inputValue("#programm") === "AK5-AK9+", "Programm bleibt");
  await page.locator(".tt-rangkarte summary").first().click();
  pruefe(await ohneScroll(page), "statisch Rangliste ohne waagrechtes Scrollen");
  await page.screenshot({ path: `${bilder}/${name}-2-statisch-rangliste.png` });

  // 3. → Startliste in Streamlit (gleicher Tab); dort Live und Ranglisten als Links auf die statische Seite
  await page.click("[data-ziel=startliste]");
  await page.waitForURL((u) => u.href.startsWith(ohneSlash(streamlit)), { timeout: 30000 });
  await stBereit(page);
  await page.waitForSelector(".tt-startliste, .tt-leer", { timeout: 60000 });
  pruefe(gleich(page.url(), st("startliste")), `Streamlit-Adresse ${page.url()}`);
  pruefe((await page.locator(".st-key-bereich label:has(input:checked)").textContent()).includes("Startliste"), "Pille Startliste aktiv");
  const links = await page.$$eval(".st-key-nav a.tt-pille", (as) => as.map((a) => [a.textContent, a.getAttribute("href")]));
  pruefe(links.length === 2 && gleich(links[0][1], live) && gleich(links[1][1], rangliste)
    && links[0][0].includes("Live") && links[1][0].includes("Ranglisten"), `Streamlit → statisch: ${JSON.stringify(links)}`);
  pruefe(await ohneScroll(page), "Streamlit ohne waagrechtes Scrollen");
  await page.screenshot({ path: `${bilder}/${name}-3-streamlit-startliste.png` });

  // 4. Streamlit-intern Zeitplan – Adresse folgt
  await page.click('.st-key-bereich label:has-text("Zeitplan")');
  await page.waitForSelector(".tt-zeitplan", { timeout: 30000 });
  await page.waitForFunction(() => location.search.includes("ansicht=zeitplan"), null, { timeout: 10000 }).catch(() => {});
  pruefe(gleich(page.url(), st("zeitplan")), `Streamlit Zeitplan: ${page.url()}`);

  // 5. ← Ranglisten (statisch), dann Live (statisch)
  await page.click(".st-key-nav a.tt-pille:has-text('Ranglisten')");
  await page.waitForURL((u) => u.href.startsWith(ohneSlash(statisch)), { timeout: 30000 });
  await page.waitForSelector(".tt-rangliste .tt-rangkarte", { timeout: 30000 });
  pruefe(gleich(page.url(), rangliste), `Streamlit → Ranglisten: ${page.url()}`);
  await page.click("[data-ansicht=live]");
  await page.waitForSelector(".tt-liste .tt-karte", { timeout: 30000 });
  pruefe(gleich(page.url(), live), `Ranglisten → Live: ${page.url()}`);

  // 6. Übersicht → Live-Resultate; alter Link und &ansicht=rangliste in Streamlit → Hinweis → statisch
  await page.goto(`${ohneSlash(streamlit)}/`);
  await stBereit(page);
  const pille = page.locator(`.tt-meta a.tt-pille[href*="t=${slug}"]`);
  await pille.waitFor({ timeout: 60000 });
  await pille.click();
  await page.waitForSelector(".tt-karte", { timeout: 30000 });
  pruefe(gleich(page.url(), live), `Übersicht → Live: ${page.url()}`);
  for (const [ansicht, ziel, text] of [[null, live, "Live-Resultate öffnen"], ["rangliste", rangliste, "Ranglisten öffnen"]]) {
    await page.goto(st(ansicht));
    await stBereit(page);
    await page.waitForSelector(".tt-verweis", { timeout: 60000 });
    pruefe((await page.textContent(".tt-verweis")).includes(text) && await page.locator(".tt-karte").count() === 0,
      `Streamlit ${ansicht || "ohne ansicht"}: Hinweis „${text}“, keine Personendaten`);
    if (ansicht) await page.screenshot({ path: `${bilder}/${name}-4-streamlit-hinweis-rangliste.png` });
    await page.click(".tt-verweis a.tt-pille");
    await page.waitForSelector(".tt-karte", { timeout: 30000 });
    pruefe(gleich(page.url(), ziel), `Hinweis → ${page.url()}`);
  }
  await page.click("#zurueck");
  await stBereit(page);
  pruefe(gleich(page.url(), `${ohneSlash(streamlit)}/`), `statisch ← Alle Testtage: ${page.url()}`);

  // 7. Unbekannter Testtag
  await page.goto(`${ohneSlash(statisch)}/?t=gibt-es-nicht&ansicht=rangliste`);
  await page.waitForSelector(".tt-leer:has-text('nicht verfügbar')", { timeout: 30000 });
  pruefe(await page.locator("#nav").isHidden() && await page.locator("#arten").isHidden(), "statisch unbekannt: keine Navigation");
  await page.goto(`${ohneSlash(streamlit)}/?t=gibt-es-nicht&ansicht=rangliste`);
  await page.waitForSelector("text=Dieser Testtag ist nicht verfügbar.", { timeout: 60000 });
  pruefe(await page.locator(".tt-pille, .tt-karte").count() === 0, "Streamlit unbekannt: kein Link, keine Daten");
  pruefe(neueTabs === 0, `alles im selben Tab (neue Tabs: ${neueTabs})`);
  await ctx.close();
}
await browser.close();
console.log(fehler.length ? `${fehler.length} Fehler` : "alle Prüfungen bestanden");
process.exit(fehler.length ? 1 : 0);
