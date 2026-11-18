// STUB: replaced by the setup wizard in Task 12.
import { t } from "../i18n.js";
import { renderStub } from "./stub.js";

export function renderWizard(root, ctx) {
  renderStub(root, t("stub.wizard"), () => ctx.navigate({ name: "grid" }));
}
