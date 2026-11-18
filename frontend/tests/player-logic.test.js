import { test } from "node:test";
import assert from "node:assert/strict";

import { shouldLeaveOnStopped } from "../js/views/player-logic.js";

test("matching key leaves without a notice for ended and user", () => {
  for (const reason of ["ended", "user"]) {
    assert.deepEqual(shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "a:b", reason }), {
      leave: true,
      notice: null,
    });
  }
});

test("matching key with tv_disconnected leaves with the Swedish notice", () => {
  const r = shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "a:b", reason: "tv_disconnected" });
  assert.equal(r.leave, true);
  assert.equal(r.notice, "TV:n kopplades från");
});

test("a superseded view (different now-playing key) ignores stopped", () => {
  for (const reason of ["ended", "user", "tv_disconnected"]) {
    assert.deepEqual(shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "c:d", reason }), {
      leave: false,
      notice: null,
    });
  }
});

test("no now-playing means this view was already left: ignore", () => {
  assert.equal(shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: null, reason: "ended" }).leave, false);
  assert.equal(shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: undefined, reason: "ended" }).leave, false);
});
