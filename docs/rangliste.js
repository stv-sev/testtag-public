/*
 * rangliste.js – Darstellung der Rangliste (Portierung von quelle.turner_details und app.rang_karte, ohne DOM).
 *
 * Reine Anzeige: Reihenfolge, Ränge und Totale stammen unverändert aus der Publikation (`eintraege`); es gibt keine
 * Rangberechnung. Gerätetotale sind wie in quelle.py Summen der veröffentlichten Linienpunkte (T) eines Geräts.
 * Fehlt ein vorgesehenes Resultat, steht „–“; eine Nullwertung bleibt 0. Passt die Aufschlüsselung nicht exakt zu den
 * veröffentlichten Totalen, wird keine Aufschlüsselung gezeigt. Ältere Publikationen ohne `struktur` leiten die
 * Gliederung aus den veröffentlichten Resultaten der AK ab. Gleiche Eingabe → gleicher Text wie app.py
 * (prototyp/rangliste_paritaet.py vergleicht beide).
 */
"use strict";
(() => {
  const ARTEN = ["gesamt", "technik", "athletik"];
  const ART_TEXT = { gesamt: "Gesamt", technik: "Technik", athletik: "Athletik" };
  const ATHLETIK = ["beweglichkeit", "kraft"];
  const ATHLETIK_TEXT = { beweglichkeit: "Beweglichkeit", kraft: "Kraft" };
  const RANG_FARBE = { 1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32" };
  const TOLERANZ = 1e-6;

  const h = (t) => String(t ?? "").replace(/[&<>"']/g, (z) => `&#${z.charCodeAt(0)};`);
  const istObjekt = (w) => w !== null && typeof w === "object" && !Array.isArray(w);
  const liste = (w) => (Array.isArray(w) ? w : []);
  const wahr = (w) => !(w === null || w === undefined || w === false || w === 0 || w === "" || Number.isNaN(w)
    || (Array.isArray(w) && !w.length) || (istObjekt(w) && !Object.keys(w).length));  // Python-Wahrheitswert

  // Zahl wie Python str(): ganzzahlig ohne Nachkommastellen, Exponentialform wie repr.
  function pyZahl(x) {
    if (Number.isInteger(x) && Math.abs(x) < 1e16) return String(x);
    const a = Math.abs(x);
    if (a !== 0 && (a < 1e-4 || a >= 1e16)) {
      const [m, e] = x.toExponential().split("e");
      const n = Number(e);
      return `${m}e${n < 0 ? "-" : "+"}${String(Math.abs(n)).padStart(2, "0")}`;
    }
    return String(x);
  }
  const zahl = (w) => (w === null || w === undefined ? "–" : typeof w === "number" && Number.isFinite(w) ? pyZahl(w)
    : typeof w === "boolean" ? (w ? "True" : "False") : String(w));
  const pyStr = (w) => (w === null || w === undefined ? "None" : typeof w === "number" ? pyZahl(w)
    : typeof w === "boolean" ? (w ? "True" : "False") : String(w));

  // float(w) wie Python (Zahl, Wahrheitswert, Zahl als Text); sonst Fehler → keine Aufschlüsselung.
  function pyFloat(w) {
    if (typeof w === "number") return w;
    if (typeof w === "boolean") return w ? 1 : 0;
    if (typeof w === "string" && /^\s*[+-]?(\d+(_?\d+)*\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/.test(w)) return Number(w.replace(/_/g, ""));
    throw new TypeError("float");
  }

  // math.fsum (Shewchuk): exakte Summe, damit Toleranzprüfung und Gerätetotale wie in Python ausfallen.
  function fsum(werte) {
    const teile = [];
    for (let x of werte) {
      let i = 0;
      for (let y of teile) {
        if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x];
        const hoch = x + y;
        const tief = y - (hoch - x);
        if (tief) teile[i++] = tief;
        x = hoch;
      }
      teile.length = i;
      teile.push(x);
    }
    let summe = 0;
    for (let i = teile.length - 1; i >= 0; i--) summe += teile[i];
    return summe;
  }

  // publication_model.number_out: auf 6 Stellen gerundet.
  const zahlAus = (x) => Number(x.toFixed(6)) + 0;
  const summe = (werte) => (werte.length ? zahlAus(fsum(werte.map(pyFloat))) : null);

  // quelle._natuerlich: Ziffernfolgen als Zahl vergleichen („Linie 10“ nach „Linie 9“).
  function natuerlich(code) {
    return String(code).split(/(\d+)/).filter(Boolean).map((t) => (/^\d+$/.test(t) ? [0, BigInt(t), ""] : [1, 0n, t]));
  }
  function vergleicheNatuerlich(a, b) {
    const x = natuerlich(a), y = natuerlich(b);
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      for (let j = 0; j < 3; j++) if (x[i][j] !== y[i][j]) return x[i][j] < y[i][j] ? -1 : 1;
    }
    return x.length - y.length;
  }
  const minNatuerlich = (codes) => codes.reduce((m, c) => (m === null || vergleicheNatuerlich(c, m) < 0 ? c : m), null);

  // quelle._abgeleitete_struktur: Ersatz für ältere Stände ohne `struktur`.
  function abgeleiteteStruktur(alle) {
    const geraete = new Map();
    const athletik = { beweglichkeit: new Map(), kraft: new Map() };
    for (const r of alle) {
      if (r.bereich === "technik") {
        const g = pyStr(r.geraet);
        if (!geraete.has(g)) geraete.set(g, new Map());
        const c = pyStr(r.code);
        if (!geraete.get(g).has(c)) geraete.get(g).set(c, { code: r.code ?? null, anzeige: r.anzeige ?? null });
      } else if (ATHLETIK.includes(r.bereich)) {
        const c = pyStr(r.code);
        if (!athletik[r.bereich].has(c)) athletik[r.bereich].set(c, { code: r.code ?? null, test: r.test ?? null });
      }
    }
    const ordnung = [...geraete.keys()].sort((a, b) => vergleicheNatuerlich(minNatuerlich([...geraete.get(a).keys()]),
      minNatuerlich([...geraete.get(b).keys()])));
    const out = { geraete: ordnung.map((g) => ({ geraet: g,
      linien: [...geraete.get(g).keys()].sort(vergleicheNatuerlich).map((c) => geraete.get(g).get(c)) })) };
    for (const b of ATHLETIK) out[b] = [...athletik[b].keys()].sort(vergleicheNatuerlich).map((c) => athletik[b].get(c));
    return out;
  }

  // Linien eines gegliederten Geräts: fehlende Werte „–“; Sprung: nur die gewählte Alternative, solange keine gewertet
  // ist beide mit „(Alternative)“. codes = gezeigte Linien (für nicht gegliederte Resultate).
  function geraetLinien(geraet, technik) {
    const linienSoll = liste(geraet.linien).filter(istObjekt);
    const gewaehlt = linienSoll.some((li) => wahr(li.alternative) && technik.has(pyStr(li.code)));
    const linien = [];
    const codes = [];
    for (const linie of linienSoll) {
      const code = pyStr(linie.code);
      const r = technik.get(code);
      if (wahr(linie.alternative) && gewaehlt && !r) continue;  // die andere Sprung-Alternative wurde gewählt
      codes.push(code);
      linien.push({ anzeige: linie.anzeige ?? null, S: r ? r.S ?? null : null, T: r ? r.T ?? null : null,
        alternative: wahr(linie.alternative) && !r });
    }
    const werte = linienSoll.filter((li) => technik.has(pyStr(li.code))).map((li) => technik.get(pyStr(li.code)).T);
    return { geraet: geraet.geraet ?? null, total: summe(werte), linien, codes };
  }

  // „Gerät“: alle vorgesehenen Linien (bzw. Beweglichkeitstests) eines Geräts für einen Turner, aus der Gliederung
  // seiner AK; veröffentlichte Werte eingesetzt, fehlende „–“. null = keine Gliederung → nur Veröffentlichtes zeigen.
  function geraetZeilen(struktur, name, eigene, bereich = "technik") {
    if (!istObjekt(struktur)) return null;
    if (bereich === "beweglichkeit") {
      const tests = liste(struktur.beweglichkeit).filter(istObjekt);
      if (!tests.length) return null;
      const eigen = new Map(eigene.filter((r) => r.bereich === "beweglichkeit").map((r) => [pyStr(r.code), r]));
      const zeilen = tests.map((t) => ({ test: t.test ?? null, punkte: eigen.has(pyStr(t.code)) ? eigen.get(pyStr(t.code)).punkte ?? null : null }));
      const bekannt = new Set(tests.map((t) => pyStr(t.code)));
      for (const [c, r] of [...eigen].sort(([x], [y]) => vergleicheNatuerlich(x, y))) {
        if (!bekannt.has(c)) zeilen.push({ test: r.test ?? null, punkte: r.punkte ?? null });
      }
      return { zeilen };
    }
    const geraet = liste(struktur.geraete).find((g) => istObjekt(g) && g.geraet === name);
    if (!geraet) return null;
    const technik = new Map(eigene.filter((r) => r.bereich === "technik").map((r) => [pyStr(r.code), r]));
    const { linien, codes } = geraetLinien(geraet, technik);
    for (const [c, r] of [...technik].sort(([x], [y]) => vergleicheNatuerlich(x, y))) {
      if (!codes.includes(c)) linien.push({ anzeige: r.anzeige ?? null, S: r.S ?? null, T: r.T ?? null, alternative: false });
    }
    return { linien };
  }

  // quelle.turner_details: Aufschlüsselung eines Eintrags; null = nicht widerspruchsfrei darstellbar.
  function details(eintrag, eigene, struktur, alle) {
    struktur = istObjekt(struktur) ? struktur : abgeleiteteStruktur(alle);
    const technik = new Map();
    for (const r of eigene) if (r.bereich === "technik") technik.set(pyStr(r.code), r);
    const athletik = {};
    for (const b of ATHLETIK) {
      athletik[b] = new Map();
      for (const r of eigene) if (r.bereich === b) athletik[b].set(pyStr(r.code), r);
    }
    const pruefen = [["technik", [...technik.values()].map((r) => r.T)],
      ...ATHLETIK.map((b) => [b, [...athletik[b].values()].map((r) => r.punkte)])];
    for (const [teil, werte] of pruefen) {
      try {
        const soll = wahr(eintrag[teil]) ? pyFloat(eintrag[teil]) : 0;
        if (Math.abs(fsum(werte.map(pyFloat)) - soll) > TOLERANZ) return null;
      } catch { return null; }
    }
    if (eigene.filter((r) => r.bereich === "technik").length !== technik.size
        || eigene.filter((r) => ATHLETIK.includes(r.bereich)).length !== athletik.beweglichkeit.size + athletik.kraft.size) {
      return null;  // doppelte Codes: nicht eindeutig zuordenbar
    }
    const geraete = [];
    const zugeordnet = new Set();
    for (const geraet of liste(struktur.geraete)) {
      if (!istObjekt(geraet)) continue;
      const g = geraetLinien(geraet, technik);
      for (const code of g.codes) zugeordnet.add(code);
      geraete.push({ geraet: g.geraet, total: g.total, linien: g.linien });
    }
    const rest = [...technik].filter(([c]) => !zugeordnet.has(c)).map(([, r]) => r)
      .sort((a, b) => vergleicheNatuerlich(pyStr(a.code), pyStr(b.code)));
    for (const r of rest) {  // nicht in der Gliederung: trotzdem zeigen
      let ziel = geraete.find((g) => g.geraet === (r.geraet ?? null));
      if (!ziel) { ziel = { geraet: r.geraet ?? null, total: null, linien: [] }; geraete.push(ziel); }
      ziel.linien.push({ anzeige: r.anzeige ?? null, S: r.S ?? null, T: r.T ?? null, alternative: false });
      ziel.total = summe([...(ziel.total !== null ? [ziel.total] : []), r.T]);
    }
    const out = { geraete };
    for (const b of ATHLETIK) {
      const tests = liste(struktur[b]).filter(istObjekt);
      const zeilen = tests.map((t) => { const r = athletik[b].get(pyStr(t.code));
        return { test: t.test ?? null, punkte: r ? r.punkte ?? null : null }; });
      const bekannt = new Set(tests.map((t) => pyStr(t.code)));
      for (const [c, r] of [...athletik[b]].sort(([x], [y]) => vergleicheNatuerlich(x, y))) {
        if (!bekannt.has(c)) zeilen.push({ test: r.test ?? null, punkte: r.punkte ?? null });
      }
      out[b] = zeilen;
    }
    return out;
  }

  // ── Darstellung wie app.py (_dz, _details_*, rang_karte) ─────────────────────────────────────────────
  function hatResultate(resultate, art) {
    if (art === "technik") return resultate.some((r) => r.bereich === "technik");
    if (art === "athletik") return resultate.some((r) => ATHLETIK.includes(r.bereich));
    return resultate.length > 0;
  }

  const dz = (label, wert, klasse = "") => `<div class='tt-dz${klasse ? ` ${klasse}` : ""}'><span>${h(label)}</span>`
    + `<b>${h(zahl(wert))}</b></div>`;

  // Aufklappbare Zeile mit Wert (zu Beginn zu); k: Schlüssel, damit sie bei einer Aktualisierung offen bleibt.
  const aufklappbar = (label, wert, inhalt, k, klasse = "tt-geraet") => `<details class='${klasse}'`
    + `${k === undefined ? "" : ` data-k='${h(k)}'`}><summary><span>${h(label)}</span><b>${h(zahl(wert))}</b><i></i>`
    + `</summary>${inhalt}</details>`;

  // Je Gerät das Gerätetotal, aufklappbar zu den Linien mit S und T.
  const geraete = (d, k) => d.geraete.map((g, i) => aufklappbar(g.geraet, g.total, "<div class='tt-linien'>"
    + g.linien.map((li) => `<div class='tt-linie'><span>${h(li.anzeige)}${li.alternative ? " (Alternative)" : ""}`
      + `</span><span class='tt-st'>S ${h(zahl(li.S))}</span><b class='tt-st'>T ${h(zahl(li.T))}</b></div>`).join("")
    + "</div>", k === undefined ? undefined : `${k}|${i}`));

  const DETAILS = {
    // Gestuft: Total Technik → Geräte → Linien; Total Athletik → Beweglichkeit/Kraft → Tests; Gesamt.
    gesamt(e, d, v, k) {
      const unter = (s) => (k === undefined ? undefined : `${k}|${s}`);
      const athletik = ["beweglichkeit", "kraft"].map((b) => aufklappbar(`Total ${ATHLETIK_TEXT[b]}`, v[b] ? e[b] : null,
        `<div class='tt-linien'>${d[b].map((t) => `<div class='tt-linie'><span>${h(t.test)}</span>`
          + `<b class='tt-st'>${h(zahl(t.punkte))}</b></div>`).join("")}</div>`, unter(`a|${b}`)));
      return [aufklappbar("Total Technik", v.technik ? e.technik : null,
        `<div class='tt-stufe'>${geraete(d, unter("t")).join("")}</div>`, unter("t"), "tt-geraet tt-total"),
      aufklappbar("Total Athletik", v.athletik ? e.athletik : null, `<div class='tt-stufe'>${athletik.join("")}</div>`,
        unter("a"), "tt-geraet tt-total"),
      dz("Gesamt", v.gesamt ? e.gesamt : null, "tt-total tt-haupt")].join("");
    },
    technik(e, d, v, k) {
      return [dz("Total Technik", v.technik ? e.technik : null, "tt-total tt-haupt"), ...geraete(d, k)].join("");
    },
    athletik(e, d, v) {
      const teile = [];
      for (const b of ATHLETIK) {
        const text = ATHLETIK_TEXT[b];
        teile.push(`<div class='tt-dtitel'>${text}</div>`, ...d[b].map((t) => dz(t.test, t.punkte)),
          dz(`Total ${text}`, v[b] ? e[b] : null, "tt-total"));
      }
      teile.push(dz("Total Athletik", v.athletik ? e.athletik : null, "tt-total tt-haupt"));
      return teile.join("");
    },
  };

  // Kompakte Zeile (Rang, Turner, Verein, Total der Ebene); Details der Ebene erst nach Antippen.
  // k (optional): Schlüssel, damit geöffnete Details eine Aktualisierung überdauern.
  function karte(e, art, eigene, d, k) {
    const rang = e.rang;
    const punkte = hatResultate(eigene, art) ? zahl(e.punkte) : "–";
    const farbe = Number.isInteger(rang) && punkte !== "–" ? RANG_FARBE[rang] : undefined;
    const stil = farbe ? ` style='background:${farbe};color:#0D4E73;'` : "";
    const vorhanden = { technik: hatResultate(eigene, "technik"), athletik: hatResultate(eigene, "athletik"),
      beweglichkeit: eigene.some((r) => r.bereich === "beweglichkeit"), kraft: eigene.some((r) => r.bereich === "kraft"),
      gesamt: eigene.length > 0 };  // nie scheinbar 0
    const inhalt = d === null ? "<div class='tt-dhinweis'>Details nicht verfügbar.</div>"
      : DETAILS[art](e, d, vorhanden, k);
    return `<div class='tt-karte tt-rangkarte'><details${k === undefined ? "" : ` data-k='${h(k)}'`}><summary><div class='tt-karte-kopf'>`
      + `<div class='tt-rang'${stil} title='Rang ${h(rang)}'>${h(rang)}</div>`
      + `<div class='tt-text'><strong>${h(e.vorname)} ${h(e.name)}</strong><small>${h(e.verein)}</small></div>`
      + `<div class='tt-wert'><div class='tt-note'>${h(punkte)}</div></div><i class='tt-pfeil'>▾</i></div>`
      + `</summary><div class='tt-details'>${inhalt}</div></details></div>`;
  }

  // Ganze Liste einer Auswahl wie app._ranglisten (ohne Auswahlfelder). `alle`: Live-Resultate der AK, neueste zuerst.
  function liste_html(eintraege, art, alle, struktur, hinweis, mitSchluessel) {
    const jePerson = new Map();
    for (const r of alle) { const k = r.teilnehmer ?? null; if (!jePerson.has(k)) jePerson.set(k, []); jePerson.get(k).push(r); }
    const karten = eintraege.map((e, i) => {  // Reihenfolge und Ränge vom Publisher
      const eigene = jePerson.get(e.teilnehmer ?? null) || [];
      return karte(e, art, eigene, details(e, eigene, struktur, alle), mitSchluessel ? `${art}|${pyStr(e.teilnehmer ?? i)}` : undefined);
    });
    return [hinweis ? `<div class='tt-anzahl'>${h(hinweis)}</div>` : "", "<div class='tt-liste tt-rangliste'>", ...karten,
      "</div><div class='tt-anzahl'>– = noch keine Note</div>"].join("");
  }

  const ziel = typeof window !== "undefined" ? window : globalThis;
  ziel.TT_RANGLISTE = Object.freeze({ ARTEN, ART_TEXT, details, karte, liste: liste_html, hatResultate, geraete, zahl, summe,
    geraetZeilen });
})();
