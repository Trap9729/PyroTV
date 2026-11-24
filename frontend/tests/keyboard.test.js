import { test } from "node:test";
import assert from "node:assert/strict";
import { LAYERS, applyKey, isKioskHost, shouldAutoShift, wantsKeyboard } from "../js/keyboard-logic.js";

test("inserts at the caret and replaces a selection", () => {
  assert.deepEqual(applyKey("ab", [1, 1], "x"), { value: "axb", caret: 2 });
  assert.deepEqual(applyKey("abcd", [1, 3], "å"), { value: "aåd", caret: 2 });
  assert.deepEqual(applyKey("a", [1, 1], "space"), { value: "a ", caret: 2 });
});

test("backspace deletes a selection, one char, or nothing at the start", () => {
  assert.deepEqual(applyKey("abcd", [1, 3], "back"), { value: "ad", caret: 1 });
  assert.deepEqual(applyKey("abc", [2, 2], "back"), { value: "ac", caret: 1 });
  assert.deepEqual(applyKey("abc", [0, 0], "back"), { value: "abc", caret: 0 });
  assert.deepEqual(applyKey("a😀", [3, 3], "back"), { value: "a", caret: 1 });
});

test("maxLength is respected", () => {
  assert.deepEqual(applyKey("abc", [3, 3], "d", 3), { value: "abc", caret: 3 });
  assert.deepEqual(applyKey("abc", [1, 2], "d", 3), { value: "adc", caret: 2 });
});

test("out-of-range selections are clamped", () => {
  assert.deepEqual(applyKey("ab", [null, null], "c"), { value: "abc", caret: 3 });
});

test("auto-shift at the start of a name, not in search", () => {
  assert.equal(shouldAutoShift("", 0), true);
  assert.equal(shouldAutoShift("Hej", 3), false);
  assert.equal(shouldAutoShift("Hej. ", 5), true);
  assert.equal(shouldAutoShift("", 0, "search"), false);
});

test("kiosk host detection", () => {
  assert.equal(isKioskHost("127.0.0.1"), true);
  assert.equal(isKioskHost("localhost"), true);
  assert.equal(isKioskHost("10.42.0.1"), false);
  assert.equal(isKioskHost("pyrotv.local"), false);
  assert.equal(isKioskHost("10.42.0.1", "?kbd=1"), true);
});

test("only text-like fields want a keyboard", () => {
  assert.equal(wantsKeyboard({ tagName: "INPUT", type: "text" }), true);
  assert.equal(wantsKeyboard({ tagName: "INPUT", type: "search" }), true);
  assert.equal(wantsKeyboard({ tagName: "INPUT", type: "range" }), false);
  assert.equal(wantsKeyboard({ tagName: "INPUT", type: "file" }), false);
  assert.equal(wantsKeyboard({ tagName: "INPUT", type: "text", readOnly: true }), false);
  assert.equal(wantsKeyboard({ tagName: "BUTTON" }), false);
});

test("Swedish letters are on the letter layer", () => {
  const keys = LAYERS.letters.flat();
  for (const k of ["å", "ä", "ö", "back", "shift", "space", "enter"]) assert.ok(keys.includes(k), k);
});
