import { test } from "node:test";
import assert from "node:assert/strict";

import {
  wizardSteps,
  diffEnabled,
  canAdvance,
  groupByCategory,
  formatPin,
  runLimited,
  mergeEnabled,
  mergeManufacturerList,
  OTHER_CATEGORY,
} from "../js/views/wizard-logic.js";

test("mergeEnabled keeps the selection and adds unseen server-enabled manufacturers", () => {
  const server = [
    { id: "a", enabled: true },
    { id: "b", enabled: false },
    { id: "c-ny", enabled: true }, // created from a phone meanwhile
    { id: "c-off", enabled: false }, // unseen but not enabled: stays off
  ];
  assert.deepEqual(mergeEnabled(server, ["a", "b"], ["b"]), ["b", "c-ny"]);
  // a known manufacturer the user switched off stays off even if the server has it on
  assert.deepEqual(mergeEnabled(server, ["a", "b", "c-ny"], ["b"]), ["b"]);
  // ids the server no longer has are dropped (the PUT would be refused)
  assert.deepEqual(mergeEnabled(server, ["a", "gone"], ["a", "gone"]), ["a", "c-ny"]);
  assert.deepEqual(mergeEnabled([], [], []), []);
});

test("mergeManufacturerList keeps local objects, appends new, drops gone", () => {
  const localA = { id: "a", enabled: false, local: true };
  const localGone = { id: "c-x" };
  const serverA = { id: "a", enabled: true };
  const serverNew = { id: "c-ny", enabled: true };
  const r = mergeManufacturerList([localA, localGone], [serverA, serverNew]);
  assert.deepEqual(r.mfrs, [localA, serverNew]);
  assert.strictEqual(r.mfrs[0], localA);
  assert.deepEqual(r.added, [serverNew]);
  assert.deepEqual(r.removed, [localGone]);
});

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

test("runLimited runs every item, never exceeds the limit and counts failures", async () => {
  let active = 0;
  let peak = 0;
  const seen = [];
  const failed = await runLimited([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
    active++;
    peak = Math.max(peak, active);
    await new Promise((r) => setTimeout(r, 2));
    active--;
    seen.push(n);
    if (n % 3 === 0) throw new Error("no");
  });
  assert.equal(failed, 2);
  assert.equal(peak, 3);
  assert.deepEqual(seen.sort(), [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(await runLimited([], 4, async () => {}), 0);
});
