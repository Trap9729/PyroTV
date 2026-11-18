import { test } from "node:test";
import assert from "node:assert/strict";

import { createDoubleTap } from "../js/gestures.js";

// Fake clock: timers fire in order when advance() moves time forward.
function makeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimer(fn, ms) {
      const id = nextId++;
      timers.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
    advance(ms) {
      const target = now + ms;
      for (;;) {
        let best = null;
        for (const [id, tm] of timers) {
          if (tm.at <= target && (!best || tm.at < best[1].at)) best = [id, tm];
        }
        if (!best) break;
        timers.delete(best[0]);
        now = best[1].at;
        best[1].fn();
      }
      now = target;
    },
    get now() {
      return now;
    },
  };
}

function setup(opts = {}) {
  const clock = makeClock();
  const seeks = [];
  let toggles = 0;
  const tap = createDoubleTap({
    width: 1280,
    onSeek: (d) => seeks.push(d),
    onToggle: () => {
      toggles++;
    },
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    ...opts,
  });
  return { clock, seeks, tap, toggles: () => toggles };
}

test("two taps 200 ms apart on the right seek +10 once and never toggle", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 900 });
  clock.advance(200);
  tap({ x: 900 });
  clock.advance(1000);
  assert.deepEqual(seeks, [10]);
  assert.equal(toggles(), 0);
});

test("two taps on the left seek -10", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 100 });
  clock.advance(200);
  tap({ x: 100 });
  clock.advance(1000);
  assert.deepEqual(seeks, [-10]);
  assert.equal(toggles(), 0);
});

test("three quick taps on the right seek twice", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 900 });
  clock.advance(150);
  tap({ x: 900 });
  clock.advance(150);
  tap({ x: 900 });
  clock.advance(1000);
  assert.deepEqual(seeks, [10, 10]);
  assert.equal(toggles(), 0);
});

test("a single tap toggles once after the window and never seeks", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 900 });
  clock.advance(299);
  assert.equal(toggles(), 0);
  clock.advance(1);
  assert.equal(toggles(), 1);
  clock.advance(2000);
  assert.equal(toggles(), 1);
  assert.deepEqual(seeks, []);
});

test("left tap then right tap inside the window seeks on the second tap's side", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 100 });
  clock.advance(120);
  tap({ x: 900 });
  clock.advance(1000);
  assert.deepEqual(seeks, [10]);
  assert.equal(toggles(), 0);
});

test("a tap after the window expired starts a new sequence (toggle, not seek)", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 900 });
  clock.advance(400);
  assert.equal(toggles(), 1);
  tap({ x: 900 });
  clock.advance(400);
  assert.equal(toggles(), 2);
  assert.deepEqual(seeks, []);
});

test("after a seek burst ends the next single tap toggles again", () => {
  const { clock, seeks, tap, toggles } = setup();
  tap({ x: 900 });
  clock.advance(100);
  tap({ x: 900 });
  clock.advance(500);
  assert.deepEqual(seeks, [10]);
  tap({ x: 900 });
  clock.advance(400);
  assert.deepEqual(seeks, [10]);
  assert.equal(toggles(), 1);
});

test("width can be a getter and is re-read per tap", () => {
  let w = 1000;
  const { clock, seeks, tap } = setup({ width: () => w });
  tap({ x: 600 });
  clock.advance(100);
  tap({ x: 600 });
  assert.deepEqual(seeks, [10]);
  clock.advance(1000);
  w = 1400;
  tap({ x: 600 });
  clock.advance(100);
  tap({ x: 600 });
  assert.deepEqual(seeks, [10, -10]);
});

test("a custom windowMs is honoured", () => {
  const { clock, seeks, tap, toggles } = setup({ windowMs: 500 });
  tap({ x: 900 });
  clock.advance(450);
  tap({ x: 900 });
  assert.deepEqual(seeks, [10]);
  clock.advance(1000);
  assert.equal(toggles(), 0);
});
