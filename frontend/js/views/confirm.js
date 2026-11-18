// Minimal modal confirm built on <dialog>: the browser traps focus, makes the
// rest of the page inert and cancels on Escape. Resolves true (OK) / false.
import { el } from "../dom.js";
import { t } from "../i18n.js";

let open = null; // { dlg, finish }

export function confirmDialog({ title, message, okLabel, danger = false }) {
  dismissConfirm();
  return new Promise((resolve) => {
    const dlg = el("dialog", "dlg");
    dlg.setAttribute("aria-labelledby", "dlg-title");
    dlg.setAttribute("aria-describedby", "dlg-msg");
    const h = el("h2", "dlg-title", title);
    h.id = "dlg-title";
    const p = el("p", "dlg-msg", message);
    p.id = "dlg-msg";
    const row = el("div", "dlg-actions");
    const cancel = el("button", "btn btn-ghost dlg-btn", t("common.cancel"));
    cancel.type = "button";
    const ok = el("button", `btn dlg-btn ${danger ? "btn-danger" : "btn-primary"}`, okLabel ?? t("common.ok"));
    ok.type = "button";
    row.append(cancel, ok);
    dlg.append(h, p, row);

    const previous = document.activeElement;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      if (open?.dlg === dlg) open = null;
      if (dlg.open) dlg.close();
      dlg.remove();
      if (previous && previous.isConnected) previous.focus?.({ preventScroll: true });
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(false));
    ok.addEventListener("click", () => finish(true));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(false);
    });
    document.body.append(dlg);
    open = { dlg, finish };
    dlg.showModal();
    cancel.focus(); // the safe choice has focus
  });
}

// Close a dialog left open when the view goes away.
export function dismissConfirm() {
  if (open) open.finish(false);
}
