import { play, playerPause, playerSeek, playerStop, recordView } from "../api.js";
import { el, icon } from "../dom.js";
import { createDoubleTap, SEEK_STEP } from "../gestures.js";
import { t } from "../i18n.js";
import { shouldLeaveOnStopped } from "./player-logic.js";
import { showToast } from "../toast.js";
import { renderGrid } from "./grid.js";

const BAR_HIDE_MS = 3000;
const VIEW_AFTER_S = 5; // seconds of actual playing time before a view is counted
const MOVE_SLOP_PX = 12;
const LABEL_RESET_MS = 700;
const RIPPLE_MS = 450;

function fmtTime(s) {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const total = Math.floor(s);
  const m = Math.floor(total / 60);
  return `${m}:${String(total % 60).padStart(2, "0")}`;
}

function iconButton(className, iconId, label) {
  const b = el("button", className);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.append(icon(iconId));
  return b;
}

export function renderPlayer(root, ctx, key) {
  let disposed = false;
  let inner = null;

  let left = false;
  function leave(notice) {
    if (left) return;
    left = true;
    if (ctx.nowPlaying?.key === key) ctx.nowPlaying = null;
    if (notice) showToast(notice);
    ctx.navigate({ name: "grid" });
  }

  async function start() {
    let np = ctx.nowPlaying && ctx.nowPlaying.key === key ? ctx.nowPlaying : null;
    if (!np) {
      // e.g. the page was reloaded on #/play/<key>: ask the backend again.
      try {
        np = await play(key);
      } catch (err) {
        console.error("play failed", err);
        if (!disposed) leave(t("grid.playError"));
        return;
      }
      if (disposed) return;
      ctx.nowPlaying = np;
    }
    inner = np.mode === "tv" ? renderTvMode(root, ctx, np, leave) : renderLocalMode(root, np, leave);
  }

  start();

  return () => {
    disposed = true;
    if (inner) inner();
    inner = null;
  };
}

// ---------------------------------------------------------------- local mode

function renderLocalMode(root, np, leave) {
  const ac = new AbortController();
  const { signal } = ac;
  const on = (target, type, fn, opts) => target.addEventListener(type, fn, { ...opts, signal });

  const stage = el("section", "player-stage");

  const video = el("video", "player-video");
  video.playsInline = true;
  video.preload = "auto";
  video.controls = false;
  video.disablePictureInPicture = true;
  video.setAttribute("controlslist", "nodownload noplaybackrate nofullscreen noremoteplayback");

  const layer = el("div", "gesture-layer");
  const fx = el("div", "fx");
  const label = el("div", "seek-label");
  label.setAttribute("aria-hidden", "true");
  fx.append(label);

  // bottom control bar
  const bar = el("div", "player-bar");
  const barTitle = el("div", "bar-title", np.title);
  const row = el("div", "bar-row");
  const toggle = iconButton("bar-btn", "i-play", t("player.play"));
  const elapsed = el("span", "bar-time", "0:00");
  const seek = el("input", "bar-seek");
  Object.assign(seek, { type: "range", min: 0, max: 1000, step: 1, value: 0 });
  seek.setAttribute("aria-label", t("player.seekBar"));
  const total = el("span", "bar-time", "0:00");
  row.append(toggle, elapsed, seek, total);
  bar.append(barTitle, row);

  const exit = iconButton("player-exit", "i-close", t("player.exit"));

  const bigPlay = iconButton("big-play", "i-play", t("player.play"));
  bigPlay.hidden = true;

  stage.append(video, layer, fx, bar, bigPlay, exit);
  root.append(stage);

  // ---- state ----
  let barVisible = false;
  let hideTimer = null;
  let scrubbing = false;
  let labelTimer = null;
  let labelTotal = 0;
  let lastTime = null;
  let played = 0;
  let viewSent = false;
  let finished = false;
  let lastTap = { x: 0, y: 0 };
  let shownElapsed = "";
  let shownTotal = "";
  const timers = new Set(); // ripple fallbacks; cleared on teardown

  const stageWidth = () => stage.clientWidth || window.innerWidth;

  function finish() {
    if (finished) return;
    finished = true;
    leave();
  }

  // ---- control bar ----
  function setBar(visible) {
    barVisible = visible;
    bar.classList.toggle("show", visible);
    clearTimeout(hideTimer);
    hideTimer = null;
    if (visible && !video.paused && !scrubbing) {
      hideTimer = setTimeout(() => setBar(false), BAR_HIDE_MS);
    }
  }

  function updatePlayState() {
    const paused = video.paused;
    toggle.replaceChildren(icon(paused ? "i-play" : "i-pause"));
    toggle.setAttribute("aria-label", paused ? t("player.play") : t("player.pause"));
    // Visible while paused; counts down to hide while playing.
    if (paused) setBar(true);
    else if (barVisible) setBar(true);
  }

  function updateProgress() {
    const d = video.duration;
    const cur = video.currentTime;
    const e = fmtTime(cur);
    if (e !== shownElapsed) {
      shownElapsed = e;
      elapsed.textContent = e;
    }
    const tt = fmtTime(d);
    if (tt !== shownTotal) {
      shownTotal = tt;
      total.textContent = tt;
    }
    if (!scrubbing) {
      const p = Number.isFinite(d) && d > 0 ? Math.min(1, cur / d) : 0;
      seek.value = String(Math.round(p * 1000));
      seek.style.setProperty("--p", `${p * 100}%`);
    }
  }

  // ---- seeking with ripple + accumulated label ----
  function ripple() {
    const r = el("span", "ripple");
    const rect = stage.getBoundingClientRect();
    r.style.left = `${lastTap.x}px`;
    r.style.top = `${lastTap.y - rect.top}px`;
    fx.append(r);
    const done = () => r.remove();
    r.addEventListener("animationend", done, { once: true });
    const fallback = setTimeout(() => {
      timers.delete(fallback);
      done();
    }, RIPPLE_MS + 200); // fallback if animations are disabled
    timers.add(fallback);
  }

  function showLabel(delta) {
    if (Math.sign(labelTotal) !== Math.sign(delta)) labelTotal = 0;
    labelTotal += delta;
    const sign = labelTotal < 0 ? "−" : "+";
    label.textContent = t("player.seekLabel", { value: `${sign}${Math.abs(labelTotal)}` });
    label.classList.toggle("left", labelTotal < 0);
    label.classList.add("show");
    clearTimeout(labelTimer);
    labelTimer = setTimeout(() => {
      label.classList.remove("show");
      labelTotal = 0;
    }, LABEL_RESET_MS);
  }

  function onSeek(delta) {
    const d = video.duration;
    let next = video.currentTime + delta;
    if (next < 0) next = 0;
    if (Number.isFinite(d) && next >= d) {
      finish();
      return;
    }
    video.currentTime = next;
    ripple();
    showLabel(delta);
    updateProgress();
  }

  const tap = createDoubleTap({
    width: stageWidth,
    onSeek,
    onToggle: () => setBar(!barVisible),
  });

  // ---- gesture layer: pointer events, single finger, no drags ----
  const pointers = new Map(); // id -> {x, y, moved}
  let multi = false;
  on(layer, "pointerdown", (e) => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, moved: false });
    if (pointers.size > 1) multi = true;
  });
  on(layer, "pointermove", (e) => {
    const p = pointers.get(e.pointerId);
    if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > MOVE_SLOP_PX) p.moved = true;
  });
  on(layer, "pointerup", (e) => {
    const p = pointers.get(e.pointerId);
    pointers.delete(e.pointerId);
    if (!p) return;
    const ok = !multi && !p.moved;
    if (pointers.size === 0) multi = false;
    if (!ok) return;
    const rect = stage.getBoundingClientRect();
    lastTap = { x: e.clientX, y: e.clientY };
    tap({ x: e.clientX - rect.left });
  });
  on(layer, "pointercancel", (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size === 0) multi = false;
  });

  // ---- controls ----
  function playPause() {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  }
  on(toggle, "click", () => {
    playPause();
    setBar(true);
  });
  on(exit, "click", () => leave());
  on(bigPlay, "click", () => {
    video.play().then(
      () => {
        bigPlay.hidden = true;
      },
      () => {},
    );
  });

  on(seek, "input", () => {
    scrubbing = true;
    clearTimeout(hideTimer);
    const d = video.duration;
    if (Number.isFinite(d) && d > 0) {
      const p = Number(seek.value) / 1000;
      seek.style.setProperty("--p", `${p * 100}%`);
      video.currentTime = Math.min(d, p * d);
      elapsed.textContent = fmtTime(video.currentTime);
    }
  });
  on(seek, "change", () => {
    scrubbing = false;
    setBar(true);
    lastTime = video.currentTime;
  });

  // ---- video events ----
  on(video, "play", () => {
    bigPlay.hidden = true;
    updatePlayState();
  });
  on(video, "pause", updatePlayState);
  on(video, "durationchange", updateProgress);
  on(video, "loadedmetadata", updateProgress);
  on(video, "seeked", () => {
    lastTime = video.currentTime;
    updateProgress();
  });
  on(video, "timeupdate", () => {
    const cur = video.currentTime;
    if (!video.paused && !video.seeking && lastTime !== null) {
      const dt = cur - lastTime;
      // Only normal forward progress counts; jumps (seeks) are skipped.
      if (dt > 0 && dt < 1.5) played += dt;
    }
    lastTime = cur;
    if (!viewSent && played >= VIEW_AFTER_S) {
      viewSent = true;
      recordView(np.key).catch((err) => console.error("recordView failed", err));
    }
    updateProgress();
  });
  on(video, "ended", finish);
  on(video, "error", () => {
    if (finished) return;
    console.error("video error", video.error);
    finished = true;
    leave(t("player.videoError"));
  });

  // ---- go ----
  video.src = np.video_url;
  updatePlayState();
  setBar(true);
  const started = video.play();
  if (started && typeof started.catch === "function") {
    started.catch(() => {
      // Autoplay with sound was refused: ask for a tap. (An abort from teardown is ignored.)
      if (!signal.aborted) bigPlay.hidden = false;
    });
  }

  return () => {
    ac.abort();
    clearTimeout(hideTimer);
    clearTimeout(labelTimer);
    tap.cancel();
    for (const id of timers) clearTimeout(id);
    timers.clear();
    // Release the decoder and its buffers; this runs on a kiosk for days on 1 GB.
    video.pause();
    video.removeAttribute("src");
    video.load();
    stage.remove();
  };
}

// ------------------------------------------------------------------ TV mode

function renderTvMode(root, ctx, np, leave) {
  const gridCleanup = renderGrid(root, ctx);

  const popup = el("aside", "tv-popup");
  popup.setAttribute("role", "dialog");
  popup.setAttribute("aria-label", np.title);

  const head = el("div", "tv-head");
  head.append(icon("i-tv", "tv-head-icon"));
  const heading = el("div", "tv-heading");
  heading.append(el("div", "tv-title", np.title), el("div", "tv-hint", t("player.tvHint")));
  head.append(heading);

  const progress = el("div", "tv-progress");
  const fill = el("span", "tv-fill");
  progress.append(fill);
  const time = el("div", "tv-time", "0:00 / 0:00");

  const controls = el("div", "tv-controls");
  const back = el("button", "tv-btn tv-seek", t("player.seekLabel", { value: "−10" }));
  back.type = "button";
  back.setAttribute("aria-label", t("player.seekBack"));
  const pause = iconButton("tv-btn tv-pause", "i-pause", t("player.pause"));
  const fwd = el("button", "tv-btn tv-seek", t("player.seekLabel", { value: "+10" }));
  fwd.type = "button";
  fwd.setAttribute("aria-label", t("player.seekForward"));
  const exit = iconButton("tv-btn tv-exit", "i-close", t("player.exit"));
  controls.append(back, pause, fwd, exit);

  popup.append(head, progress, time, controls);
  root.append(popup);

  let state = "playing";
  function renderState() {
    const playing = state === "playing";
    pause.replaceChildren(icon(playing ? "i-pause" : "i-play"));
    pause.setAttribute("aria-label", playing ? t("player.pause") : t("player.play"));
  }

  function command(fn) {
    return fn().catch((err) => {
      console.error("player command failed", err);
      showToast(t("player.commandError"));
    });
  }

  back.addEventListener("click", () => command(() => playerSeek(-SEEK_STEP)));
  fwd.addEventListener("click", () => command(() => playerSeek(SEEK_STEP)));
  pause.addEventListener("click", () => {
    state = state === "playing" ? "paused" : "playing"; // optimistic; the next event confirms
    renderState();
    command(playerPause);
  });
  exit.addEventListener("click", () => {
    playerStop().catch((err) => console.error("stop failed", err));
    leave();
  });

  const unsubPlayer = ctx.subscribe("player", (ev) => {
    if (ev.state === "playing" || ev.state === "paused") {
      state = ev.state;
      renderState();
    }
    const pos = Number(ev.position) || 0;
    const dur = Number(ev.duration) || 0;
    time.textContent = `${fmtTime(pos)} / ${fmtTime(dur)}`;
    fill.style.transform = `scaleX(${dur > 0 ? Math.min(1, pos / dur) : 0})`;
  });
  const unsubStopped = ctx.subscribe("stopped", (ev) => {
    // Checked when the event arrives: a newer playback may have superseded this view.
    const d = shouldLeaveOnStopped({ viewKey: np.key, nowPlayingKey: ctx.nowPlaying?.key, reason: ev.reason });
    if (d.leave) leave(d.notice ?? undefined);
  });

  return () => {
    unsubPlayer();
    unsubStopped();
    popup.remove();
    gridCleanup?.();
  };
}
