// Pure setup-wizard helpers (no DOM, testable in Node).

export const OTHER_CATEGORY = "Övrigt";
export const SHOP_NAME_MAX = 60;

export function wizardSteps(selectedManufacturers) {
  return [
    "shop",
    "manufacturers",
    ...selectedManufacturers.map((id) => `pieces:${id}`),
    "custom",
    "finish",
  ];
}

// Which piece keys must be switched on / off to go from `current` to `wanted`.
export function diffEnabled(current, wanted) {
  const cur = new Set(current);
  const want = new Set(wanted);
  const enable = [];
  const disable = [];
  for (const k of want) if (!cur.has(k)) enable.push(k);
  for (const k of cur) if (!want.has(k)) disable.push(k);
  return { enable, disable };
}

export function canAdvance(step, state = {}) {
  if (step === "shop") {
    const n = String(state.shopName ?? "").trim().length;
    return n >= 1 && n <= SHOP_NAME_MAX;
  }
  if (step === "manufacturers") return (state.selected?.length ?? 0) > 0;
  return true;
}

export function groupByCategory(pieces) {
  const groups = new Map();
  for (const p of pieces) {
    const category = p.category && String(p.category).trim() ? p.category : OTHER_CATEGORY;
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(p);
  }
  return [...groups].map(([category, items]) => ({ category, pieces: items }));
}

// "12345678" -> "1234 5678"
export function formatPin(pin) {
  if (!pin) return "";
  return String(pin).replace(/(.{4})(?=.)/g, "$1 ");
}
