// On-screen keyboard for the touchscreen kiosk (Swedish). Replaces squeekboard,
// which cannot draw above a fullscreen Chromium under sway.
//
// It shows while a text field has focus and never takes focus itself: key presses
// are handled on pointerdown with preventDefault, so the field keeps its caret.
// The app container is shrunk by --kb-h so the page stays usable above the keyboard.
import { el } from "./dom.js";
import { t } from "./i18n.js";
import { LAYERS, applyKey, shouldAutoShift, wantsKeyboard } from "./keyboard-logic.js";

const REPEAT_DELAY_MS = 380;
const REPEAT_MS = 70;

const FLEX = { shift: 1.5, back: 1.5, sym: 1.5, abc: 1.5, space: 5, enter: 2 };

export function initKeyboard() {
  const kb = el("div", "kb");
  kb.hidden = true;
  kb.setAttribute("aria-hidden", "true");
  document.body.append(kb);

  let target = null;
  let layer = "letters";
  let shift = false;
  let repeatTimer = null;

  const labelOf = (key) => {
    switch (key) {
      case "shift": return "⇧";
      case "back": return "⌫";
      case "sym": return "?123";
      case "abc": return "ABC";
      case "space": return "";
      case "enter": return t("keyboard.enter");
      default: return shift && layer === "letters" ? key.toUpperCase() : key;
    }
  };

  function build() {
    const frag = document.createDocumentFragment();
    for (const row of LAYERS[layer]) {
      const r = el("div", "kb-row");
      for (const key of row) {
        const b = el("button", "kb-key", labelOf(key));
        b.type = "button";
        b.tabIndex = -1;
        b.dataset.key = key;
        b.style.flexGrow = String(FLEX[key] ?? 1);
        if (FLEX[key]) b.classList.add("kb-special");
        if (key === "enter") b.classList.add("kb-enter");
        if (key === "shift") {
          b.classList.toggle("on", shift);
          b.setAttribute("aria-label", t("keyboard.shift"));
        }
        if (key === "back") b.setAttribute("aria-label", t("keyboard.back"));
        if (key === "space") b.setAttribute("aria-label", t("keyboard.space"));
        r.append(b);
      }
      frag.append(r);
    }
    kb.replaceChildren(frag);
  }

  function setInset() {
    document.documentElement.style.setProperty("--kb-h", kb.hidden ? "0px" : `${kb.offsetHeight}px`);
  }

  function selection() {
    const v = target.value;
    let s = null;
    let e = null;
    try {
      s = target.selectionStart;
      e = target.selectionEnd;
    } catch {
      /* number/email fields have no selection API */
    }
    return [s ?? v.length, e ?? s ?? v.length];
  }

  function refreshAutoShift() {
    if (!target || layer !== "letters") return;
    const [caret] = selection();
    const want = shouldAutoShift(target.value, caret, target.type);
    if (want !== shift) {
      shift = want;
      build();
    }
  }

  function show(node) {
    target = node;
    layer = "letters";
    shift = false;
    kb.hidden = false;
    build();
    refreshAutoShift();
    setInset();
    // The field may now sit under the keyboard or off-screen.
    requestAnimationFrame(() => target?.scrollIntoView?.({ block: "center", behavior: "instant" }));
  }

  function stopRepeat() {
    clearTimeout(repeatTimer);
    repeatTimer = null;
  }

  function hide() {
    stopRepeat();
    target = null;
    kb.hidden = true;
    setInset();
  }

  function press(key) {
    if (!target || !target.isConnected) return hide();
    switch (key) {
      case "shift":
        shift = !shift;
        return build();
      case "sym":
      case "abc":
        layer = key === "sym" ? "symbols" : "letters";
        shift = false;
        build();
        return refreshAutoShift();
      case "enter": {
        target.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
        target.blur(); // "Klar": close the keyboard
        return;
      }
      default: {
        const ch = layer === "letters" && shift && key.length === 1 ? key.toUpperCase() : key;
        const res = applyKey(target.value, selection(), ch, target.maxLength ?? -1);
        if (res.value === target.value) return; // nothing changed (start of field, max length)
        target.value = res.value;
        try {
          target.setSelectionRange(res.caret, res.caret);
        } catch {
          /* no selection API on this type */
        }
        const typed = key === "back" ? null : ch;
        target.dispatchEvent(
          new InputEvent("input", { bubbles: true, inputType: typed ? "insertText" : "deleteContentBackward", data: typed }),
        );
        if (shift && key !== "back") shift = false;
        build();
        refreshAutoShift();
      }
    }
  }

  function startRepeat(key) {
    stopRepeat();
    if (key !== "back") return;
    const tick = () => {
      press("back");
      repeatTimer = setTimeout(tick, REPEAT_MS);
    };
    repeatTimer = setTimeout(tick, REPEAT_DELAY_MS);
  }

  // Keys never take focus (preventDefault also cancels the compatibility mouse
  // events, whose default action would move focus off the text field).
  kb.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const b = e.target.closest(".kb-key");
    if (!b) return;
    press(b.dataset.key);
    startRepeat(b.dataset.key);
  });
  for (const type of ["pointerup", "pointercancel", "pointerleave"]) kb.addEventListener(type, stopRepeat);
  kb.addEventListener("mousedown", (e) => e.preventDefault());
  // A tap on the keyboard's padding must not blur the field either.
  kb.addEventListener("touchstart", (e) => e.preventDefault(), { passive: false });

  document.addEventListener("focusin", (e) => {
    if (wantsKeyboard(e.target)) show(e.target);
  });
  document.addEventListener("focusout", () => {
    // Focus moving from one field to another must not flicker the keyboard.
    setTimeout(() => {
      if (!wantsKeyboard(document.activeElement)) hide();
    }, 0);
  });
  window.addEventListener("hashchange", hide);
  // Edits made by the page itself (e.g. the clear button emptying the search box).
  document.addEventListener("input", (e) => {
    if (e.target === target) refreshAutoShift();
  });
}
