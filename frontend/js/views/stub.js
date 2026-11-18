// Temporary placeholder view used by the player/wizard/settings stubs until
// their real implementations land (Tasks 11-13). Delete with the last stub.
import { t } from "../i18n.js";

export function renderStub(root, title, onBack) {
  const view = document.createElement("section");
  view.className = "stub-view";

  const bar = document.createElement("header");
  bar.className = "subbar";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "btn btn-ghost back-btn";
  back.innerHTML = '<svg aria-hidden="true"><use href="#i-back"/></svg>';
  back.append(document.createTextNode(t("common.back")));
  back.addEventListener("click", onBack);
  const h = document.createElement("h1");
  h.className = "subbar-title";
  h.textContent = title;
  bar.append(back, h);

  const body = document.createElement("div");
  body.className = "panel-center";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = '<svg class="stub-icon" aria-hidden="true"><use href="#i-rocket"/></svg>';
  const p = document.createElement("p");
  p.className = "stub-text";
  p.textContent = t("stub.soon");
  card.append(p);
  body.append(card);

  view.append(bar, body);
  root.append(view);
}
