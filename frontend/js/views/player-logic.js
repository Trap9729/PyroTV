import { t } from "../i18n.js";

// Seconds of TV playback position before a TV play counts as a view.
export const VIEW_AFTER_S = 5;

// SSE player/stopped events carry the key of the TV playback they describe. An
// event for another key (a newer playback replaced this one) is not for this view.
// Events without a key (older backend) are accepted.
export function eventIsForView(eventKey, viewKey) {
  return !eventKey || eventKey === viewKey;
}

// Pure decision for a `stopped` SSE event in TV mode. The event only concerns this
// view while it is still the current playback; a newer playback (or an already
// left view, where nowPlaying was cleared) supersedes it.
export function shouldLeaveOnStopped({ viewKey, nowPlayingKey, reason, eventKey }) {
  if (!eventIsForView(eventKey, viewKey)) return { leave: false, notice: null };
  if (!nowPlayingKey || nowPlayingKey !== viewKey) return { leave: false, notice: null };
  return { leave: true, notice: reason === "tv_disconnected" ? t("player.tvDisconnected") : null };
}

// Pure decision: should this `player` event record the TV view now?
export function tvViewDue({ viewSent, state, position }) {
  return !viewSent && state === "playing" && Number(position) >= VIEW_AFTER_S;
}
