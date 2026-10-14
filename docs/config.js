// Öffentliche Konfiguration der statischen Seite (GitHub Pages, main /docs).
// Nur der Publishable Key (sb_publishable_…) ist zulässig – er ist für Browser bestimmt und durch RLS
// auf öffentliche Testtage beschränkt. Nie einen Secret Key eintragen; live.js weist jeden anderen Schlüssel ab.
window.TT_CONFIG = Object.freeze({
  supabaseUrl: "https://lxljapebdcutlaaidtry.supabase.co",
  publishableKey: "sb_publishable_j9MGb4EU6J_afcu0vhAMkg_JybzHBKN",
  streamlitUrl: "https://testtag.streamlit.app",
  // Rückfall auf die bisherige Anzeige: true leitet jeden Aufruf vor dem ersten Datenabruf nach
  // streamlitUrl/?t=<slug>&ansicht=live um (vorher in Streamlit das Secret live_url entfernen).
  rueckfall: false,
  // Ansichten auf dieser Seite zusätzlich zu Live. Eine fehlende führt (Pille, &ansicht=…) nach
  // streamlitUrl/?t=<slug>&ansicht=…; vorher in Streamlit dieselbe aus dem Secret statische_bereiche entfernen.
  ansichten: ["rangliste", "startliste", "zeitplan"],
});
