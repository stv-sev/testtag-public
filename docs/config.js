// Öffentliche Konfiguration der statischen Live-Ansicht (Prototyp).
// Nur der Publishable Key (sb_publishable_…) ist zulässig – er ist für Browser bestimmt und durch RLS
// auf öffentliche Testtage beschränkt. Nie einen Secret Key eintragen; live.js weist jeden anderen Schlüssel ab.
window.TT_CONFIG = Object.freeze({
  supabaseUrl: "https://lxljapebdcutlaaidtry.supabase.co",
  publishableKey: "sb_publishable_j9MGb4EU6J_afcu0vhAMkg_JybzHBKN",
  streamlitUrl: "https://testtag.streamlit.app",
  // Rückfall auf die bisherige Anzeige: true leitet jeden Aufruf vor dem ersten Datenabruf nach
  // streamlitUrl/?t=<slug>&ansicht=live um (vorher in Streamlit das Secret live_url entfernen).
  rueckfall: false,
  // Ranglisten auf dieser Seite (true) oder in Streamlit (false: Pille und &ansicht=rangliste führen nach
  // streamlitUrl/?t=<slug>&ansicht=rangliste; vorher in Streamlit das Secret statische_rangliste entfernen).
  rangliste: true,
});
