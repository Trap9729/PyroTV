// Every user-visible string of the kiosk UI, in Swedish. Flat keys, `{name}` placeholders.
export const sv = {
  "app.name": "PyroTV",

  "common.back": "Tillbaka",
  "common.settings": "Inställningar",
  "common.retry": "Försök igen",

  "boot.errorTitle": "Kunde inte ansluta till PyroTV",
  "boot.errorHint": "Kontrollera att enheten är igång och försök igen om en stund.",

  "grid.all": "Alla",
  "grid.popular": "Mest sedda",
  "grid.search": "Sök fyrverkeri",
  "grid.clearSearch": "Rensa sökningen",
  "grid.filters": "Filtrera efter kategori",
  "grid.tvConnected": "TV ansluten",
  "grid.empty": "Inga fyrverkerier ännu",
  "grid.emptyHint": "Öppna Inställningar för att välja tillverkare eller lägga till egna filmer.",
  "grid.noResults": "Inga träffar för ”{q}”",
  "grid.noResultsCategory": "Inga fyrverkerier i den här kategorin",
  "grid.noResultsHint": "Prova ett annat sökord eller välj Alla.",
  "grid.loadError": "Kunde inte hämta fyrverkerierna. Försök igen.",
  "grid.playError": "Kunde inte starta filmen. Försök igen.",

  "player.exit": "Avsluta filmen",
  "player.play": "Spela",
  "player.pause": "Pausa",
  "player.seekLabel": "{value} s",
  "player.seekBack": "Spola tillbaka 10 sekunder",
  "player.seekForward": "Spola framåt 10 sekunder",
  "player.seekBar": "Position i filmen",
  "player.tvHint": "Spelas på TV:n",
  "player.tvDisconnected": "TV:n kopplades från",
  "player.commandError": "Kunde inte styra TV:n. Försök igen.",
  "player.videoError": "Filmen kunde inte spelas upp.",

  "stub.soon": "Kommer snart",

  "wizard.step": "Steg {n} av {total}",
  "wizard.progress": "Hur långt du har kommit",
  "wizard.next": "Nästa",
  "wizard.saving": "Sparar…",
  "wizard.loading": "Hämtar…",
  "wizard.loadError": "Kunde inte hämta uppgifterna. Försök igen.",
  "wizard.saveError": "Kunde inte spara. Försök igen.",
  "wizard.selectAll": "Välj alla",
  "wizard.selectNone": "Välj inga",

  "wizard.shop.title": "Vad heter butiken?",
  "wizard.shop.label": "Butikens namn",
  "wizard.shop.hint": "Namnet visas högst upp på skärmen. Högst 60 tecken.",
  "wizard.shop.placeholder": "Till exempel Fyrverkeributiken",

  "wizard.mfr.title": "Vilka tillverkare säljer ni?",
  "wizard.mfr.hint": "Välj en eller flera. Du kan ändra detta senare i Inställningar.",
  "wizard.mfr.count": "{n} fyrverkerier",
  "wizard.mfr.custom": "Egen",
  "wizard.mfr.none": "Inga tillverkare hittades.",

  "wizard.pieces.hint": "Avmarkera fyrverkerier som ni inte säljer. Resten visas på skärmen.",
  "wizard.pieces.count": "{n} av {total} valda",
  "wizard.pieces.empty": "Den här tillverkaren har inga fyrverkerier.",

  "wizard.custom.title": "Egna fyrverkerier",
  "wizard.custom.intro": "Vill du lägga till egna filmer? Starta Wi‑Fi och använd mobilen. Du kan hoppa över det här steget.",
  "wizard.custom.step1": "1. Anslut mobilen till Wi‑Fi-nätverket med PIN-koden.",
  "wizard.custom.step2": "2. Skanna QR-koden eller öppna adressen i mobilens webbläsare.",
  "wizard.custom.start": "Starta Wi‑Fi",
  "wizard.custom.stop": "Stäng av Wi‑Fi",
  "wizard.custom.starting": "Startar Wi‑Fi…",
  "wizard.custom.stopping": "Stänger av Wi‑Fi…",
  "wizard.custom.off": "Wi‑Fi är avstängt.",
  "wizard.custom.ssid": "Wi‑Fi-nätverk",
  "wizard.custom.pin": "PIN-kod",
  "wizard.custom.url": "Adress",
  "wizard.custom.qr": "QR-kod till uppladdningssidan",
  "wizard.custom.newPin": "En ny PIN-kod skapas varje gång Wi‑Fi startas.",
  "wizard.custom.error": "Kunde inte ändra Wi‑Fi. Försök igen.",
  "wizard.custom.listTitle": "Redan tillagda",
  "wizard.custom.listEmpty": "Inga egna fyrverkerier ännu.",

  "wizard.finish.title": "Allt klart!",
  "wizard.finish.hint": "Kontrollera sammanfattningen och tryck på Klar. Allt går att ändra senare i Inställningar.",
  "wizard.finish.shop": "Butik",
  "wizard.finish.manufacturers": "Tillverkare",
  "wizard.finish.pieces": "Fyrverkerier",
  "wizard.finish.custom": "Egna fyrverkerier",
  "wizard.finish.done": "Klar",
  "wizard.finish.error": "Kunde inte slutföra installationen. Försök igen.",
};

export function t(key, vars) {
  if (!Object.hasOwn(sv, key)) throw new Error(`unknown i18n key: ${key}`);
  const s = sv[key];
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, name) => (Object.hasOwn(vars, name) ? String(vars[name]) : m));
}
