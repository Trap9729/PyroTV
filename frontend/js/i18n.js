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

  "stub.soon": "Kommer snart",
  "stub.wizard": "Kom igång",
};

export function t(key, vars) {
  if (!Object.hasOwn(sv, key)) throw new Error(`unknown i18n key: ${key}`);
  const s = sv[key];
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, name) => (Object.hasOwn(vars, name) ? String(vars[name]) : m));
}
