// Öffentliche Konfiguration der statischen Live-Ansicht (Prototyp).
// Nur der Publishable Key (sb_publishable_…) ist zulässig – er ist für Browser bestimmt und durch RLS
// auf öffentliche Testtage beschränkt. Nie einen Secret Key eintragen; live.js weist jeden anderen Schlüssel ab.
window.TT_CONFIG = Object.freeze({
  supabaseUrl: "https://lxljapebdcutlaaidtry.supabase.co",
  publishableKey: "sb_publishable_j9MGb4EU6J_afcu0vhAMkg_JybzHBKN",
  streamlitUrl: "https://testtag.streamlit.app",
});
