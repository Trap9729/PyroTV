import { t } from "../i18n.js";

// Pure decision for a `stopped` SSE event in TV mode. The event only concerns this
// view while it is still the current playback; a newer playback (or an already
// left view, where nowPlaying was cleared) supersedes it.
export function shouldLeaveOnStopped({ viewKey, nowPlayingKey, reason }) {
  if (!nowPlayingKey || nowPlayingKey !== viewKey) return { leave: false, notice: null };
  return { leave: true, notice: reason === "tv_disconnected" ? t("player.tvDisconnected") : null };
}
