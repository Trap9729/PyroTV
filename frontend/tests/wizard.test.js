import { test } from "node:test";
import assert from "node:assert/strict";

import {
  wizardSteps,
  diffEnabled,
  canAdvance,
  groupByCategory,
  formatPin,
  OTHER_CATEGORY,
} from "../js/views/wizard-logic.js";

test("wizardSteps lists one pieces step per manufacturer in order", () => {
  assert.deepEqual(wizardSteps(["lesli", "nico"]), [
    "shop",
    "manufacturers",
    "pieces:lesli",
    "pieces:nico",
    "custom",
    "finish",
  ]);
});

test("wizardSteps without manufacturers", () => {
  assert.deepEqual(wizardSteps([]), ["shop", "manufacturers", "custom", "finish"]);
});

test("diffEnabled returns only changed keys", () => {
  const r = diffEnabled(new Set(["a", "b", "c"]), ["b", "c", "d"]);
  assert.deepEqual(r, { enable: ["d"], disable: ["a"] });
  assert.deepEqual(diffEnabled([], []), { enable: [], disable: [] });
  assert.deepEqual(diffEnabled(["x"], new Set(["x"])), { enable: [], disable: [] });
});

test("canAdvance validates shop name and manufacturer selection", () => {
  assert.equal(canAdvance("shop", { shopName: "   " }), false);
  assert.equal(canAdvance("shop", { shopName: " Fyrverkeri AB " }), true);
  assert.equal(canAdvance("shop", { shopName: "x".repeat(60) }), true);
  assert.equal(canAdvance("shop", { shopName: "x".repeat(61) }), false);
  assert.equal(canAdvance("manufacturers", { selected: [] }), false);
  assert.equal(canAdvance("manufacturers", { selected: ["lesli"] }), true);
  assert.equal(canAdvance("pieces:lesli", {}), true);
  assert.equal(canAdvance("custom", {}), true);
  assert.equal(canAdvance("finish", {}), true);
});

test("groupByCategory keeps first-seen order and falls back for empty", () => {
  const pieces = [
    { key: "1", category: "Tårtor" },
    { key: "2", category: "Raketer" },
    { key: "3", category: "Tårtor" },
    { key: "4", category: "" },
    { key: "5" },
  ];
  const g = groupByCategory(pieces);
  assert.deepEqual(
    g.map((x) => [x.category, x.pieces.map((p) => p.key)]),
    [
      ["Tårtor", ["1", "3"]],
      ["Raketer", ["2"]],
      [OTHER_CATEGORY, ["4", "5"]],
    ],
  );
  assert.deepEqual(groupByCategory([]), []);
});

test("formatPin spaces digits in groups of four", () => {
  assert.equal(formatPin("12345678"), "1234 5678");
  assert.equal(formatPin("123456"), "1234 56");
  assert.equal(formatPin(null), "");
});
