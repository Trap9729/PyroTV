// STUB: replaced by the settings view in Task 13.
import { t } from "../i18n.js";
import { renderStub } from "./stub.js";

export function renderSettings(root, ctx) {
  renderStub(root, t("common.settings"), () => ctx.navigate({ name: "grid" }));
}
