// Double-tap / tap-burst recogniser (pure, no DOM; timers are injectable for tests).
//
// A single tap with no follow-up inside `windowMs` calls onToggle. A second tap
// inside the window seeks +/-10 s for the side of THAT tap, and every further tap
// inside the window of the previous one keeps seeking (YouTube style). Once no tap
// arrives for `windowMs` the sequence resets.

export const SEEK_STEP = 10;

export function createDoubleTap({
  width,
  windowMs = 300,
  onSeek,
  onToggle,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let timer = null; // pending while a sequence is open

  const getWidth = () => (typeof width === "function" ? width() : width);

  function arm(fn) {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(() => {
      timer = null;
      fn();
    }, windowMs);
  }

  function tap({ x }) {
    if (timer === null) {
      // First tap of a new sequence: wait to see whether a second one follows.
      arm(() => onToggle());
      return;
    }
    // Inside a window: seek, and keep the burst alive (its timer expiry is a no-op).
    arm(() => {});
    onSeek(x < getWidth() / 2 ? -SEEK_STEP : SEEK_STEP);
  }

  // Drop any pending timer and reset (view teardown).
  tap.cancel = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };

  return tap;
}
