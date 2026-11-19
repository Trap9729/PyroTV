// Settings: one view for the touchscreen (sidebar + content) and the phone
// (top bar + scrolling chip row). Sections are built once and shown one at a
// time, so a running upload keeps going while you look at another section.
// All API text goes in via textContent.
import {
  getManufacturers,
  putSettings,
  setPieceEnabled,
  getStorage,
  getDiagnostics,
  backupExport,
  backupImport,
  setupReset,
} from "../api.js";
import { el, icon } from "../dom.js";
import { t } from "../i18n.js";
import { showToast } from "../toast.js";
import { confirmDialog, dismissConfirm } from "./confirm.js";
import { createCustomPanel } from "./custom-pieces.js";
import { createHotspotCard } from "./hotspot-card.js";
import { diffEnabled, groupByCategory, mergeEnabled, runLimited, SHOP_NAME_MAX } from "./wizard-logic.js";
import { formatBytes, storagePercent } from "./settings-logic.js";

const CONCURRENCY = 6;

function button(cls, text, onClick) {
  const b = el("button", cls, text);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function sw(label, on) {
  const b = el("button", "set-switch");
  b.type = "button";
  b.setAttribute("role", "switch");
  b.setAttribute("aria-label", label);
  b.setAttribute("aria-checked", String(on));
  b.append(el("span", "set-knob"));
  return b;
}

const paintSwitch = (b, on) => b.setAttribute("aria-checked", String(on));

export function renderSettings(root, ctx) {
  let alive = true;
  const panels = new Map(); // id -> { node, show, cleanup, stale }
  let activeId = "shop";

  // ----- skeleton -----
  const view = el("section", "set");
  const bar = el("header", "subbar set-top");
  const back = button("btn btn-ghost back-btn", "", () => ctx.navigate({ name: "grid" }));
  back.append(icon("i-back"), document.createTextNode(t("common.back")));
  bar.append(back, el("h1", "subbar-title", t("common.settings")));
  const main = el("div", "set-main");
  const nav = el("nav", "set-nav");
  nav.setAttribute("role", "tablist");
  nav.setAttribute("aria-label", t("common.settings"));
  const content = el("div", "set-content");
  main.append(nav, content);
  view.append(bar, main);
  root.append(view);

  const tabs = new Map();
  function addSection(id, label, build) {
    const tab = el("button", "set-tab", label);
    tab.type = "button";
    tab.id = `set-tab-${id}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `set-panel-${id}`);
    tab.addEventListener("click", () => select(id));
    nav.append(tab);
    tabs.set(id, tab);

    const node = el("section", "set-panel");
    node.id = `set-panel-${id}`;
    node.setAttribute("role", "tabpanel");
    node.setAttribute("aria-labelledby", tab.id);
    node.hidden = true;
    content.append(node);
    const api = build(node) ?? {};
    panels.set(id, { node, api });
  }

  function select(id) {
    if (!panels.has(id)) return;
    activeId = id;
    for (const [pid, p] of panels) {
      const on = pid === id;
      p.node.hidden = !on;
      const tab = tabs.get(pid);
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    }
    content.scrollTop = 0;
    tabs.get(id).scrollIntoView?.({ block: "nearest", inline: "nearest" });
    panels.get(id).api.show?.();
  }

  nav.addEventListener("keydown", (e) => {
    const keys = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
    if (!(e.key in keys)) return;
    const ids = [...tabs.keys()];
    const next = ids[(ids.indexOf(activeId) + keys[e.key] + ids.length) % ids.length];
    select(next);
    tabs.get(next).focus();
    e.preventDefault();
  });

  const heading = (text) => el("h2", "set-h", text);
  const markStale = (...ids) => {
    for (const id of ids) {
      const p = panels.get(id);
      if (p) p.api.stale = true;
    }
  };

  // ===== 1. shop name =====
  addSection("shop", t("settings.nav.shop"), (node) => {
    const card = el("form", "set-card");
    const input = el("input", "set-input");
    input.type = "text";
    input.id = "set-shop";
    input.maxLength = SHOP_NAME_MAX;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = t("wizard.shop.placeholder");
    input.value = ctx.state?.shop_name ?? "";
    const label = el("label", "set-label", t("wizard.shop.label"));
    label.htmlFor = input.id;
    const save = el("button", "btn btn-primary", t("settings.save"));
    save.type = "submit";
    let saved = input.value.trim();
    const check = () => {
      const v = input.value.trim();
      save.disabled = !v || v.length > SHOP_NAME_MAX || v === saved;
    };
    input.addEventListener("input", check);
    card.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (save.disabled) return;
      const v = input.value.trim();
      save.disabled = true;
      input.disabled = true;
      try {
        await putSettings({ shop_name: v });
        await ctx.refreshState();
        saved = v;
        input.value = v;
        if (alive) showToast(t("settings.saved"));
      } catch {
        if (alive) showToast(t("wizard.saveError"));
      } finally {
        if (alive) {
          input.disabled = false;
          check();
        }
      }
    });
    card.append(label, input, el("p", "set-note", t("wizard.shop.hint")), save);
    check();
    node.append(heading(t("settings.nav.shop")), card);
    return {
      // The stored name changed behind our back (backup import): show it and use it as the baseline.
      reset() {
        saved = String(ctx.state?.shop_name ?? "").trim();
        input.value = saved;
        check();
      },
    };
  });

  // ===== 2. manufacturers + pieces =====
  addSection("mfrs", t("settings.nav.mfrs"), (node) => {
    let mfrs = [];
    let chain = Promise.resolve();
    let openId = null;
    const api = { stale: true };
    const box = el("div", "set-mfrs");
    node.append(heading(t("settings.nav.mfrs")), el("p", "set-note", t("settings.mfrs.hint")), box);

    const enabledCount = (m) => m.pieces.reduce((n, p) => n + (p.enabled ? 1 : 0), 0);
    const metaText = (m) => t("settings.mfrs.active", { n: enabledCount(m), total: m.pieces.length });

    async function load() {
      box.replaceChildren(el("p", "set-status", t("wizard.loading")));
      try {
        mfrs = await getManufacturers();
      } catch {
        if (!alive) return;
        box.replaceChildren(el("p", "set-note", t("wizard.loadError")), button("btn btn-ghost", t("common.retry"), load));
        return;
      }
      if (!alive) return;
      api.stale = false;
      paint();
    }

    function paint() {
      box.replaceChildren();
      if (!mfrs.length) {
        box.append(el("p", "set-status", t("wizard.mfr.none")));
        return;
      }
      for (const m of mfrs) box.append(mfrRow(m));
    }

    function mfrRow(m) {
      const row = el("div", "set-mfr");
      row.dataset.id = m.id;
      row.classList.toggle("off", !m.enabled);
      const headEl = el("div", "set-mfr-head");
      const main = el("button", "set-mfr-main");
      main.type = "button";
      main.setAttribute("aria-expanded", "false");
      const text = el("span", "set-mfr-text");
      const nameRow = el("span", "set-mfr-namerow");
      nameRow.append(el("span", "set-mfr-name", m.name));
      if (m.custom) nameRow.append(el("span", "wiz-badge", t("wizard.mfr.custom")));
      const meta = el("span", "set-mfr-meta", metaText(m));
      text.append(nameRow, meta);
      main.append(text, icon("i-chev", "set-chev"));
      const toggle = sw(t("settings.mfrs.show", { name: m.name }), m.enabled);
      headEl.append(main, toggle);
      const panel = el("div", "set-mfr-body");
      panel.hidden = true;
      row.append(headEl, panel);

      main.addEventListener("click", () => {
        const opening = panel.hidden;
        for (const other of box.querySelectorAll(".set-mfr")) {
          if (other === row) continue;
          other.querySelector(".set-mfr-body").hidden = true;
          other.querySelector(".set-mfr-main").setAttribute("aria-expanded", "false");
          other.querySelector(".set-mfr-body").replaceChildren();
        }
        panel.hidden = !opening;
        main.setAttribute("aria-expanded", String(opening));
        openId = opening ? m.id : null;
        if (opening) renderPieces(m, panel, meta);
        else panel.replaceChildren();
      });

      toggle.addEventListener("click", () => {
        const prev = m.enabled;
        m.enabled = !prev;
        paintSwitch(toggle, m.enabled);
        row.classList.toggle("off", !m.enabled);
        chain = chain.then(async () => {
          try {
            // Written whole: merge against a fresh list so a manufacturer created
            // from a phone meanwhile is not silently switched off.
            const server = await getManufacturers();
            const known = mfrs.map((x) => x.id);
            const ids = mergeEnabled(server, known, mfrs.filter((x) => x.enabled).map((x) => x.id));
            await putSettings({ enabled_manufacturers: ids });
            if (!alive) return;
            for (const s of server) {
              if (known.includes(s.id)) continue;
              mfrs.push(s);
              if (box.isConnected) box.append(mfrRow(s));
            }
          } catch {
            m.enabled = prev;
            paintSwitch(toggle, prev);
            row.classList.toggle("off", !prev);
            if (alive) showToast(t("wizard.saveError"));
          }
        });
      });
      if (openId === m.id) main.click();
      return row;
    }

    function renderPieces(m, panel, meta) {
      panel.replaceChildren();
      if (!m.pieces.length) {
        panel.append(el("p", "set-note", t("wizard.pieces.empty")));
        return;
      }
      const tools = el("div", "set-piece-tools");
      const rows = new Map();
      const paintAll = () => {
        for (const [key, r] of rows) paintSwitch(r.sw, r.piece.enabled);
        meta.textContent = metaText(m);
      };
      const bulk = async (on) => {
        const cur = m.pieces.filter((p) => p.enabled).map((p) => p.key);
        const want = on ? m.pieces.map((p) => p.key) : [];
        const { enable, disable } = diffEnabled(cur, want);
        const jobs = [...enable.map((k) => [k, true]), ...disable.map((k) => [k, false])];
        if (!jobs.length) return;
        tools.classList.add("busy");
        for (const b of tools.querySelectorAll("button")) b.disabled = true;
        const byKey = new Map(m.pieces.map((p) => [p.key, p]));
        const failed = await runLimited(jobs, CONCURRENCY, async ([key, v]) => {
          await setPieceEnabled(key, v);
          byKey.get(key).enabled = v;
        });
        if (!alive) return;
        tools.classList.remove("busy");
        for (const b of tools.querySelectorAll("button")) b.disabled = false;
        paintAll();
        if (failed) showToast(t("wizard.saveError"));
      };
      tools.append(
        button("btn btn-ghost set-small", t("settings.mfrs.allOn"), () => bulk(true)),
        button("btn btn-ghost set-small", t("settings.mfrs.allOff"), () => bulk(false)),
      );
      const list = el("div", "set-pieces");
      const groups = groupByCategory(m.pieces);
      for (const g of groups) {
        const sec = el("div", "set-group");
        if (groups.length > 1) sec.append(el("h3", "set-group-name", g.category));
        for (const p of g.pieces) {
          const r = el("button", "set-piece");
          r.type = "button";
          r.dataset.key = p.key;
          r.setAttribute("role", "switch");
          r.setAttribute("aria-checked", String(p.enabled));
          r.append(el("span", "set-piece-name", p.name), el("span", "set-switch-vis", ""));
          r.lastChild.append(el("span", "set-knob"));
          rows.set(p.key, { sw: r, piece: p });
          sec.append(r);
        }
        list.append(sec);
      }
      list.addEventListener("click", async (e) => {
        const r = e.target.closest(".set-piece");
        if (!r || r.dataset.pending) return;
        const entry = rows.get(r.dataset.key);
        const prev = entry.piece.enabled;
        entry.piece.enabled = !prev;
        paintSwitch(r, !prev);
        meta.textContent = metaText(m);
        r.dataset.pending = "1";
        try {
          await setPieceEnabled(entry.piece.key, !prev);
        } catch {
          entry.piece.enabled = prev;
          paintSwitch(r, prev);
          meta.textContent = metaText(m);
          if (alive) showToast(t("wizard.saveError"));
        } finally {
          delete r.dataset.pending;
        }
      });
      panel.append(tools, list);
    }

    api.show = () => {
      if (api.stale) load();
    };
    return api;
  });

  // ===== 3. custom pieces =====
  addSection("custom", t("settings.nav.custom"), (node) => {
    const p = createCustomPanel(node, { onChanged: () => markStale("mfrs") });
    return { show: p.show, cleanup: p.cleanup };
  });

  // ===== 4. storage =====
  addSection("storage", t("settings.nav.storage"), (node) => {
    const card = el("div", "set-card");
    node.append(heading(t("settings.nav.storage")), card);
    return {
      async show() {
        card.replaceChildren(el("p", "set-status", t("wizard.loading")));
        let s;
        try {
          s = await getStorage();
        } catch {
          if (!alive) return;
          card.replaceChildren(el("p", "set-note", t("wizard.loadError")), button("btn btn-ghost", t("common.retry"), () => this.show()));
          return;
        }
        if (!alive) return;
        const pct = storagePercent(s.used_bytes, s.budget_bytes);
        const track = el("div", "set-bar set-bar-big");
        track.setAttribute("role", "progressbar");
        track.setAttribute("aria-label", t("settings.nav.storage"));
        track.setAttribute("aria-valuemin", "0");
        track.setAttribute("aria-valuemax", "100");
        track.setAttribute("aria-valuenow", String(Math.round(pct)));
        const fill = el("div", "set-bar-fill");
        fill.style.transform = `scaleX(${pct / 100})`;
        if (pct >= 90) fill.classList.add("warn");
        track.append(fill);
        const kv = (k, v) => {
          const r = el("div", "set-kv");
          r.append(el("span", "set-k", k), el("span", "set-v", v));
          return r;
        };
        card.replaceChildren(
          el("p", "set-big", t("settings.storage.used", { used: formatBytes(s.used_bytes), budget: formatBytes(s.budget_bytes) })),
          track,
          kv(t("settings.storage.free"), formatBytes(s.free_bytes)),
          kv(t("settings.storage.total"), formatBytes(s.total_bytes)),
          el("p", "set-note", t("settings.storage.hint")),
        );
      },
    };
  });

  // ===== 5. wifi =====
  addSection("wifi", t("settings.nav.wifi"), (node) => {
    const hot = createHotspotCard({ autoOff: true });
    node.append(heading(t("settings.nav.wifi")), el("p", "set-note", t("settings.wifi.hint")), hot.node);
    return {
      show: () => hot.load(),
      cleanup: () => hot.destroy(),
    };
  });

  // ===== 6. diagnostics =====
  addSection("diag", t("settings.nav.diag"), (node) => {
    const card = el("div", "set-card");
    const head = el("div", "set-row-head");
    head.append(heading(t("settings.nav.diag")));
    const refresh = button("btn btn-ghost set-small", t("settings.diag.refresh"), () => api.show());
    head.append(refresh);
    node.append(head, card);
    const api = {
      async show() {
        card.replaceChildren(el("p", "set-status", t("wizard.loading")));
        let d;
        try {
          d = await getDiagnostics();
        } catch {
          if (!alive) return;
          card.replaceChildren(el("p", "set-note", t("wizard.loadError")));
          return;
        }
        if (!alive) return;
        const out = [];
        const kv = (k, v) => {
          const r = el("div", "set-kv");
          r.append(el("span", "set-k", k), el("span", "set-v", v));
          return r;
        };
        out.push(kv(t("settings.storage.free"), formatBytes(d.free_bytes)));
        const missing = d.missing_media ?? [];
        const sec1 = el("div", "set-diag");
        sec1.append(el("h3", "set-h3", t("settings.diag.missing")));
        if (missing.length) {
          sec1.append(el("p", "set-note", t("settings.diag.missingHint")));
          const ul = el("ul", "set-diag-list");
          for (const name of missing) ul.append(el("li", "", name));
          sec1.append(ul);
        } else {
          sec1.append(el("p", "set-ok", t("settings.diag.noneMissing")));
        }
        out.push(sec1);
        const sec2 = el("div", "set-diag");
        sec2.append(el("h3", "set-h3", t("settings.diag.catalogue")));
        if (d.catalogue_error) sec2.append(el("p", "set-status error set-left", String(d.catalogue_error)));
        else sec2.append(el("p", "set-ok", t("settings.diag.catalogueOk")));
        out.push(sec2);
        card.replaceChildren(...out);
      },
    };
    return api;
  });

  // ===== 7. backup =====
  addSection("backup", t("settings.nav.backup"), (node) => {
    const card = el("div", "set-card");
    const msg = el("p", "set-status set-left");
    msg.setAttribute("role", "status");
    const exportBtn = button("btn btn-ghost set-wide", t("settings.backup.export"));
    const importBtn = button("btn btn-ghost set-wide", t("settings.backup.import"));
    const say = (text, kind = "") => {
      msg.textContent = text;
      msg.className = `set-status set-left ${kind}`;
    };
    const failText = (err) => {
      const detail = err?.body?.detail;
      if (err?.status === 409 && detail === "no_usb") return t("settings.backup.noUsb");
      if (err?.status === 404 && detail === "no_backup") return t("settings.backup.noBackup");
      if (err?.status === 422) return t("settings.backup.invalid");
      return t("settings.backup.error");
    };
    const run = async (fn) => {
      exportBtn.disabled = true;
      importBtn.disabled = true;
      try {
        await fn();
      } finally {
        if (alive) {
          exportBtn.disabled = false;
          importBtn.disabled = false;
        }
      }
    };
    exportBtn.addEventListener("click", () =>
      run(async () => {
        say(t("wizard.saving"));
        try {
          await backupExport();
          if (alive) say(t("settings.backup.exported"), "ok");
        } catch (err) {
          if (alive) say(failText(err), "error");
        }
      }),
    );
    importBtn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: t("settings.backup.confirmTitle"),
        message: t("settings.backup.confirmMsg"),
        okLabel: t("settings.backup.import"),
        danger: true,
      });
      if (!ok || !alive) return;
      await run(async () => {
        say(t("wizard.loading"));
        try {
          await backupImport();
          await ctx.refreshState();
          markStale("mfrs");
          panels.get("shop")?.api.reset?.();
          if (alive) say(t("settings.backup.imported"), "ok");
        } catch (err) {
          if (alive) say(failText(err), "error");
        }
      });
    });
    card.append(el("p", "set-note", t("settings.backup.hint")), exportBtn, importBtn, msg);
    node.append(heading(t("settings.nav.backup")), card);
  });

  // ===== 8. rerun wizard =====
  addSection("wizard", t("settings.nav.wizard"), (node) => {
    const card = el("div", "set-card");
    const go = button("btn btn-ghost set-wide", t("settings.wizard.run"));
    go.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: t("settings.wizard.confirmTitle"),
        message: t("settings.wizard.confirmMsg"),
        okLabel: t("settings.wizard.run"),
      });
      if (!ok || !alive) return;
      go.disabled = true;
      try {
        await setupReset();
        await ctx.refreshState();
        if (alive) ctx.navigate({ name: "wizard" });
      } catch {
        if (alive) {
          go.disabled = false;
          showToast(t("wizard.saveError"));
        }
      }
    });
    card.append(el("p", "set-note", t("settings.wizard.hint")), go);
    node.append(heading(t("settings.nav.wizard")), card);
  });

  select(activeId);

  return () => {
    alive = false;
    dismissConfirm();
    for (const p of panels.values()) p.api.cleanup?.();
  };
}
