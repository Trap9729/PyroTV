import { getCategories, getPieces, play, thumbUrl } from "../api.js";
import { el, icon } from "../dom.js";
import { t } from "../i18n.js";
import { showToast } from "../toast.js";

const SEARCH_DEBOUNCE_MS = 200;

// Remembered across visits so returning from the player keeps the filter and scroll.
const SHOP_FS_MAX = 1.6; // rem; matches the CSS default
const SHOP_FS_ONE_LINE_MIN = 1.2; // rem; smaller than this, wrap to two lines instead
const SHOP_FS_TWO_LINES = 1.3; // rem; start size for two lines
const SHOP_FS_MIN = 0.95; // rem; below this the name is ellipsised instead

// Shrink the shop name until it fits its column; if one line gets too small,
// wrap to two lines and shrink from there. Ellipsis only as the last resort.
function fitShopName(node) {
  node.style.removeProperty("--shop-fs");
  node.classList.remove("two-lines");
  if (!node.isConnected || node.clientWidth === 0) return;
  const overflows = () => node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1;
  const shrink = (from, min) => {
    let fs = from;
    node.style.setProperty("--shop-fs", `${fs}rem`);
    while (overflows() && fs > min) {
      fs = Math.max(min, +(fs * 0.94).toFixed(3));
      node.style.setProperty("--shop-fs", `${fs}rem`);
    }
    return !overflows();
  };
  if (shrink(SHOP_FS_MAX, SHOP_FS_ONE_LINE_MIN)) {
    if (SHOP_FS_MAX === parseFloat(node.style.getPropertyValue("--shop-fs"))) node.style.removeProperty("--shop-fs");
    return;
  }
  node.classList.add("two-lines");
  shrink(SHOP_FS_TWO_LINES, SHOP_FS_MIN);
}

const memory = { chip: "all", q: "", scrollTop: 0 };

function chipQuery(chip) {
  if (chip === "popular") return { sort: "views" };
  if (chip.startsWith("cat:")) return { sort: "name", category: chip.slice(4) };
  return { sort: "name" };
}

export function renderGrid(root, ctx) {
  let disposed = false;
  let requestSeq = 0;
  let debounceTimer = null;
  let busy = false; // a play() is in flight
  let categories = [];
  let categoriesLoaded = false;

  // ---- skeleton ----
  const view = el("section", "grid-view");

  const top = el("header", "topbar");
  const brand = el("div", "brand");
  brand.append(icon("i-burst", "brand-mark"));
  const shopName = el("h1", "shop-name", ctx.state?.shop_name || t("app.name"));
  brand.append(shopName);

  const search = el("label", "search");
  search.append(icon("i-search", "search-icon"));
  const input = el("input", "search-input");
  Object.assign(input, {
    type: "search",
    placeholder: t("grid.search"),
    autocomplete: "off",
    spellcheck: false,
    value: memory.q,
  });
  input.setAttribute("aria-label", t("grid.search"));
  input.setAttribute("enterkeyhint", "search");
  input.setAttribute("autocapitalize", "off");
  const clear = el("button", "search-clear");
  clear.type = "button";
  clear.setAttribute("aria-label", t("grid.clearSearch"));
  clear.append(icon("i-close"));
  search.append(input, clear);

  const tools = el("div", "top-tools");
  const tv = el("span", "tv-pill");
  tv.append(icon("i-tv"), el("span", "", t("grid.tvConnected")));
  const gear = el("button", "icon-btn gear-btn");
  gear.type = "button";
  gear.setAttribute("aria-label", t("common.settings"));
  gear.append(icon("i-gear"));
  tools.append(tv, gear);

  top.append(brand, search, tools);

  const chipsBar = el("nav", "chips");
  chipsBar.setAttribute("aria-label", t("grid.filters"));

  const scroller = el("main", "tiles-scroll");
  const tiles = el("div", "tiles");
  const empty = el("div", "empty");
  empty.hidden = true;
  scroller.append(tiles, empty);

  view.append(top, chipsBar, scroller);
  root.append(view);

  // ---- helpers ----
  function updateTv() {
    tv.classList.toggle("on", !!ctx.state?.tv_connected);
  }
  function updateClear() {
    clear.classList.toggle("visible", input.value.length > 0);
  }

  function renderChips() {
    const defs = [
      ["all", t("grid.all")],
      ["popular", t("grid.popular")],
      ...categories.map((c) => [`cat:${c}`, c]),
    ];
    if (categoriesLoaded && !defs.some(([id]) => id === memory.chip)) {
      // The remembered category disappeared (e.g. manufacturer disabled).
      memory.chip = "all";
      load();
    }
    const frag = document.createDocumentFragment();
    for (const [id, label] of defs) {
      const b = el("button", "chip", label);
      b.type = "button";
      b.dataset.chip = id;
      b.setAttribute("aria-pressed", String(id === memory.chip));
      frag.append(b);
    }
    chipsBar.replaceChildren(frag);
    chipsBar.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function selectChip(id) {
    if (id === memory.chip) return;
    memory.chip = id;
    for (const b of chipsBar.children) {
      b.setAttribute("aria-pressed", String(b.dataset.chip === id));
    }
    chipsBar.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
    scroller.scrollTop = 0;
    load();
  }

  function makeTile(p) {
    const b = el("button", "tile");
    b.type = "button";
    b.dataset.key = p.key;
    const thumb = el("span", "thumb");
    if (p.has_image) {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.draggable = false;
      img.addEventListener("error", () => {
        img.remove();
        thumb.classList.add("ph");
        thumb.append(icon("i-burst", "ph-icon"));
      }, { once: true });
      img.src = thumbUrl(p.key);
      thumb.append(img);
    } else {
      thumb.classList.add("ph");
      thumb.append(icon("i-burst", "ph-icon"));
    }
    b.append(thumb, el("span", "tile-name", p.name));
    return b;
  }

  function renderPieces(pieces) {
    const frag = document.createDocumentFragment();
    for (const p of pieces) frag.append(makeTile(p));
    tiles.replaceChildren(frag);

    const q = input.value.trim();
    if (pieces.length) {
      empty.hidden = true;
      empty.replaceChildren();
      return;
    }
    empty.hidden = false;
    const box = el("div", "empty-box");
    box.append(icon("i-rocket", "empty-icon"));
    if (q) {
      box.append(el("h2", "", t("grid.noResults", { q })), el("p", "", t("grid.noResultsHint")));
    } else if (memory.chip !== "all" && memory.chip !== "popular") {
      box.append(el("h2", "", t("grid.noResultsCategory")), el("p", "", t("grid.noResultsHint")));
    } else {
      box.append(el("h2", "", t("grid.empty")), el("p", "", t("grid.emptyHint")));
      const go = el("button", "btn btn-primary", t("common.settings"));
      go.type = "button";
      go.addEventListener("click", () => ctx.navigate({ name: "settings" }));
      box.append(go);
    }
    empty.replaceChildren(box);
  }

  async function load({ restoreScroll = false } = {}) {
    const seq = ++requestSeq;
    const q = input.value.trim();
    view.classList.add("loading");
    try {
      const pieces = await getPieces({ ...chipQuery(memory.chip), q });
      if (disposed || seq !== requestSeq) return;
      renderPieces(pieces);
      if (restoreScroll) scroller.scrollTop = memory.scrollTop;
    } catch (err) {
      if (disposed || seq !== requestSeq) return;
      console.error("loading pieces failed", err);
      showToast(t("grid.loadError"));
      if (tiles.childElementCount === 0) {
        // Nothing to show at all: offer a retry instead of a blank screen.
        const box = el("div", "empty-box");
        box.append(icon("i-rocket", "empty-icon"), el("h2", "", t("grid.loadError")));
        const retry = el("button", "btn btn-primary", t("common.retry"));
        retry.type = "button";
        retry.addEventListener("click", () => load());
        box.append(retry);
        empty.replaceChildren(box);
        empty.hidden = false;
      }
    } finally {
      if (!disposed && seq === requestSeq) view.classList.remove("loading");
    }
  }

  async function loadCategories() {
    try {
      categories = await getCategories();
      categoriesLoaded = true;
    } catch (err) {
      console.error("loading categories failed", err);
      categories = [];
    }
    if (!disposed) renderChips();
  }

  async function onTileTap(tile) {
    if (busy) return;
    busy = true;
    tile.classList.add("starting");
    view.classList.add("busy");
    const key = tile.dataset.key;
    try {
      const start = await play(key);
      if (disposed) return;
      ctx.nowPlaying = start;
      memory.scrollTop = scroller.scrollTop;
      ctx.navigate({ name: "play", key: start?.key || key });
    } catch (err) {
      console.error("play failed", err);
      if (!disposed) showToast(t("grid.playError"));
    } finally {
      busy = false;
      if (!disposed) {
        tile.classList.remove("starting");
        view.classList.remove("busy");
      }
    }
  }

  // ---- events ----
  input.addEventListener("input", () => {
    updateClear();
    memory.q = input.value;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      scroller.scrollTop = 0;
      load();
    }, SEARCH_DEBOUNCE_MS);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      clearTimeout(debounceTimer);
      input.blur();
      load();
    }
  });
  clear.addEventListener("click", () => {
    input.value = "";
    memory.q = "";
    updateClear();
    clearTimeout(debounceTimer);
    scroller.scrollTop = 0;
    load();
  });
  chipsBar.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (b) selectChip(b.dataset.chip);
  });
  tiles.addEventListener("click", (e) => {
    const tile = e.target.closest(".tile");
    if (tile) onTileTap(tile);
  });
  gear.addEventListener("click", () => {
    memory.scrollTop = scroller.scrollTop;
    ctx.navigate({ name: "settings" });
  });

  const unsubTv = ctx.subscribe("tv", updateTv);
  const unsubState = ctx.subscribe("state", () => {
    shopName.textContent = ctx.state?.shop_name || t("app.name");
    fitShopName(shopName);
    updateTv();
  });
  const onResize = () => fitShopName(shopName);
  window.addEventListener("resize", onResize);

  // ---- initial paint ----
  updateTv();
  updateClear();
  renderChips();
  loadCategories();
  load({ restoreScroll: true });
  requestAnimationFrame(() => fitShopName(shopName)); // needs layout

  return () => {
    disposed = true;
    window.removeEventListener("resize", onResize);
    clearTimeout(debounceTimer);
    unsubTv();
    unsubState();
  };
}
