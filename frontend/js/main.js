import { getState, openEvents } from "./api.js";
import { t } from "./i18n.js";
import { buildRoute, parseRoute } from "./router.js";
import { renderGrid } from "./views/grid.js";
import { renderPlayer } from "./views/player.js";
import { renderWizard } from "./views/wizard.js";
import { renderSettings } from "./views/settings.js";

const root = document.getElementById("app");

// Kiosk hygiene: no long-press context menu (except in text fields), and a
// passive touchstart listener so :active press states show on touch.
document.addEventListener("contextmenu", (e) => {
  if (!e.target.closest?.("input, textarea")) e.preventDefault();
});
document.addEventListener("touchstart", () => {}, { passive: true });

const listeners = new Map(); // type -> Set<fn>
let events = null;
let cleanup = null;

function dispatch(ev) {
  const set = listeners.get(ev?.type);
  if (!set) return;
  for (const fn of [...set]) {
    try {
      fn(ev);
    } catch (err) {
      console.error("event listener failed", err);
    }
  }
}

const ctx = {
  state: null,
  nowPlaying: null,
  navigate(route) {
    const hash = buildRoute(route);
    if (location.hash === hash) render();
    else location.hash = hash;
  },
  subscribe(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
    return () => listeners.get(type)?.delete(fn);
  },
  async refreshState() {
    const state = await getState();
    ctx.state = state;
    dispatch({ type: "state", state });
    return state;
  },
};

function render() {
  if (cleanup) {
    try {
      cleanup();
    } catch (err) {
      console.error("view cleanup failed", err);
    }
    cleanup = null;
  }
  root.replaceChildren();
  const route = parseRoute(location.hash);
  root.dataset.view = route.name;
  let result;
  switch (route.name) {
    case "play":
      result = renderPlayer(root, ctx, route.key);
      break;
    case "wizard":
      result = renderWizard(root, ctx);
      break;
    case "settings":
      result = renderSettings(root, ctx);
      break;
    default:
      result = renderGrid(root, ctx);
  }
  cleanup = typeof result === "function" ? result : null;
}

function renderBootError() {
  root.replaceChildren();
  root.dataset.view = "error";
  const panel = document.createElement("div");
  panel.className = "panel-center";
  const card = document.createElement("div");
  card.className = "card boot-error";
  card.innerHTML = '<svg class="boot-error-icon" aria-hidden="true"><use href="#i-burst"/></svg>';
  const h = document.createElement("h1");
  h.textContent = t("boot.errorTitle");
  const p = document.createElement("p");
  p.textContent = t("boot.errorHint");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-primary";
  btn.textContent = t("common.retry");
  btn.addEventListener("click", () => {
    btn.disabled = true;
    boot();
  });
  card.append(h, p, btn);
  panel.append(card);
  root.append(panel);
}

async function boot() {
  try {
    ctx.state = await getState();
  } catch (err) {
    console.error("boot failed", err);
    renderBootError();
    return;
  }
  if (!events) {
    events = openEvents((ev) => {
      if (ev?.type === "tv" && ctx.state) ctx.state.tv_connected = !!ev.connected;
      dispatch(ev);
    });
  }
  if (!ctx.state.setup_done && parseRoute(location.hash).name !== "wizard") {
    history.replaceState(null, "", buildRoute({ name: "wizard" }));
  }
  window.removeEventListener("hashchange", render);
  window.addEventListener("hashchange", render);
  render();
}

boot();
