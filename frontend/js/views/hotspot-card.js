// Wi-Fi hotspot card shared by the setup wizard and Settings. Data (including the
// PIN) is rendered straight into the DOM and not retained. All API text goes in via
// textContent; the only markup parsed is the backend-generated QR SVG.
import { getHotspot, setHotspot, putSettings } from "../api.js";
import { el } from "../dom.js";
import { t } from "../i18n.js";
import { showToast } from "../toast.js";
import { formatPin } from "./wizard-logic.js";
import { AUTOOFF_OPTIONS } from "./settings-logic.js";

function button(cls, text, onClick) {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function qrNode(svgText, label) {
  if (!svgText) return null;
  // The backend SVG carries no xmlns; without it XML parsing yields non-SVG elements.
  const xml = /xmlns=/.test(svgText.slice(0, 300))
    ? svgText
    : svgText.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  const doc = new DOMParser().parseFromString(xml, "image/svg+xml");
  const svg = doc.documentElement;
  if (!svg || svg.localName !== "svg" || doc.querySelector("parsererror")) return null;
  const node = document.importNode(svg, true);
  if (!node.hasAttribute("viewBox")) {
    const w = parseFloat(node.getAttribute("width"));
    const h = parseFloat(node.getAttribute("height"));
    if (w > 0 && h > 0) node.setAttribute("viewBox", `0 0 ${w} ${h}`);
  }
  node.removeAttribute("width");
  node.removeAttribute("height");
  node.classList.add("wiz-qr-svg");
  node.setAttribute("role", "img");
  node.setAttribute("aria-label", label);
  const box = el("div", "wiz-qr");
  box.append(node);
  return box;
}

// options.onBusy(bool): called while a switch on/off request runs.
// options.autoOff: true adds the auto-off minutes select (Settings only).
export function createHotspotCard({ onBusy = () => {}, autoOff = false } = {}) {
  const node = el("div", "wiz-card wiz-hot");
  let alive = true;
  let working = false;
  let autoOffMinutes = 30; // last known server value

  const render = (h) => {
    if (!alive) return;
    if (typeof h?.autooff_minutes === "number") autoOffMinutes = h.autooff_minutes;
    node.replaceChildren(...nodesFor(h));
  };

  const toggle = async (on) => {
    if (working || !alive) return;
    working = true;
    onBusy(true);
    node.replaceChildren(el("p", "wiz-status", on ? t("wizard.custom.starting") : t("wizard.custom.stopping")));
    try {
      render(await setHotspot(on));
    } catch {
      if (alive) {
        showToast(t("wizard.custom.error"));
        try {
          render(await getHotspot());
        } catch {
          render(null);
        }
      }
    } finally {
      working = false;
      onBusy(false);
    }
  };

  const autoOffNode = () => {
    const wrap = el("div", "wiz-kv set-autooff");
    const label = el("label", "wiz-k", t("settings.wifi.autooff"));
    const sel = el("select", "set-select");
    sel.id = "set-autooff";
    label.htmlFor = sel.id;
    for (const m of AUTOOFF_OPTIONS) {
      const o = el("option", "", m === 0 ? t("settings.wifi.never") : t("settings.wifi.minutes", { n: m }));
      o.value = String(m);
      sel.append(o);
    }
    if (!AUTOOFF_OPTIONS.includes(autoOffMinutes)) {
      const o = el("option", "", t("settings.wifi.minutes", { n: autoOffMinutes }));
      o.value = String(autoOffMinutes);
      sel.append(o);
    }
    sel.value = String(autoOffMinutes);
    sel.addEventListener("change", async () => {
      const next = Number(sel.value);
      sel.disabled = true;
      try {
        await putSettings({ hotspot_autooff_minutes: next });
        autoOffMinutes = next;
        showToast(t("settings.saved"));
      } catch {
        sel.value = String(autoOffMinutes);
        showToast(t("wizard.saveError"));
      } finally {
        sel.disabled = false;
      }
    });
    wrap.append(label, sel);
    return wrap;
  };

  const offNodes = () => {
    const f = document.createDocumentFragment();
    f.append(
      el("p", "wiz-status", t("wizard.custom.off")),
      button("btn btn-primary wiz-big", t("wizard.custom.start"), () => toggle(true)),
      el("p", "wiz-note", t("wizard.custom.newPin")),
    );
    if (autoOff) f.append(autoOffNode());
    return f;
  };

  const nodesFor = (h) => {
    if (!h?.on) return [offNodes()];
    const f = document.createDocumentFragment();
    const row = (label, value, cls) => {
      const r = el("div", "wiz-kv");
      r.append(el("span", "wiz-k", label), el("span", `wiz-v ${cls || ""}`, value));
      return r;
    };
    // One section per step, each with its own code beside it. The page code sits well
    // below the Wi-Fi code so the two are not mixed up when scanning.
    const section = (infoNodes, svg, cap, label) => {
      const sec = el("div", "wiz-hot-sec");
      const info = el("div", "wiz-hot-info");
      info.append(...infoNodes);
      sec.append(info);
      const qr = qrNode(svg, label);
      if (qr) {
        const item = el("div", "wiz-qr-item");
        item.append(qr, el("span", "wiz-qr-cap", cap));
        sec.append(item);
      }
      return sec;
    };
    f.append(
      section(
        [
          el("p", "wiz-steps", t("wizard.custom.step1")),
          row(t("wizard.custom.ssid"), h.ssid ?? ""),
          row(t("wizard.custom.pin"), formatPin(h.pin), "wiz-pin"),
        ],
        h.qr_svg,
        t("wizard.custom.capWifi"),
        t("wizard.custom.qrWifi"),
      ),
      section(
        [
          el("p", "wiz-steps", t("wizard.custom.step2")),
          row(t("wizard.custom.url"), h.url ?? ""),
          ...(h.ip_url ? [row(t("wizard.custom.ipUrl"), h.ip_url, "wiz-v-small")] : []),
        ],
        h.url_qr_svg,
        t("wizard.custom.capSite"),
        t("wizard.custom.qr"),
      ),
    );
    const foot = el("div", "wiz-hot-foot");
    foot.append(el("p", "wiz-note", t("wizard.custom.newPin")));
    if (autoOff) foot.append(autoOffNode());
    foot.append(button("btn btn-ghost wiz-big", t("wizard.custom.stop"), () => toggle(false)));
    f.append(foot);
    return [f];
  };

  const load = () => {
    node.replaceChildren(el("p", "wiz-status", t("wizard.loading")));
    getHotspot().then(render, () => {
      if (!alive) return;
      showToast(t("wizard.custom.error"));
      render(null);
    });
  };

  return {
    node,
    load,
    destroy() {
      alive = false;
    },
  };
}
