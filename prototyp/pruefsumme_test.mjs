// Prüft die Prüfsummen-Portierung in docs/live.js gegen echte Zeilen (JSON-Datei mit [{pruefsumme, inhalt}])
// und Randfälle der Zahlendarstellung gegen erwartete Python-Ausgaben.
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
const src = readFileSync(new URL("../docs/live.js", import.meta.url), "utf8");
const teil = src.slice(src.indexOf("  function pyZahl"), src.indexOf("  async function sha256"));
const QuellenFehler = class extends Error {};
const { pyZahl, kanonisch } = new Function("QuellenFehler", `${teil}; return { pyZahl, kanonisch };`)(QuellenFehler);
const faelle = { "0.1": 0.1, "1e-05": 1e-5, "0.0001": 1e-4, "9.25": 9.25, "12": 12, "-3": -3, "1.5e-07": 1.5e-7,
  "123456.789": 123456.789, "0": 0 };
let fehler = 0;
for (const [erwartet, x] of Object.entries(faelle)) if (pyZahl(x) !== erwartet) { fehler++; console.log("Zahl", x, pyZahl(x), erwartet); }
const zeilen = JSON.parse(readFileSync(process.argv[2], "utf8"));
let ok = 0;
for (const z of zeilen) {
  const s = createHash("sha256").update(kanonisch(z.inhalt), "utf8").digest("hex");
  if (s === z.pruefsumme) ok++; else { fehler++; console.log("Abweichung", z.bereich, z.programm, z.ak); }
}
console.log(`Zeilen ${ok}/${zeilen.length} identisch, Fehler ${fehler}`);
process.exit(fehler ? 1 : 0);
