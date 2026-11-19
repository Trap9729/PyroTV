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

// The enabled-manufacturer list is written whole, so build it against a fresh
// server list: the user's selection, plus every manufacturer the server has
// enabled that this client has never seen (e.g. a custom manufacturer created
// from a phone meanwhile). Ids the server no longer knows (a deleted custom
// manufacturer) are dropped, or the PUT would be refused.
export function mergeEnabled(serverMfrs, knownIds, selectedIds) {
  const onServer = new Set(serverMfrs.map((m) => m.id));
  const known = new Set(knownIds);
  const out = selectedIds.filter((id) => onServer.has(id));
  const seen = new Set(out);
  for (const m of serverMfrs) {
    if (m.enabled && !known.has(m.id) && !seen.has(m.id)) {
      out.push(m.id);
      seen.add(m.id);
    }
  }
  return out;
}

// Merge a fresh manufacturer list into the local one: server order, local objects
// kept for manufacturers already known, new ones appended; gone ones removed.
export function mergeManufacturerList(localMfrs, serverMfrs) {
  const local = new Map(localMfrs.map((m) => [m.id, m]));
  const mfrs = serverMfrs.map((m) => local.get(m.id) ?? m);
  const added = serverMfrs.filter((m) => !local.has(m.id));
  const onServer = new Set(serverMfrs.map((m) => m.id));
  const removed = localMfrs.filter((m) => !onServer.has(m.id));
  return { mfrs, added, removed };
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

// Run `worker(item)` for every item with at most `limit` in flight.
// Resolves with the number of items whose worker threw.
export async function runLimited(items, limit, worker) {
  let failed = 0;
  let i = 0;
  const lane = async () => {
    while (i < items.length) {
      const item = items[i++];
      try {
        await worker(item);
      } catch {
        failed++;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return failed;
}
