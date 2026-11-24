// On-screen keyboard: pure parts (layouts and text editing), testable in Node.
// The DOM side lives in keyboard.js.

// Each key is a character, or a special: "shift", "back", "sym", "abc", "space", "enter".
export const LAYERS = {
  letters: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "å"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"],
    ["shift", "z", "x", "c", "v", "b", "n", "m", ",", ".", "back"],
    ["sym", "-", "space", "?", "enter"],
  ],
  symbols: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["@", "#", "&", "*", "(", ")", "+", "=", "/", "%"],
    ["!", "?", ":", ";", "'", '"', "_", "<", ">", "$"],
    ["é", "ü", "è", "ø", "æ", ",", ".", "-", "back"],
    ["abc", "space", "enter"],
  ],
};

// Input types that take typed text (everything else, e.g. range/file/checkbox, gets no keyboard).
const TEXT_TYPES = new Set(["text", "search", "email", "url", "tel", "password", "number", ""]);

export function wantsKeyboard(node) {
  if (!node || node.disabled || node.readOnly) return false;
  const tag = String(node.tagName || "").toLowerCase();
  if (tag === "textarea") return true;
  if (tag !== "input") return false;
  return TEXT_TYPES.has(String(node.type ?? "text").toLowerCase());
}

// The kiosk browser loads the app from loopback; phones come in over the hotspot
// and have their own keyboard. `?kbd=1` forces it on (desktop testing).
export function isKioskHost(hostname, search = "") {
  if (/[?&]kbd=1(&|$)/.test(search)) return true;
  return ["127.0.0.1", "localhost", "[::1]", "::1"].includes(hostname);
}

// Start of a sentence/name: auto-capitalise there (not in search boxes).
export function shouldAutoShift(value, caret, type = "text") {
  if (type === "search" || type === "email" || type === "url" || type === "password") return false;
  const before = value.slice(0, caret);
  return before.trim() === "" || /[.!?]\s+$/.test(before);
}

// Apply one key to a field's value. `sel` is [start, end]. Returns the new value and caret.
// Specials other than "back" / "space" are not handled here.
export function applyKey(value, sel, key, maxLength = -1) {
  let [start, end] = sel;
  start = Math.max(0, Math.min(start ?? value.length, value.length));
  end = Math.max(start, Math.min(end ?? start, value.length));
  if (key === "back") {
    if (start !== end) return { value: value.slice(0, start) + value.slice(end), caret: start };
    if (start === 0) return { value, caret: 0 };
    // Delete one code point, not half a surrogate pair.
    const cut = /[\uDC00-\uDFFF]/.test(value[start - 1]) && start >= 2 ? 2 : 1;
    return { value: value.slice(0, start - cut) + value.slice(end), caret: start - cut };
  }
  const text = key === "space" ? " " : key;
  let next = value.slice(0, start) + text + value.slice(end);
  if (maxLength >= 0 && next.length > maxLength) return { value, caret: start };
  return { value: next, caret: start + text.length };
}
