import { test } from "node:test";
import assert from "node:assert/strict";

import {
  formatBytes,
  storagePercent,
  AUTOOFF_OPTIONS,
  validateCustomForm,
  customErrorMessage,
  jobFailureText,
  NEW_VALUE,
} from "../js/views/settings-logic.js";
import { UploadError } from "../js/uploader.js";
import { ApiError } from "../js/api.js";
import { t } from "../js/i18n.js";

test("formatBytes uses a Swedish decimal comma and sensible units", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1,5 kB");
  assert.equal(formatBytes(5 * 1024 ** 2), "5,0 MB");
  assert.equal(formatBytes(12.4 * 1024 ** 3), "12,4 GB");
  assert.equal(formatBytes(40 * 1024 ** 3), "40,0 GB");
  assert.equal(formatBytes(2 * 1024 ** 4), "2,0 TB");
  assert.equal(formatBytes(-5), "0 B");
  assert.equal(formatBytes(NaN), "0 B");
});

test("storagePercent is clamped to 0..100", () => {
  assert.equal(storagePercent(10, 40), 25);
  assert.equal(storagePercent(50, 40), 100);
  assert.equal(storagePercent(0, 0), 0);
  assert.equal(storagePercent(-1, 40), 0);
});

test("auto-off options: never, 15, 30, 60, 120", () => {
  assert.deepEqual(AUTOOFF_OPTIONS, [0, 15, 30, 60, 120]);
});

const ok = {
  mfrChoice: "lesli",
  newMfr: "",
  name: "Vulkan",
  catChoice: "Batterier",
  newCat: "",
  video: { name: "a.mp4", size: 5 },
};

test("validateCustomForm accepts a complete form and resolves manufacturer and category", () => {
  assert.deepEqual(validateCustomForm(ok), { ok: true, errors: [] });
  const fresh = { ...ok, mfrChoice: NEW_VALUE, newMfr: "  Mina  ", catChoice: NEW_VALUE, newCat: " Raketer " };
  assert.equal(validateCustomForm(fresh).ok, true);
});

test("validateCustomForm lists what is missing", () => {
  assert.deepEqual(validateCustomForm({ ...ok, name: "  " }).errors, ["name"]);
  assert.deepEqual(validateCustomForm({ ...ok, video: null }).errors, ["video"]);
  assert.deepEqual(validateCustomForm({ ...ok, mfrChoice: NEW_VALUE, newMfr: " " }).errors, ["manufacturer"]);
  assert.deepEqual(validateCustomForm({ ...ok, catChoice: NEW_VALUE, newCat: "" }).errors, ["category"]);
  assert.deepEqual(validateCustomForm({ ...ok, name: "x".repeat(201) }).errors, ["name"]);
});

test("customErrorMessage maps statuses to Swedish text", () => {
  assert.equal(customErrorMessage(new UploadError("x", { status: 507 })), t("custom.err.disk"));
  assert.equal(customErrorMessage(new UploadError("x", { status: 413 })), t("custom.err.size"));
  assert.equal(customErrorMessage(new UploadError("x", { status: 415 })), t("custom.err.format"));
  assert.equal(customErrorMessage(new UploadError("x", { status: 0 })), t("custom.err.network"));
  assert.equal(customErrorMessage(new ApiError(409, { detail: "dup" })), t("custom.err.exists"));
  assert.equal(customErrorMessage(new ApiError(404, null)), t("custom.err.unknownMfr"));
  assert.equal(customErrorMessage(new ApiError(422, null)), t("custom.err.invalid"));
  assert.equal(customErrorMessage(new TypeError("Failed to fetch")), t("custom.err.network"));
  assert.equal(customErrorMessage(new ApiError(500, null)), t("custom.err.generic"));
});

test("jobFailureText always has a Swedish lead and appends the server message", () => {
  assert.ok(jobFailureText("ffprobe missing").startsWith(t("custom.jobFailed")));
  assert.ok(jobFailureText("ffprobe missing").includes("ffprobe missing"));
  assert.equal(jobFailureText(""), t("custom.jobFailed"));
});
