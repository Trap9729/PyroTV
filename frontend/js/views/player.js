// STUB: replaced by the real player in Task 11.
import { playerStop } from "../api.js";
import { renderStub } from "./stub.js";

export function renderPlayer(root, ctx, key) {
  const np = ctx.nowPlaying && ctx.nowPlaying.key === key ? ctx.nowPlaying : null;
  const title = np ? np.title : key.slice(key.indexOf(":") + 1);
  renderStub(root, title, () => {
    playerStop().catch(() => {});
    ctx.navigate({ name: "grid" });
  });
}
