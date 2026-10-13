// Funktionstest der statischen Live-Ansicht in Headless-Chromium (nur lesend).
//   node funktionstest.mjs <seiten-url> <slug> <chromium-exe>
// Prüft: erste Anzeige, Requests/Bytes, Filterwechsel (gezieltes Nachladen), Unterbruch (offline) mit Hinweis,
// Erholung, verborgener Testtag (simulierte leere Antwort) → keine Personendaten mehr im DOM.
import { chromium } from "playwright-core";
const [seite, slug, exe] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: exe });
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
const req = [];
let verbergen = false;
await page.route(/supabase\.co\/rest\/v1\/testtage/, (r) => verbergen
  ? r.fulfill({ status: 200, contentType: "application/json", body: "[]" }) : r.continue());
page.on("requestfinished", async (r) => {
  if (!r.url().includes("supabase.co")) return;
  const s = await r.sizes();
  const u = new URL(r.url());
  req.push({ tabelle: u.pathname.split("/").pop(), sel: (u.searchParams.get("select") || "").slice(0, 20),
    bytes: s.responseBodySize + s.responseHeadersSize, body: s.responseBodySize,
    auth: !!(await r.allHeaders())["authorization"], key: (u.searchParams.get("apikey") || "").slice(0, 15) });
});
const anzeigen = [];
await page.exposeFunction("__ttAnzeige", (i) => anzeigen.push(i));
const t0 = Date.now();
await page.goto(`${seite}?t=${encodeURIComponent(slug)}`);
await page.waitForSelector(".tt-karte", { timeout: 20000 });
console.log(`Erste Anzeige nach ${Date.now() - t0} ms; Karten ${await page.locator(".tt-karte").count()}; `
  + `Anzahl: ${await page.locator(".tt-anzahl").first().textContent()}`);
const zusammen = (liste) => { const m = {}; for (const r of liste) { const k = `${r.tabelle}:${r.sel}`;
  m[k] = m[k] || { n: 0, bytes: 0 }; m[k].n++; m[k].bytes += r.bytes; } return m; };
console.log("Start-Requests", JSON.stringify(zusammen(req)));
console.log("Schlüssel nur publishable:", req.every((r) => r.key === "sb_publishable_" && !r.auth));
const vorher = req.length;
await page.waitForTimeout(16000);
console.log("16 s ohne Änderung:", JSON.stringify(zusammen(req.slice(vorher))));
console.log("Stand-Zeile:", await page.locator("#stand").textContent());
console.log("Hinweise:", await page.locator("#hinweise").textContent());
const opts = await page.locator("#ak option").allTextContents();
console.log("AK-Optionen:", opts.join(", "));
const v2 = req.length;
await page.selectOption("#ak", opts[1]);
await page.waitForTimeout(1500);
console.log(`Filter ${opts[1]}: ${await page.locator(".tt-anzahl").first().textContent()}; neue Requests`,
  JSON.stringify(zusammen(req.slice(v2))));
const metas = await page.locator(".tt-text small").allTextContents();
console.log("Alle Karten aus AK:", metas.every((m) => m.endsWith(opts[1])));
console.log("Beispielkarte:", (await page.locator(".tt-karte").first().innerText()).replace(/\s+/g, " "));
await ctx.setOffline(true);
await page.waitForTimeout(20000);
console.log("Offline 20 s – Warnung sichtbar:", await page.locator("#warnung").isVisible(), "|",
  await page.locator("#warnung").textContent(), "| Karten weiter:", await page.locator(".tt-karte").count());
await ctx.setOffline(false);
await page.waitForTimeout(4000);
console.log("Wieder online 4 s – Warnung sichtbar:", await page.locator("#warnung").isVisible());
const name = await page.locator(".tt-text strong").first().textContent();
verbergen = true;
await page.waitForTimeout(7000);
const text = await page.locator("body").innerText();
console.log("Verborgen – Karten:", await page.locator(".tt-karte").count(), "| Meldung:",
  await page.locator("#inhalt").textContent(), "| Filter sichtbar:", await page.locator("#filter").isVisible());
console.log("Name der zuvor ersten Karte noch sichtbar:", text.includes(name), "| im HTML:", (await page.content()).includes(name));
await page.screenshot({ path: process.env.SCREENSHOT || "verborgen.png" });
await browser.close();
