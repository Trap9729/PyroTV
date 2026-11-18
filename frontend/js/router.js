// Pure hash routing helpers (no DOM, testable in Node).
//   #/            -> {name: "grid"}
//   #/play/<key>  -> {name: "play", key}
//   #/wizard      -> {name: "wizard"}
//   #/settings    -> {name: "settings"}

const GRID = Object.freeze({ name: "grid" });

export function parseRoute(hash) {
  const path = String(hash || "").replace(/^#/, "");
  if (path === "/wizard") return { name: "wizard" };
  if (path === "/settings") return { name: "settings" };
  if (path.startsWith("/play/")) {
    const raw = path.slice("/play/".length);
    if (!raw) return { ...GRID };
    try {
      return { name: "play", key: decodeURIComponent(raw) };
    } catch {
      return { ...GRID };
    }
  }
  return { ...GRID };
}

export function buildRoute(route) {
  switch (route?.name) {
    case "play":
      return "#/play/" + encodeURIComponent(route.key);
    case "wizard":
      return "#/wizard";
    case "settings":
      return "#/settings";
    default:
      return "#/";
  }
}
