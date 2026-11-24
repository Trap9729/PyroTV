#!/usr/bin/env python3
"""PyroTV: calibrate the touchscreen when taps land in the wrong place.

    deploy/touch-calibrate.py            # tap the 4 corners, then it applies + saves
    deploy/touch-calibrate.py --reset    # forget the calibration

Some touch controllers report positions over an area larger than the panel, so
the visible screen only covers a rectangle of the touch area (for example the top
right corner). This tool asks you to tap the four corners of the VISIBLE screen,
works out the libinput calibration matrix and

  - applies it to the running sway session right away, and
  - saves it to /data/touch-calibration.conf, which sway.conf includes at every
    start (/data stays writable with the read-only root enabled).

Run it as the kiosk user (`pyrotv`, group `input`), from an SSH session or the
Terminal, with the kiosk running. Python 3 standard library only.
"""
import argparse
import fcntl
import glob
import json
import os
import select
import struct
import subprocess
import sys

EV_KEY, EV_ABS = 1, 3
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_X, ABS_MT_Y, ABS_MT_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14A
EVENT = struct.Struct("llHHi")
DEFAULT_OUT = "/data/touch-calibration.conf"
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | nr


def has_abs(fd, code):
    buf = bytearray(8)
    fcntl.ioctl(fd, _ioc(2, 0x20 + EV_ABS, len(buf)), buf)
    return bool(buf[code // 8] >> (code % 8) & 1)


def abs_range(fd, code):
    buf = bytearray(24)  # struct input_absinfo: value, min, max, fuzz, flat, resolution
    fcntl.ioctl(fd, _ioc(2, 0x40 + code, len(buf)), buf)
    _value, lo, hi, _fuzz, _flat, _res = struct.unpack("6i", buf)
    return lo, hi


def device_name(fd):
    buf = bytearray(256)
    fcntl.ioctl(fd, _ioc(2, 0x06, len(buf)), buf)
    return bytes(buf).split(b"\0", 1)[0].decode("utf-8", "replace")


def find_touch_device(path=None):
    """Return (path, name, x_code, y_code). Prefers a multitouch device."""
    candidates = [path] if path else sorted(glob.glob("/dev/input/event*"))
    fallback = None
    for p in candidates:
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            name = device_name(fd)
            if has_abs(fd, ABS_MT_X) and has_abs(fd, ABS_MT_Y):
                return p, name, ABS_MT_X, ABS_MT_Y
            # Single-touch fallback, but never the "... Mouse" twin some controllers add.
            if fallback is None and has_abs(fd, ABS_X) and has_abs(fd, ABS_Y) and "mouse" not in name.lower():
                fallback = (p, name, ABS_X, ABS_Y)
        except OSError:
            pass
        finally:
            os.close(fd)
    return fallback


def drain(fd):
    while select.select([fd], [], [], 0)[0]:
        if not os.read(fd, EVENT.size * 64):
            break


def wait_for_tap(fd, xc, yc):
    """Block until one finger touches and lifts; return its last raw (x, y)."""
    drain(fd)
    x = y = None
    slot = 0
    while True:
        released = False
        select.select([fd], [], [])  # the device is non-blocking: wait until it has events
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            continue
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _u, etype, code, value = EVENT.unpack_from(data, off)
            if etype == EV_ABS:
                if code == ABS_MT_SLOT:
                    slot = value
                elif slot == 0 and code == xc:
                    x = value
                elif slot == 0 and code == yc:
                    y = value
                elif slot == 0 and code == ABS_MT_ID and value == -1:
                    released = True
            elif etype == EV_KEY and code == BTN_TOUCH and value == 0:
                released = True
        if released and x is not None and y is not None:
            return x, y


def sway_socket():
    sock = os.environ.get("SWAYSOCK")
    if sock:
        return sock
    found = sorted(glob.glob(f"/run/user/{os.getuid()}/sway-ipc.*.sock"))
    return found[0] if found else None


def swaymsg(sock, *args):
    return subprocess.run(["swaymsg", "-s", sock, *args], capture_output=True, text=True)


def matching_inputs(sock, dev_name):
    """sway identifiers of the touch device and its twins (e.g. "... Mouse")."""
    res = swaymsg(sock, "-t", "get_inputs")
    if res.returncode != 0:
        sys.exit(f"swaymsg get_inputs failed: {res.stderr.strip()}")
    return [i["identifier"] for i in json.loads(res.stdout)
            if i.get("type") in ("touch", "pointer") and i.get("name", "").startswith(dev_name)]


def fmt(matrix):
    return " ".join(f"{v:.6f}" for v in matrix)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", help="evdev path (default: auto-detect)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"file to save (default {DEFAULT_OUT})")
    ap.add_argument("--reset", action="store_true", help="remove the calibration")
    args = ap.parse_args()

    found = find_touch_device(args.device)
    if not found:
        sys.exit("no touch device found (is the user in the 'input' group?)")
    path, name, xc, yc = found
    sock = sway_socket()
    if not sock:
        sys.exit("no running sway session found (is the kiosk up?)")
    idents = matching_inputs(sock, name)
    if not idents:
        sys.exit(f"sway does not list an input named like {name!r}")
    print(f"touch device: {name} ({path}); sway inputs: {', '.join(idents)}")

    if args.reset:
        matrix, lines = IDENTITY, []
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        x_lo, x_hi = abs_range(fd, xc)
        y_lo, y_hi = abs_range(fd, yc)
        print(f"touch range: x {x_lo}..{x_hi}, y {y_lo}..{y_hi}\n")
        print("Tap the very CORNERS of the visible picture, as precisely as you can.")
        print("(The page behind may react to the taps; that is fine.)\n")
        pts = {}
        for label in ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"):
            input(f"Press Enter, then tap the {label} corner of the screen... ")
            rx, ry = wait_for_tap(fd, xc, yc)
            pts[label] = ((rx - x_lo) / (x_hi - x_lo), (ry - y_lo) / (y_hi - y_lo))
            print(f"  got x={pts[label][0]:.4f} y={pts[label][1]:.4f}  (raw {rx}, {ry})")
        os.close(fd)
        x_left = (pts["TOP-LEFT"][0] + pts["BOTTOM-LEFT"][0]) / 2
        x_right = (pts["TOP-RIGHT"][0] + pts["BOTTOM-RIGHT"][0]) / 2
        y_top = (pts["TOP-LEFT"][1] + pts["TOP-RIGHT"][1]) / 2
        y_bottom = (pts["BOTTOM-LEFT"][1] + pts["BOTTOM-RIGHT"][1]) / 2
        if x_right - x_left < 0.05 or y_bottom - y_top < 0.05:
            sys.exit("the corners are too close together (or in the wrong order); nothing changed")
        sx, sy = 1 / (x_right - x_left), 1 / (y_bottom - y_top)
        matrix = (sx, 0.0, -x_left * sx, 0.0, sy, -y_top * sy)
        print(f"\nvisible screen covers x {x_left:.3f}..{x_right:.3f}, y {y_top:.3f}..{y_bottom:.3f} of the touch area")
        lines = [f'input "{i}" calibration_matrix {fmt(matrix)}' for i in idents]

    for ident in idents:
        res = swaymsg(sock, f'input "{ident}" calibration_matrix {fmt(matrix)}')
        if res.returncode != 0:
            print(f"warning: swaymsg failed for {ident}: {res.stderr.strip() or res.stdout.strip()}")
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# Written by deploy/touch-calibrate.py; included by sway.conf. Re-run it to change.\n")
            f.write("".join(line + "\n" for line in lines))
        print(f"{'removed calibration from' if args.reset else 'saved to'} {args.out}")
    except OSError as e:
        print(f"could not save to {args.out}: {e}\nThe calibration is active now but will be lost at the next start.")
    print("Applied. Tap around to check; run again if it is still off.")


if __name__ == "__main__":
    main()
