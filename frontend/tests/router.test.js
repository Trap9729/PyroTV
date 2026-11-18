import { test } from "node:test";
import assert from "node:assert/strict";

import { parseRoute, buildRoute } from "../js/router.js";

test("empty or root hash routes to the grid", () => {
  assert.deepEqual(parseRoute(""), { name: "grid" });
  assert.deepEqual(parseRoute("#"), { name: "grid" });
  assert.deepEqual(parseRoute("#/"), { name: "grid" });
});

test("unknown hash routes to the grid", () => {
  assert.deepEqual(parseRoute("#/nope"), { name: "grid" });
  assert.deepEqual(parseRoute("#/play/"), { name: "grid" });
});

test("wizard and settings", () => {
  assert.deepEqual(parseRoute("#/wizard"), { name: "wizard" });
  assert.deepEqual(parseRoute("#/settings"), { name: "settings" });
});

test("play route decodes the key", () => {
  assert.deepEqual(parseRoute("#/play/lesli%3A%C3%85ska%2049"), {
    name: "play",
    key: "lesli:Åska 49",
  });
});

test("malformed encoding falls back to the grid", () => {
  assert.deepEqual(parseRoute("#/play/%E0%A4%A"), { name: "grid" });
});

test("buildRoute is the inverse of parseRoute", () => {
  assert.equal(buildRoute({ name: "grid" }), "#/");
  assert.equal(buildRoute({ name: "wizard" }), "#/wizard");
  assert.equal(buildRoute({ name: "settings" }), "#/settings");
  const r = { name: "play", key: "lesli:Åska 49" };
  const h = buildRoute(r);
  assert.equal(h, "#/play/lesli%3A%C3%85ska%2049");
  assert.deepEqual(parseRoute(h), r);
  const slash = { name: "play", key: "c-1:a/b?c#d" };
  assert.deepEqual(parseRoute(buildRoute(slash)), slash);
});
