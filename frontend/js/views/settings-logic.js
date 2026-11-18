// Pure settings helpers (no DOM, testable in Node).
import { t } from "../i18n.js";

export const NEW_VALUE = "__new__";
export const NAME_MAX = 200;
export const AUTOOFF_OPTIONS = [0, 15, 30, 60, 120];

// 12.4 GiB -> "12,4 GB" (binary steps, Swedish decimal comma).
export function formatBytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return "0 B";
  if (v < 1024) return `${Math.round(v)} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let x = v / 1024;
  let u = 0;
  while (x >= 1024 && u < units.length - 1) {
    x /= 1024;
    u++;
  }
  return `${x.toFixed(1).replace(".", ",")} ${units[u]}`;
}

export function storagePercent(used, budget) {
  if (!(budget > 0)) return 0;
  return Math.max(0, Math.min(100, (used / budget) * 100));
}

// form: { mfrChoice, newMfr, name, catChoice, newCat, video }
export function validateCustomForm(form) {
  const errors = [];
  const name = String(form.name ?? "").trim();
  if (!name || name.length > NAME_MAX) errors.push("name");
  if (form.mfrChoice === NEW_VALUE) {
    const m = String(form.newMfr ?? "").trim();
    if (!m || m.length > NAME_MAX) errors.push("manufacturer");
  } else if (!form.mfrChoice) {
    errors.push("manufacturer");
  }
  if (form.catChoice === NEW_VALUE || !form.catChoice) {
    const c = String(form.newCat ?? "").trim();
    if (!c || c.length > NAME_MAX) errors.push("category");
  }
  if (!form.video) errors.push("video");
  return { ok: errors.length === 0, errors };
}

// Any error thrown while uploading / creating a custom piece -> Swedish sentence.
export function customErrorMessage(err) {
  const status = typeof err?.status === "number" ? err.status : 0;
  switch (status) {
    case 507:
      return t("custom.err.disk");
    case 413:
      return t("custom.err.size");
    case 415:
      return t("custom.err.format");
    case 409:
      return t("custom.err.exists");
    case 404:
      return t("custom.err.unknownMfr");
    case 422:
      return t("custom.err.invalid");
    case 0:
      return t("custom.err.network");
    default:
      return t("custom.err.generic");
  }
}

export function jobFailureText(serverMessage) {
  const lead = t("custom.jobFailed");
  const msg = String(serverMessage ?? "").trim();
  return msg ? `${lead} ${msg}` : lead;
}
