// First-run / re-run setup wizard. Full-screen, step list is dynamic:
// shop -> manufacturers -> pieces:<id>... -> custom -> finish.
// All API-provided text goes in via textContent; the only markup parsed is the
// backend-generated QR SVG (DOMParser + importNode).
import {
  getManufacturers,
  putSettings,
  setPieceEnabled,
  listCustom,
  setupComplete,
} from "../api.js";
import { el, icon } from "../dom.js";
import { t } from "../i18n.js";
import { showToast } from "../toast.js";
import { createHotspotCard } from "./hotspot-card.js";
import {
  wizardSteps,
  diffEnabled,
  canAdvance,
  groupByCategory,
  runLimited,
  SHOP_NAME_MAX,
} from "./wizard-logic.js";

export { wizardSteps };

const CONCURRENCY = 6;

export function renderWizard(root, ctx) {
  let alive = true;
  let token = 0; // bumped on every step render; async work checks it
  let busy = false;
  let hotCard = null;

  const S = {
    loaded: false,
    mfrs: [],
    shopName: ctx.state?.shop_name ?? "",
    selected: new Set(),
    ticks: new Map(), // manufacturer id -> Set of keys the user wants enabled
    backend: new Map(), // manufacturer id -> Set of keys enabled on the server
    stepId: "shop",
  };

  // ----- skeleton -----
  const view = el("section", "wiz");
  const head = el("header", "wiz-head");
  const brand = el("div", "wiz-brand");
  brand.append(icon("i-burst", "wiz-mark"), el("span", "wiz-app", t("app.name")));
  const stepLabel = el("span", "wiz-steplabel");
  head.append(brand, stepLabel);
  const bar = el("div", "wiz-bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", t("wizard.progress"));
  const barFill = el("div", "wiz-bar-fill");
  bar.append(barFill);
  const body = el("main", "wiz-body");
  const foot = el("footer", "wiz-foot");
  const backBtn = el("button", "btn btn-ghost wiz-btn");
  backBtn.type = "button";
  backBtn.append(icon("i-back"), document.createTextNode(t("common.back")));
  const nextBtn = el("button", "btn btn-primary wiz-btn wiz-next");
  nextBtn.type = "button";
  foot.append(backBtn, nextBtn);
  view.append(head, bar, body, foot);
  root.append(view);

  // ----- helpers -----
  const selectedIds = () => S.mfrs.filter((m) => S.selected.has(m.id)).map((m) => m.id);
  const steps = () => wizardSteps(selectedIds());
  const mfrById = (id) => S.mfrs.find((m) => m.id === id);
  const stepState = () => ({ shopName: S.shopName, selected: [...S.selected] });

  function setBusy(b, label) {
    busy = b;
    view.classList.toggle("busy", b);
    updateNav(label);
  }

  function updateNav(savingLabel) {
    const list = steps();
    const idx = Math.max(0, list.indexOf(S.stepId));
    const last = S.stepId === "finish";
    stepLabel.textContent = t("wizard.step", { n: idx + 1, total: list.length });
    barFill.style.transform = `scaleX(${(idx + 1) / list.length})`;
    bar.setAttribute("aria-valuenow", String(idx + 1));
    bar.setAttribute("aria-valuemax", String(list.length));
    backBtn.disabled = busy || idx === 0;
    nextBtn.hidden = last;
    nextBtn.textContent = busy && savingLabel ? savingLabel : t("wizard.next");
    nextBtn.disabled = busy || !S.loaded || !canAdvance(S.stepId, stepState());
  }

  function title(text, hint) {
    const wrap = el("div", "wiz-title");
    wrap.append(el("h1", "", text));
    if (hint) wrap.append(el("p", "wiz-hint", hint));
    return wrap;
  }

  function button(cls, text, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  // ----- loading -----
  async function load() {
    body.replaceChildren(el("p", "wiz-status", t("wizard.loading")));
    try {
      S.mfrs = await getManufacturers();
    } catch {
      if (!alive) return;
      const card = el("div", "wiz-card wiz-center");
      card.append(
        el("p", "wiz-status", t("wizard.loadError")),
        button("btn btn-primary", t("common.retry"), load),
      );
      body.replaceChildren(card);
      updateNav();
      return;
    }
    if (!alive) return;
    const configured = S.mfrs.some((m) => m.enabled);
    S.selected = new Set(S.mfrs.filter((m) => m.enabled).map((m) => m.id));
    for (const m of S.mfrs) {
      const on = new Set(m.pieces.filter((p) => p.enabled).map((p) => p.key));
      S.backend.set(m.id, on);
      S.ticks.set(m.id, configured ? new Set(on) : new Set(m.pieces.map((p) => p.key)));
    }
    S.loaded = true;
    show();
  }

  // ----- steps -----
  function show() {
    token++;
    hotCard?.destroy();
    hotCard = null;
    body.scrollTop = 0;
    const id = S.stepId;
    if (id === "shop") showShop();
    else if (id === "manufacturers") showManufacturers();
    else if (id.startsWith("pieces:")) showPieces(id.slice("pieces:".length));
    else if (id === "custom") showCustom();
    else showFinish();
    updateNav();
  }

  function showShop() {
    const card = el("div", "wiz-card");
    card.append(title(t("wizard.shop.title"), t("wizard.shop.hint")));
    const label = el("label", "wiz-label", t("wizard.shop.label"));
    const input = el("input", "wiz-input");
    input.type = "text";
    input.id = "wiz-shop";
    input.maxLength = SHOP_NAME_MAX;
    input.value = S.shopName;
    input.placeholder = t("wizard.shop.placeholder");
    input.autocomplete = "off";
    input.spellcheck = false;
    label.htmlFor = input.id;
    input.addEventListener("input", () => {
      S.shopName = input.value;
      updateNav();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && canAdvance("shop", stepState()) && !busy) next();
    });
    card.append(label, input);
    body.replaceChildren(card);
  }

  function showManufacturers() {
    const wrap = el("div", "wiz-wide");
    wrap.append(title(t("wizard.mfr.title"), t("wizard.mfr.hint")));
    if (!S.mfrs.length) {
      wrap.append(el("p", "wiz-status", t("wizard.mfr.none")));
      body.replaceChildren(wrap);
      return;
    }
    const tools = el("div", "wiz-tools");
    const cards = new Map();
    const sync = () => {
      for (const [id, c] of cards) c.setAttribute("aria-pressed", String(S.selected.has(id)));
      updateNav();
    };
    tools.append(
      button("btn btn-ghost wiz-small", t("wizard.selectAll"), () => {
        S.selected = new Set(S.mfrs.map((m) => m.id));
        sync();
      }),
      button("btn btn-ghost wiz-small", t("wizard.selectNone"), () => {
        S.selected = new Set();
        sync();
      }),
    );
    const grid = el("div", "wiz-mfrs");
    for (const m of S.mfrs) {
      const c = el("button", "wiz-mfr");
      c.type = "button";
      const name = el("span", "wiz-mfr-name", m.name);
      const meta = el("span", "wiz-mfr-meta", t("wizard.mfr.count", { n: m.pieces.length }));
      c.append(el("span", "wiz-mfr-check"), name, meta);
      if (m.custom) c.append(el("span", "wiz-badge", t("wizard.mfr.custom")));
      c.addEventListener("click", () => {
        if (S.selected.has(m.id)) S.selected.delete(m.id);
        else S.selected.add(m.id);
        sync();
      });
      cards.set(m.id, c);
      grid.append(c);
    }
    wrap.append(tools, grid);
    body.replaceChildren(wrap);
    sync();
  }

  function showPieces(id) {
    const m = mfrById(id);
    const wrap = el("div", "wiz-wide wiz-pieces");
    if (!m) {
      body.replaceChildren(wrap);
      return;
    }
    const ticks = S.ticks.get(id);
    const total = m.pieces.length;
    const count = el("span", "wiz-count");
    const heading = title(m.name, t("wizard.pieces.hint"));
    const tools = el("div", "wiz-tools wiz-sticky");
    const setAll = (on) => {
      ticks.clear();
      if (on) for (const p of m.pieces) ticks.add(p.key);
      for (const row of list.querySelectorAll(".wiz-row")) paintRow(row, ticks.has(row.dataset.key));
      refreshCount();
    };
    tools.append(
      count,
      button("btn btn-ghost wiz-small", t("wizard.selectAll"), () => setAll(true)),
      button("btn btn-ghost wiz-small", t("wizard.selectNone"), () => setAll(false)),
    );
    const list = el("div", "wiz-list");

    function paintRow(row, on) {
      row.setAttribute("aria-checked", String(on));
    }
    function refreshCount() {
      count.textContent = t("wizard.pieces.count", { n: ticks.size, total });
    }

    if (!total) {
      wrap.append(heading, el("p", "wiz-status", t("wizard.pieces.empty")));
      body.replaceChildren(wrap);
      return;
    }

    const groups = groupByCategory(m.pieces);
    for (const g of groups) {
      const sec = el("section", "wiz-group");
      const gh = el("div", "wiz-group-head");
      const setGroup = (on) => {
        for (const p of g.pieces) {
          if (on) ticks.add(p.key);
          else ticks.delete(p.key);
        }
        for (const row of sec.querySelectorAll(".wiz-row")) paintRow(row, ticks.has(row.dataset.key));
        refreshCount();
      };
      gh.append(
        el("h2", "wiz-group-name", g.category),
        button("wiz-link", t("wizard.selectAll"), () => setGroup(true)),
        button("wiz-link", t("wizard.selectNone"), () => setGroup(false)),
      );
      if (groups.length > 1) sec.append(gh); // a lone group needs no header
      for (const p of g.pieces) {
        const row = el("button", "wiz-row");
        row.type = "button";
        row.setAttribute("role", "checkbox");
        row.dataset.key = p.key;
        row.append(el("span", "wiz-row-name", p.name), el("span", "wiz-check"));
        paintRow(row, ticks.has(p.key));
        sec.append(row);
      }
      list.append(sec);
    }
    // One delegated listener instead of hundreds.
    list.addEventListener("click", (e) => {
      const row = e.target.closest(".wiz-row");
      if (!row) return;
      const key = row.dataset.key;
      const on = !ticks.has(key);
      if (on) ticks.add(key);
      else ticks.delete(key);
      paintRow(row, on);
      refreshCount();
    });
    refreshCount();
    wrap.append(heading, tools, list);
    body.replaceChildren(wrap);
  }

  // Hotspot card (shared with Settings). Data incl. the PIN lives only in the DOM.
  function showCustom() {
    const my = token;
    const wrap = el("div", "wiz-wide");
    wrap.append(title(t("wizard.custom.title"), t("wizard.custom.intro")));
    hotCard = createHotspotCard({ onBusy: (b) => alive && setBusy(b) });
    const existing = el("div", "wiz-card wiz-existing");
    wrap.append(hotCard.node, existing);
    body.replaceChildren(wrap);
    hotCard.load();

    existing.append(el("h2", "wiz-sub", t("wizard.custom.listTitle")));
    listCustom().then(
      (items) => {
        if (!alive || my !== token) return;
        if (!items.length) {
          existing.append(el("p", "wiz-note", t("wizard.custom.listEmpty")));
          return;
        }
        const ul = el("ul", "wiz-custom-list");
        for (const c of items) {
          const li = el("li");
          li.append(el("span", "wiz-cl-name", c.name), el("span", "wiz-cl-mfr", c.manufacturer_name));
          ul.append(li);
        }
        existing.append(ul);
      },
      () => {
        if (alive && my === token) existing.append(el("p", "wiz-note", t("wizard.loadError")));
      },
    );
  }

  function showFinish() {
    const my = token;
    const card = el("div", "wiz-card wiz-finish");
    card.append(title(t("wizard.finish.title"), t("wizard.finish.hint")));
    const ids = selectedIds();
    let pieces = 0;
    for (const id of ids) pieces += S.backend.get(id)?.size ?? 0;
    const dl = el("dl", "wiz-summary");
    const add = (k, v) => {
      const row = el("div");
      row.append(el("dt", "", k), el("dd", "", String(v)));
      dl.append(row);
      return row.lastChild;
    };
    add(t("wizard.finish.shop"), S.shopName.trim());
    add(t("wizard.finish.manufacturers"), ids.length);
    add(t("wizard.finish.pieces"), pieces);
    const customDd = add(t("wizard.finish.custom"), "–");
    listCustom().then(
      (items) => {
        if (alive && my === token) customDd.textContent = String(items.length);
      },
      () => {},
    );
    const done = button("btn btn-primary wiz-big", t("wizard.finish.done"), async () => {
      if (busy) return;
      setBusy(true);
      try {
        await setupComplete();
        await ctx.refreshState();
        if (!alive) return;
        ctx.navigate({ name: "grid" });
      } catch {
        showToast(t("wizard.finish.error"));
      } finally {
        if (alive) setBusy(false);
      }
    });
    card.append(dl, done);
    body.replaceChildren(card);
  }

  // ----- applying a step -----
  async function applyPieces(id) {
    const cur = S.backend.get(id);
    const want = S.ticks.get(id);
    const { enable, disable } = diffEnabled(cur, want);
    const jobs = [...enable.map((k) => [k, true]), ...disable.map((k) => [k, false])];
    const failed = await runLimited(jobs, CONCURRENCY, async ([key, on]) => {
      await setPieceEnabled(key, on);
      if (on) cur.add(key);
      else cur.delete(key);
    });
    // Ticks must mirror what the server really has.
    S.ticks.set(id, new Set(cur));
    return failed === 0;
  }

  async function apply(id) {
    if (id === "shop") {
      await putSettings({ shop_name: S.shopName.trim() });
      await ctx.refreshState();
    } else if (id === "manufacturers") {
      await putSettings({ enabled_manufacturers: selectedIds() });
    } else if (id.startsWith("pieces:")) {
      if (!(await applyPieces(id.slice("pieces:".length)))) throw new Error("pieces");
    }
  }

  async function next() {
    if (busy || !canAdvance(S.stepId, stepState())) return;
    const id = S.stepId;
    const label = id.startsWith("pieces:") ? t("wizard.saving") : null;
    setBusy(true, label ?? t("wizard.saving"));
    try {
      await apply(id);
    } catch {
      if (!alive) return;
      showToast(t("wizard.saveError"));
      setBusy(false);
      if (id.startsWith("pieces:")) show(); // redraw ticks from server reality
      return;
    }
    if (!alive) return;
    const list = steps();
    S.stepId = list[Math.min(list.indexOf(id) + 1, list.length - 1)];
    busy = false;
    view.classList.remove("busy");
    show();
  }

  function back() {
    if (busy) return;
    const list = steps();
    const idx = list.indexOf(S.stepId);
    if (idx <= 0) return;
    S.stepId = list[idx - 1];
    show();
  }

  nextBtn.addEventListener("click", next);
  backBtn.addEventListener("click", back);
  updateNav();
  load();

  return () => {
    alive = false;
    token++;
    hotCard?.destroy();
  };
}
