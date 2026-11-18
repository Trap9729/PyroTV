// Settings > "Egna fyrverkerier": list / edit / delete custom pieces and the
// add flow (resumable uploads -> POST /api/custom -> poll the transcode job).
// All API text goes in via textContent.
import {
  listCustom,
  getManufacturers,
  createCustom,
  getCustomJob,
  updateCustom,
  deleteCustom,
  thumbUrl,
} from "../api.js";
import { el } from "../dom.js";
import { t } from "../i18n.js";
import { showToast } from "../toast.js";
import { uploadFile } from "../uploader.js";
import { confirmDialog } from "./confirm.js";
import { groupByCategory } from "./wizard-logic.js";
import {
  NEW_VALUE,
  NAME_MAX,
  formatBytes,
  validateCustomForm,
  customErrorMessage,
  jobFailureText,
} from "./settings-logic.js";

const POLL_MS = 1500;
const POLL_MAX_FAILS = 5;

function button(cls, text, onClick) {
  const b = el("button", cls, text);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function field(labelText, control, id) {
  const wrap = el("div", "set-field");
  const label = el("label", "set-label", labelText);
  control.id = id;
  label.htmlFor = id;
  wrap.append(label, control);
  return wrap;
}

function textInput(placeholder) {
  const i = el("input", "set-input");
  i.type = "text";
  i.maxLength = NAME_MAX;
  i.autocomplete = "off";
  i.spellcheck = false;
  if (placeholder) i.placeholder = placeholder;
  return i;
}

function option(value, text) {
  const o = el("option", "", text);
  o.value = value;
  return o;
}

function progressBar(label) {
  const wrap = el("div", "set-prog");
  const head = el("div", "set-prog-head");
  const name = el("span", "set-prog-name", label);
  const pct = el("span", "set-prog-pct", "");
  head.append(name, pct);
  const track = el("div", "set-bar");
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", label);
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = el("div", "set-bar-fill");
  track.append(fill);
  wrap.append(head, track);
  wrap.hidden = true;
  return {
    node: wrap,
    set(f) {
      const p = Math.round(Math.max(0, Math.min(1, f)) * 100);
      wrap.hidden = false;
      track.classList.remove("indeterminate");
      fill.style.transform = `scaleX(${p / 100})`;
      track.setAttribute("aria-valuenow", String(p));
      pct.textContent = `${p} %`;
    },
    reset() {
      wrap.hidden = true;
      fill.style.transform = "scaleX(0)";
      pct.textContent = "";
      track.removeAttribute("aria-valuenow");
    },
  };
}

function filePicker(labelText, accept, onChange) {
  const wrap = el("div", "set-field");
  wrap.append(el("span", "set-label", labelText));
  const row = el("div", "set-file");
  const input = el("input", "set-file-input");
  input.type = "file";
  input.accept = accept;
  input.tabIndex = -1;
  input.setAttribute("aria-label", labelText);
  const pick = button("btn btn-ghost set-pick", t("custom.choose"), () => input.click());
  const info = el("span", "set-file-info", t("custom.noFile"));
  const clear = button("set-link", t("custom.removeFile"), () => {
    input.value = "";
    onChange(null);
    paint();
  });
  clear.hidden = true;
  row.append(input, pick, info, clear);
  wrap.append(row);
  const paint = () => {
    const f = input.files?.[0] ?? null;
    info.textContent = f ? `${f.name} (${formatBytes(f.size)})` : t("custom.noFile");
    info.classList.toggle("has", !!f);
    clear.hidden = !f;
  };
  input.addEventListener("change", () => {
    onChange(input.files?.[0] ?? null);
    paint();
  });
  return {
    node: wrap,
    get file() {
      return input.files?.[0] ?? null;
    },
    setDisabled(d) {
      input.disabled = d;
      pick.disabled = d;
      clear.disabled = d;
    },
    reset() {
      input.value = "";
      paint();
    },
  };
}

// env: { onChanged() } is called when pieces were added/changed/removed.
export function createCustomPanel(host, env = {}) {
  let alive = true;
  let busy = false;
  let items = [];
  let mfrs = [];
  let abort = null;
  let pollTimer = null;
  const freshCache = () => ({ video: null, videoId: null, image: null, imageId: null, partial: {} });
  let cache = freshCache();

  // ----- header + form shell -----
  const head = el("div", "set-row-head");
  head.append(el("h2", "set-h", t("settings.nav.custom")));
  const addBtn = button("btn btn-primary set-add", t("custom.add"));
  addBtn.setAttribute("aria-expanded", "false");
  head.append(addBtn);

  const form = el("form", "set-card set-form");
  form.hidden = true;
  form.noValidate = true;
  form.append(el("h3", "set-h3", t("custom.formTitle")));

  const mfrSel = el("select", "set-select");
  const newMfr = textInput(t("custom.newMfrPlaceholder"));
  const mfrField = field(t("custom.manufacturer"), mfrSel, "cp-mfr");
  const newMfrField = field(t("custom.newMfr"), newMfr, "cp-newmfr");

  const nameInput = textInput(t("custom.namePlaceholder"));
  const nameField = field(t("custom.name"), nameInput, "cp-name");

  const catSel = el("select", "set-select");
  const newCat = textInput(t("custom.newCatPlaceholder"));
  const catField = field(t("custom.category"), catSel, "cp-cat");
  const newCatField = field(t("custom.newCat"), newCat, "cp-newcat");

  const video = filePicker(t("custom.video"), "video/*,.mkv,.avi", () => {
    cache.video = null;
    cache.videoId = null;
    delete cache.partial.video;
    sync();
  });
  const image = filePicker(t("custom.image"), "image/*", () => {
    cache.image = null;
    cache.imageId = null;
    delete cache.partial.image;
    sync();
  });

  const vBar = progressBar(t("custom.video"));
  const iBar = progressBar(t("custom.image"));
  const status = el("p", "set-status");
  status.setAttribute("role", "status");
  const jobBar = el("div", "set-bar indeterminate");
  jobBar.append(el("div", "set-bar-fill"));
  jobBar.hidden = true;
  const note = el("p", "set-note", t("custom.note"));

  const actions = el("div", "set-actions");
  const cancelBtn = button("btn btn-ghost", t("common.cancel"));
  const submitBtn = el("button", "btn btn-primary set-submit", t("custom.upload"));
  submitBtn.type = "submit";
  actions.append(cancelBtn, submitBtn);

  form.append(
    mfrField,
    newMfrField,
    nameField,
    catField,
    newCatField,
    video.node,
    image.node,
    vBar.node,
    iBar.node,
    jobBar,
    status,
    note,
    actions,
  );

  const listCard = el("div", "set-card set-list-card");
  const listBox = el("div", "set-list-box");
  listCard.append(listBox);

  host.append(head, form, listCard);

  // ----- form logic -----
  const selectedMfr = () => mfrs.find((m) => m.id === mfrSel.value) ?? null;

  function categoriesOf(m) {
    if (!m) return [];
    const set = new Set((m.categories ?? []).filter((c) => c && String(c).trim()));
    if (!set.size) for (const g of groupByCategory(m.pieces ?? [])) if (g.category) set.add(g.category);
    return [...set].sort((a, b) => a.localeCompare(b, "sv"));
  }

  function fillMfrs() {
    const keep = mfrSel.value;
    mfrSel.replaceChildren();
    for (const m of mfrs) mfrSel.append(option(m.id, m.custom ? `${m.name} (${t("custom.ownTag")})` : m.name));
    mfrSel.append(option(NEW_VALUE, t("custom.newMfrOption")));
    mfrSel.value = [...mfrSel.options].some((o) => o.value === keep) ? keep : mfrs.length ? mfrs[0].id : NEW_VALUE;
    fillCats();
  }

  function fillCats() {
    const keep = catSel.value;
    catSel.replaceChildren();
    for (const c of categoriesOf(selectedMfr())) catSel.append(option(c, c));
    catSel.append(option(NEW_VALUE, t("custom.newCatOption")));
    catSel.value = [...catSel.options].some((o) => o.value === keep) ? keep : catSel.options[0].value;
    sync();
  }

  function sync() {
    const mfrNew = mfrSel.value === NEW_VALUE;
    newMfrField.hidden = !mfrNew;
    // A brand-new manufacturer has no categories yet: only free text.
    catField.hidden = mfrNew;
    const catNew = mfrNew || catSel.value === NEW_VALUE;
    newCatField.hidden = !catNew;
    const v = validateCustomForm(currentForm());
    submitBtn.disabled = busy || !v.ok;
  }

  function currentForm() {
    const mfrNew = mfrSel.value === NEW_VALUE;
    return {
      mfrChoice: mfrSel.value,
      newMfr: newMfr.value,
      name: nameInput.value,
      catChoice: mfrNew ? NEW_VALUE : catSel.value,
      newCat: newCat.value,
      video: video.file,
    };
  }

  function setBusy(b) {
    busy = b;
    for (const c of [mfrSel, newMfr, nameInput, catSel, newCat, cancelBtn]) c.disabled = b;
    video.setDisabled(b);
    image.setDisabled(b);
    form.classList.toggle("busy", b);
    sync();
  }

  function setStatus(text, kind = "") {
    status.textContent = text;
    status.className = `set-status ${kind}`;
  }

  function resetForm() {
    nameInput.value = "";
    newMfr.value = "";
    newCat.value = "";
    video.reset();
    image.reset();
    vBar.reset();
    iBar.reset();
    jobBar.hidden = true;
    setStatus("");
    cache = freshCache();
    fillMfrs();
  }

  function openForm(open) {
    form.hidden = !open;
    addBtn.setAttribute("aria-expanded", String(open));
    addBtn.hidden = open;
    if (open) {
      loadMfrs().then(() => {
        if (alive) nameInput.focus({ preventScroll: true });
      });
      form.scrollIntoView({ block: "nearest" });
    }
  }

  async function loadMfrs() {
    try {
      mfrs = await getManufacturers();
    } catch {
      if (alive) showToast(t("wizard.loadError"));
    }
    if (alive) fillMfrs();
  }

  addBtn.addEventListener("click", () => openForm(true));
  cancelBtn.addEventListener("click", () => {
    if (busy) return;
    resetForm();
    openForm(false);
  });
  mfrSel.addEventListener("change", () => {
    catSel.value = ""; // a different manufacturer: start from its first category
    fillCats();
  });
  catSel.addEventListener("change", sync);
  for (const c of [newMfr, nameInput, newCat]) c.addEventListener("input", sync);

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!busy) submit();
  });

  async function submit() {
    const f = currentForm();
    if (!validateCustomForm(f).ok) return;
    const videoFile = video.file;
    const imageFile = image.file;
    abort = new AbortController();
    const signal = abort.signal;
    setBusy(true);
    setStatus("");
    jobBar.hidden = true;
    vBar.reset();
    iBar.reset();
    const onRetry = () => alive && setStatus(t("custom.retrying"), "warn");
    // An unfinished upload of the same File is resumed on "try again", not restarted.
    const resumeOpts = (kindKey, file) => ({
      signal,
      onRetry,
      resumeId: cache.partial[kindKey]?.file === file ? cache.partial[kindKey].id : undefined,
      onUploadId: (id) => {
        cache.partial[kindKey] = { file, id };
      },
    });
    try {
      if (cache.video === videoFile && cache.videoId) {
        vBar.set(1);
      } else {
        setStatus(t("custom.uploadingVideo"));
        vBar.set(0);
        cache.videoId = await uploadFile(videoFile, "video", { ...resumeOpts("video", videoFile), onProgress: (p) => vBar.set(p) });
        delete cache.partial.video;
        cache.video = videoFile;
      }
      let imageId;
      if (imageFile) {
        if (cache.image === imageFile && cache.imageId) {
          iBar.set(1);
        } else {
          setStatus(t("custom.uploadingImage"));
          iBar.set(0);
          cache.imageId = await uploadFile(imageFile, "image", { ...resumeOpts("image", imageFile), onProgress: (p) => iBar.set(p) });
          delete cache.partial.image;
          cache.image = imageFile;
        }
        imageId = cache.imageId;
      }
      const body = {
        name: f.name.trim(),
        category: (f.catChoice === NEW_VALUE ? f.newCat : f.catChoice).trim(),
        video_upload_id: cache.videoId,
      };
      if (f.mfrChoice === NEW_VALUE) body.new_manufacturer_name = f.newMfr.trim();
      else body.manufacturer_id = f.mfrChoice;
      if (imageId) body.image_upload_id = imageId;
      setStatus(t("custom.creating"));
      let job;
      try {
        job = await createCustom(body);
      } catch (err) {
        if (err?.status === 404 || err?.status === 422) cache = freshCache();
        throw err;
      }
      if (!alive) return;
      poll(job.job_id);
    } catch (err) {
      if (!alive || signal.aborted) return;
      setStatus(customErrorMessage(err), "error");
      setBusy(false);
    }
  }

  function poll(jobId) {
    setStatus(t("custom.processing"), "busy");
    jobBar.hidden = false;
    let fails = 0;
    const tick = async () => {
      pollTimer = null;
      if (!alive) return;
      try {
        const job = await getCustomJob(jobId);
        if (!alive) return;
        fails = 0;
        if (job.state === "done") {
          jobBar.hidden = true;
          showToast(t("custom.added"));
          resetForm();
          setBusy(false);
          openForm(false);
          env.onChanged?.();
          await reload();
          return;
        }
        if (job.state === "error") {
          jobBar.hidden = true;
          setStatus(jobFailureText(job.message), "error");
          cache = freshCache();
          setBusy(false);
          return;
        }
      } catch {
        if (!alive) return;
        if (++fails >= POLL_MAX_FAILS) {
          jobBar.hidden = true;
          setStatus(t("custom.err.network"), "error");
          setBusy(false);
          return;
        }
      }
      pollTimer = setTimeout(tick, POLL_MS);
    };
    pollTimer = setTimeout(tick, POLL_MS);
  }

  // ----- list -----
  function paintList() {
    listBox.replaceChildren();
    listBox.append(el("h3", "set-h3", t("custom.listTitle")));
    if (!items.length) {
      listBox.append(el("p", "set-note", t("custom.listEmpty")));
      return;
    }
    const ul = el("ul", "set-items");
    for (const c of items) ul.append(itemRow(c));
    listBox.append(ul);
  }

  function itemRow(c) {
    const li = el("li", "set-item");
    const view = () => {
      li.replaceChildren();
      if (c.has_image) {
        const img = el("img", "set-thumb");
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        img.src = thumbUrl(c.key);
        li.append(img);
      } else {
        li.append(el("span", "set-thumb set-thumb-empty"));
      }
      const text = el("div", "set-item-text");
      text.append(el("span", "set-item-name", c.name), el("span", "set-item-meta", `${c.manufacturer_name} · ${c.category}`));
      const act = el("div", "set-item-actions");
      act.append(
        button("btn btn-ghost set-small", t("custom.edit"), edit),
        button("btn btn-ghost set-small btn-danger-ghost", t("custom.delete"), remove),
      );
      li.append(text, act);
    };
    const edit = () => {
      li.replaceChildren();
      const box = el("div", "set-edit");
      const n = textInput();
      n.value = c.name;
      const cat = textInput();
      cat.value = c.category;
      const err = el("p", "set-status error");
      err.hidden = true;
      const save = el("button", "btn btn-primary set-small", t("settings.save"));
      save.type = "button";
      const cancel = button("btn btn-ghost set-small", t("common.cancel"), view);
      const check = () => {
        save.disabled = !n.value.trim() || !cat.value.trim();
      };
      n.addEventListener("input", check);
      cat.addEventListener("input", check);
      save.addEventListener("click", async () => {
        save.disabled = true;
        cancel.disabled = true;
        try {
          await updateCustom(c.id, { name: n.value.trim(), category: cat.value.trim() });
          if (!alive) return;
          showToast(t("settings.saved"));
          env.onChanged?.();
          reload();
        } catch (e2) {
          if (!alive) return;
          err.textContent = customErrorMessage(e2);
          err.hidden = false;
          cancel.disabled = false;
          check();
        }
      });
      const row = el("div", "set-edit-row");
      row.append(field(t("custom.name"), n, `cp-en-${c.id}`), field(t("custom.category"), cat, `cp-ec-${c.id}`));
      const bar = el("div", "set-actions");
      bar.append(cancel, save);
      box.append(row, err, bar);
      li.append(box);
      n.focus({ preventScroll: true });
      check();
    };
    const remove = async () => {
      const ok = await confirmDialog({
        title: t("custom.deleteTitle", { name: c.name }),
        message: t("custom.deleteMsg"),
        okLabel: t("custom.delete"),
        danger: true,
      });
      if (!ok || !alive) return;
      try {
        await deleteCustom(c.id);
        if (!alive) return;
        showToast(t("custom.deleted"));
        env.onChanged?.();
        reload();
      } catch {
        if (alive) showToast(t("wizard.saveError"));
      }
    };
    view();
    return li;
  }

  async function reload() {
    try {
      items = await listCustom();
    } catch {
      if (!alive) return;
      listBox.replaceChildren(el("p", "set-note", t("wizard.loadError")), button("btn btn-ghost", t("common.retry"), reload));
      return;
    }
    if (alive) paintList();
  }

  fillMfrs();
  sync();

  return {
    show() {
      if (!busy) reload();
    },
    cleanup() {
      alive = false;
      abort?.abort();
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
    },
  };
}
