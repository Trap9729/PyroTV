import { test } from "node:test";
import assert from "node:assert/strict";

import { eventIsForView, shouldLeaveOnStopped, tvViewDue } from "../js/views/player-logic.js";

test("stopped for another playback's key is ignored; a missing key is accepted", () => {
  assert.deepEqual(
    shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "a:b", reason: "ended", eventKey: "c:d" }),
    { leave: false, notice: null },
  );
  assert.equal(
    shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "a:b", reason: "ended", eventKey: "a:b" }).leave,
    true,
  );
  assert.equal(
    shouldLeaveOnStopped({ viewKey: "a:b", nowPlayingKey: "a:b", reason: "ended", eventKey: null }).leave,
    true,
  );
});

test("eventIsForView", () => {
  assert.equal(eventIsForView("a:b", "a:b"), true);
  assert.equal(eventIsForView(undefined, "a:b"), true);
  assert.equal(eventIsForView("c:d", "a:b"), false);
});

test("tvViewDue: once, while playing, from 5 s of position", () => {
  assert.equal(tvViewDue({ viewSent: false, state: "playing", position: 4.9 }), false);
  assert.equal(tvViewDue({ viewSent: false, state: "playing", position: 5 }), true);
  assert.equal(tvViewDue({ viewSent: false, state: "playing", position: "6.5" }), true);
  assert.equal(tvViewDue({ viewSent: false, state: "paused", position: 30 }), false);
  assert.equal(tvViewDue({ viewSent: true, state: "playing", position: 30 }), false);
  assert.equal(tvViewDue({ viewSent: false, state: "playing", position: undefined }), false);
});

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
