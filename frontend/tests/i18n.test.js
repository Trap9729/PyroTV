import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { sv, t } from "../js/i18n.js";

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "js");

function jsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...jsFiles(p));
    else if (name.endsWith(".js")) out.push(p);
  }
  return out;
}

function usedKeys() {
  const re = /\bt\(\s*(["'])([^"']+)\1/g;
  const keys = new Map();
  for (const file of jsFiles(JS_DIR)) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(re)) keys.set(m[2], file);
  }
  return keys;
}

test("every literal t() key used under frontend/js exists in sv", () => {
  const used = usedKeys();
  assert.ok(used.size > 0, "expected some t() calls");
  for (const [key, file] of used) {
    assert.ok(Object.hasOwn(sv, key), `unknown i18n key "${key}" in ${file}`);
  }
});

test("every sv key is used by at least one t() call", () => {
  const used = usedKeys();
  for (const key of Object.keys(sv)) {
    assert.ok(used.has(key), `sv key "${key}" is never used`);
  }
});

test("t() throws on an unknown key", () => {
  assert.throws(() => t("unknown"), /unknown/);
});

test("t() returns the Swedish string and fills placeholders", () => {
  assert.equal(t("grid.all"), "Alla");
  assert.equal(t("common.back"), "Tillbaka");
  assert.equal(typeof t("grid.empty"), "string");
});

test("t() substitutes {name}-style placeholders", () => {
  const key = Object.keys(sv).find((k) => /\{\w+\}/.test(sv[k]));
  assert.ok(key, "expected at least one string with a placeholder");
  const name = sv[key].match(/\{(\w+)\}/)[1];
  const out = t(key, { [name]: "XYZ" });
  assert.ok(out.includes("XYZ"));
  assert.ok(!out.includes(`{${name}}`));
});

test("all sv values are non-empty strings", () => {
  for (const [k, v] of Object.entries(sv)) {
    assert.equal(typeof v, "string", k);
    assert.ok(v.trim().length > 0, k);
  }
});
