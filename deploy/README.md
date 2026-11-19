# PyroTV deployment (Raspberry Pi 4B, Raspberry Pi OS Lite 64-bit Bookworm)

This folder turns a fresh Raspberry Pi OS Lite card into the PyroTV kiosk:
it boots straight into the app, survives power cuts (read-only root plus a
separate writable `/data` partition), and offers a Wi-Fi hotspot (off by
default) for managing the shop's PyroTV from a phone.

> **Status: written on a PC, NOT yet run on a Pi.** Everything here still has
> to be checked on the real hardware. Go through
> [Verify on the Pi](#verify-on-the-pi) during the hardware spike (Task 0,
> results in `docs/spike-results.md`, not written yet) and the acceptance
> test (Task 16, the checklist in the plan
> `docs/superpowers/plans/2026-11-18-pyrotv.md`).

## Files

| File | Installed to | Purpose |
|---|---|---|
| `install.sh` | run from the repo | One-shot, idempotent installer (`--dry-run`, `--yes`, `--dev`, ...) |
| `pyrotv.service` | `/etc/systemd/system/` | Backend: `uvicorn pyrotv.main:app --factory` on port 80 as user `pyrotv` |
| `pyrotv.env.example` | `/etc/pyrotv.env` | Paths, TV connector, audio sinks, mpv and Chromium flags |
| `sway.conf` | `/home/pyrotv/.config/sway/config` | Kiosk compositor: outputs, touch mapping, window rules |
| `kiosk.sh` | `/opt/pyrotv/deploy/` | Waits for the backend, keeps Chromium running |
| `offline.html` | `/opt/pyrotv/deploy/` | Static page Chromium shows if the backend is not up after ~90 s; retries every 5 s |
| `hotspot-setup.sh` | `/opt/pyrotv/deploy/` | NetworkManager profile `pyrotv-hotspot` + `/data/hotspot.json` |
| `50-pyrotv-nm.rules` | `/etc/polkit-1/rules.d/` | Lets user `pyrotv` switch the hotspot with `nmcli` |
| `99-pyrotv-usb.rules` | `/etc/udev/rules.d/` | Mounts FAT/exFAT USB sticks at `/media/usb-sdXN` for backup export/import |
| `overlay.sh` | `/opt/pyrotv/deploy/` | `status` / `enable` / `disable` the read-only root |
| `update.sh` | run from the repo | `git pull`, reinstall app, restart (refuses while the overlay is on) |

## How it fits together

- **One user for the whole appliance: `pyrotv`** (groups `video`, `audio`,
  `render`, `input`, `netdev`). tty1 logs in automatically as `pyrotv`
  (`getty@tty1` drop-in) and `~/.bash_profile` runs `sway` (only on tty1,
  only if no Wayland session is running). sway starts `squeekboard` (on-screen
  keyboard) and `kiosk.sh` (Chromium).
- **The backend is a system service running as that same user**, so the `mpv`
  it spawns for the TV opens sway's Wayland socket and the user's PipeWire, and
  its `pactl` calls switch that PipeWire's default sink. `/etc/pyrotv.env`
  points it there: `XDG_RUNTIME_DIR=/run/user/<uid>`,
  `WAYLAND_DISPLAY=wayland-1`, `PULSE_SERVER=unix:/run/user/<uid>/pulse/native`.
  `loginctl enable-linger pyrotv` makes `/run/user/<uid>` and PipeWire exist
  from boot. sway names its socket `wayland-1` (it deliberately skips
  `wayland-0`, see sway's `server.c`); check with `ls /run/user/$(id -u pyrotv)`.
- **Only the touchscreen and the hotspot can use the app**: the app answers
  loopback and `10.42.0.0/24` only. uvicorn runs with `--no-proxy-headers`, so
  `X-Forwarded-For` cannot fake the client address. Chromium opens
  `http://127.0.0.1/` (an IP, not `localhost`, because the guard checks IPs).
- **Storage**: `/` is a read-only overlay (writes go to RAM and vanish on
  reboot); `/boot/firmware` is mounted read-only; `/data` is a real ext4
  partition (`noatime,commit=30`) holding the database, media, custom pieces,
  thumbnails and `hotspot.json`. The journal is kept in RAM (32 MB max), swap
  is compressed RAM (zram), because a swap file cannot live on the overlay.

## Which OS image

**Use Raspberry Pi OS Lite 64-bit, Debian 12 "Bookworm". Not the current
default.** The installer refuses any other release (`VERSION_CODENAME` from
`/etc/os-release` must be in `ALLOWED_CODENAMES`, currently `bookworm`).
`--allow-other-os` overrides this; it is untested and prints a loud warning.

Raspberry Pi OS moved to Debian 13 "Trixie" in October 2025. Imager's default
"Raspberry Pi OS Lite (64-bit)" is therefore Trixie. Bookworm is still offered
as a Legacy image. Trixie differs in exactly the things this installer and the
procedure below rely on. Checked against the RPi-Distro sources (pi-gen
`master` vs `bookworm`, raspberrypi-sys-mods `pios/trixie` vs `bookworm`):

- **First-boot resize.** Bookworm's `cmdline.txt` carries
  `init=/usr/lib/raspberrypi-sys-mods/firstboot`, and the initramfs script
  `local-premount/firstboot` grows the root. Trixie's `cmdline.txt` ends in a
  plain `resize` token instead, and the work is done by
  `local-premount/resize_early` plus an `rpi-resize.service`. Step 2 below
  would not stop it.
- **Imager customisation.** Trixie images include cloud-init (pi-gen
  `stage2/04-cloud-init`), not Bookworm's `firstrun.sh`/`custom.toml`.
- **Packages and swap.** Trixie ships its own swap setup (zram by default,
  according to a Raspberry Pi staff comment on the Trixie announcement).
  Chromium packaging and the availability of `squeekboard` were not checked
  for Trixie.
- **Overlay.** `raspi-config`'s overlay code (`trixie` branch) still adds a
  bare `overlayroot=tmpfs` and greps for it the same way. So `overlay.sh` would
  probably work, but this was not tested.

Raspberry Pi recommends a clean flash over upgrading Bookworm to Trixie in
place, and says Bookworm keeps getting updates while it is offered as Legacy.

In **Raspberry Pi Imager**: *Choose OS* → **Raspberry Pi OS (other)** →
**Raspberry Pi OS (Legacy, 64-bit) Lite** (described as Debian Bookworm). The
exact wording comes from third-party guides written after the Trixie release.
I could not check the menu in a current Imager, so look for the entry that
says *Legacy*, *64-bit*, *Lite* and *Bookworm*. After the first boot,
`grep VERSION_CODENAME /etc/os-release` must print `bookworm`.

Sources:
- [Trixie announcement](https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/)
  (2 Oct 2025: clean flash recommended)
- [Last Bookworm release](https://www.raspberrypi.com/news/a-new-raspberry-pi-os-release/)
  (May 2025)
- [raspberrytips: OS versions](https://raspberrytips.com/raspberry-pi-os-versions/)
  (Legacy = Bookworm, updates while it is offered as Legacy)
- [SunFounder install guide](https://docs.sunfounder.com/projects/ai-lab-kit/en/latest/_sources/quick_start/install_the_os.rst.txt)
  (menu path)
- RPi-Distro on GitHub: `pi-gen` (`stage1/00-boot-files/files/cmdline.txt`),
  `raspberrypi-sys-mods` and `raspi-config` (branches `bookworm` /
  `pios/trixie` / `trixie`).

## Install from scratch

1. **Flash** the Bookworm Legacy image with Raspberry Pi Imager: *Choose OS* →
   **Raspberry Pi OS (other)** → **Raspberry Pi OS (Legacy, 64-bit) Lite**.
   Do not use the default "Raspberry Pi OS Lite (64-bit)": that is Trixie, and
   the installer will refuse it (see [Which OS image](#which-os-image)).
   In the Imager's OS customisation set a user name and password, enable SSH,
   and (for the install only) your Wi-Fi or plug in Ethernet. The install
   downloads packages, so it needs internet.
2. **Before the first boot, stop the root partition from filling the card.**
   Re-insert the card in the PC and open `cmdline.txt` on the `bootfs`
   partition (one single line). Delete the token

   ```
   init=/usr/lib/raspberrypi-sys-mods/firstboot
   ```

   and nothing else; keep it one line; save. Why: on Bookworm this token makes
   the initramfs grow the root partition to the whole card on the first boot
   (`raspberrypi-sys-mods`: `initramfs-tools/scripts/local-premount/firstboot`
   runs only when `firstboot` is on the kernel command line), which would leave
   no room for `/data`. `install.sh` later grows the root to a fixed 12 GiB and
   puts `/data` in the rest.

   Side effects of skipping `firstboot` (checked against its source, UNVERIFIED
   on a real first boot): SSH host keys are still generated by the separate
   `regenerate_ssh_host_keys.service`; the disk identifier is not randomised
   (harmless for a single device); and a `custom.toml` on the boot partition is
   NOT applied. Imager's Bookworm customisation normally writes `firstrun.sh`
   plus a `systemd.run=/boot/firstrun.sh ...` token, which still works (do
   not remove those). **If you find a `custom.toml` instead of `firstrun.sh`**,
   create the user without it: put an empty file named `ssh` on `bootfs` and a
   `userconf.txt` containing `admin:<hash>` (hash from
   `openssl passwd -6`), as described in the Raspberry Pi documentation.
3. **Boot** the Pi with the card, wait a minute, `ssh <user>@<pi address>`.
4. **Get the code onto the Pi**: `git clone <repo url> ~/PyroTV` (or copy the
   folder with `scp -r`). The `VIDEOS/` folder is not needed on the Pi.
5. **Look at the plan first** (changes nothing):

   ```
   cd ~/PyroTV
   sudo ./deploy/install.sh --dry-run
   ```

   Run as root, the dry run also reads the real partition table and prints the
   exact `sfdisk` commands it would run.
6. **Install**:

   ```
   sudo ./deploy/install.sh            # asks you to type YES before partitioning
   sudo ./deploy/install.sh --yes      # no question (non-interactive)
   sudo ./deploy/install.sh --dev      # keep the SD card writable (development)
   ```

   Other options: `--disk /dev/mmcblk0`, `--root-size 12GiB`, `--ssid NAME`,
   `--country SE`, `--hostname pyrotv`. Re-running is safe.
7. **Reboot**: `sudo reboot`. The touchscreen should show the setup wizard.
   Then check the storage: `sudo /opt/pyrotv/deploy/overlay.sh status` must
   show `/data` as `ext4` on `/dev/mmcblk0p3`, not `overlay`.
8. **Audio sinks** (until the spike has recorded them): see
   [Audio sinks](#audio-sinks).

### What the installer does, in order

1. Preflight: Linux, root, aarch64. Stops unless `VERSION_CODENAME` is
   `bookworm` (override: `--allow-other-os`). Also stops while the overlay is on.
2. `apt-get install`: sway, squeekboard, Chromium (`chromium-browser` if the
   Pi repository offers it, else `chromium`), mpv, ffmpeg, PipeWire
   (`pipewire`, `pipewire-pulse`, `wireplumber`), `pulseaudio-utils`
   (`pactl`), NetworkManager + `dnsmasq-base`, `polkitd`, `avahi-daemon`,
   `python3-venv`, `rsync`, `parted`, `e2fsprogs`, dconf, fonts, `zram-tools`,
   `overlayroot` (not with `--dev`).
3. User `pyrotv`, its groups, `loginctl enable-linger`.
4. The data partition (see below), `/etc/fstab`, mount, data directories.
5. The app: `backend/`, `frontend/`, `catalogue/`, `deploy/` to `/opt/pyrotv`,
   a venv at `/opt/pyrotv/venv`, `pip install /opt/pyrotv/backend` (runtime
   dependencies only, no `dev` extra).
6. `/etc/pyrotv.env` (from `pyrotv.env.example`, kept if it already exists).
7. `pyrotv.service`, the polkit rule, the USB mount rule, journald and zram
   settings.
8. The kiosk session (autologin, `.bash_profile`, sway config, squeekboard
   defaults), host name `pyrotv` (so `http://pyrotv.local` works over mDNS).
9. The hotspot profile (`hotspot-setup.sh`).
10. Starts the backend.
11. **Last**: the read-only root and boot partition (`overlay.sh enable`), so
    everything above is already on the card. Skipped with `--dev`.

### The data partition

- Only ever on the card that holds the running root: `/` must be partition 2
  of `--disk` (default `/dev/mmcblk0`), otherwise the installer stops. The
  table must be MBR with exactly p1 (FAT boot), p2 (root) and optionally p3.
- **p3 exists, ext4, label `data`**: reused untouched. It is never reformatted.
- **p3 exists, anything else**: the installer stops.
- **Only p1 and p2, free space after p2**: after showing the plan and getting
  `YES` (or `--yes`) it saves the partition table to
  `/root/pyrotv-ptable-<date>.sfdisk`, grows p2 to `--root-size` (12 GiB) with
  `sfdisk --no-reread -N 2`, adds p3 over the rest of the card with
  `sfdisk --no-reread --append`, tells the kernel (`partx -u`), grows the root
  filesystem online (`resize2fs`) and formats p3 (`mkfs.ext4 -L data`) only if
  `blkid -p` finds nothing on it. (`sfdisk` is used instead of
  `parted resizepart` because `parted` refuses to resize a mounted partition in
  script mode.)
- **Root already fills the card**: the installer stops and explains step 2
  above (re-flash, edit `cmdline.txt`).
- `/etc/fstab` gets `UUID=<p3 uuid> /data ext4 defaults,noatime,commit=30,nofail 0 2`
  (systemd turns it into `data.mount`, which `pyrotv.service` requires). UUID
  rather than `LABEL=data` so a USB stick that happens to be called "data" can
  never become `/data`; `nofail` so a broken data partition still lets the Pi
  boot far enough for SSH instead of hanging in emergency mode.
- If the kernel cannot see the new partition until a reboot, the installer
  says so; reboot and run it again (it then formats the blank p3).

## Hardware wiring

- **Touchscreen**: HDMI connector `HDMI-A-1` + its USB cable (touch).
- **TV** (optional): `HDMI-A-2`.

On the Pi 4 `HDMI-A-1` is normally the micro-HDMI port **next to the USB-C
power socket** ("HDMI0") and `HDMI-A-2` the one next to the audio jack, but
**verify it**: plug only the touchscreen in and run `ls /sys/class/drm`
and `cat /sys/class/drm/card*-HDMI-A-*/status`; the touchscreen's connector
must say `connected`. (The `card` number can change between kernels, the
connector names should not.) If they are the wrong way round, swap the cables.
`PYROTV_TV_CONNECTOR` in `/etc/pyrotv.env` and the names in `sway.conf` must
match.

## Media: videos and images

The built-in catalogue (`/opt/pyrotv/catalogue/catalogue.json`) lists pieces by
name. Their media live on the data partition, flat, named exactly like the
piece (same spelling, case, spaces and Swedish letters):

```
/data/media/videos/<exact piece name>.mp4
/data/media/images/<exact piece name>.jpg   (or .png)
```

**Commit the real catalogue to the repo** as `catalogue/catalogue.json`.
`install.sh` and `update.sh` copy `catalogue/` to `/opt/pyrotv/catalogue` with
`rsync --delete`, so a catalogue edited only on the Pi is overwritten by the
next install or update.

Copy them while the root is writable or not, it does not matter: `/data` is
always writable. 40 GB takes a while; either

- **USB stick** (exFAT is easiest: FAT32 cannot hold files over 4 GB),
  plugged into the Pi:
  `sudo rsync -rt --info=progress2 /media/usb-sda1/videos/ /data/media/videos/`
  (FAT/exFAT sticks are mounted automatically at `/media/usb-sdXN`; mount
  other filesystems by hand), or
- **over the network** from the PC (before the shop install, while the Pi still
  has network): `scp -r videos/* <user>@<pi>:/data/media/videos/` (copy to a
  folder you can write first, or `sudo chown -R <user> /data/media` for the
  copy and `sudo chown -R pyrotv:pyrotv /data/media` after).

Restart the app afterwards (`sudo systemctl restart pyrotv`) so it rescans.
Installing with `--dev` while filling the card is convenient (no overlay
toggling), then re-run without `--dev` for the shop.

> **Open follow-up: the owner's video library does not match yet.** The
> owner's own library (folder `VIDEOS/` at the repo root, git-ignored) is
> organised as `<MANUFACTURER>/<CATEGORY>/<files like F_AETHRA-Z.mp4>`, not in
> the flat, name-equals-catalogue-name layout above, and no real catalogue file
> for it exists yet (`catalogue/catalogue.json` is a sample). Reconciling them
> (a `catalogue.json` with the exact names, and either flattening/renaming the
> files or teaching the catalogue loader the folder layout) is still to do.
> Nothing in `deploy/` touches `VIDEOS/`.

## Audio sinks

`PYROTV_SINK_HDMI` (TV) and `PYROTV_SINK_JACK` (shop speakers) are empty until
the spike fills them in; while empty, the app never switches audio and the
installer prints a warning. To find them, after a reboot with the session
running:

```
sudo -u pyrotv XDG_RUNTIME_DIR=/run/user/$(id -u pyrotv) pactl list short sinks
```

Put the names in `/etc/pyrotv.env` and `sudo systemctl restart pyrotv`
(with the overlay on, disable it first, see below).

## Logs, restart, recovery

- Backend logs: `journalctl -u pyrotv -f`; sway/Chromium/kiosk:
  `journalctl -t sway`; the journal is in RAM and starts empty on every boot.
- Restart the backend: `sudo systemctl restart pyrotv`. Restart the kiosk
  browser: `sudo pkill -u pyrotv -f -- --user-data-dir=/tmp/chromium-kiosk`
  (kiosk.sh starts it again). Restart the whole session:
  `sudo systemctl restart getty@tty1`.
- Storage state: `sudo /opt/pyrotv/deploy/overlay.sh status`.
- SSH still works in the shop if you plug in Ethernet (the app itself refuses
  clients that are not on the hotspot).
- If the Pi does not come up at all: put the card in a PC and remove
  `overlayroot=tmpfs:recurse=0` from `cmdline.txt` on `bootfs` to boot with a
  writable root.
- If the backend does not answer within about 90 s after boot, the kiosk shows
  a static page ("PyroTV-tjänsten startar inte", `deploy/offline.html`) that
  retries every 5 s and opens the app by itself once it is up. Usual cause: the
  data partition did not mount (`journalctl -u pyrotv`, `findmnt /data`).

## Before the shop install: security and behaviour notes

- **SSH is reachable from hotspot clients** (the hotspot is a normal network
  for the Pi: sshd listens on every interface; only the web app filters
  clients). Use a strong password or key-only login
  (`PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`), or disable SSH
  for the shop (`sudo systemctl disable --now ssh`, with the overlay off).
- **Remove the Imager's Wi-Fi profile** once the install is done, so the Pi
  does not join the workshop network in the shop:
  `nmcli con show` to find its name, then `sudo nmcli con delete <name>`
  (overlay off, then reboot).
- **The Settings gear is open to anyone at the touchscreen**, by design (no
  PIN); the shop staff are trusted to keep customers out of it.
- **A TV in standby may still report "connected" over HDMI**, so playback goes
  to a dark TV. Switch the TV off at the mains (or unplug it) when it is not
  meant to show videos; the touchscreen then plays locally.

## Updating (overlay on)

While the overlay is active, every change to `/` (including `/etc/pyrotv.env`
and `/opt/pyrotv`) is lost at the next reboot. `/data` is not affected. To
update:

```
sudo /opt/pyrotv/deploy/overlay.sh disable && sudo reboot
# ...after the reboot, root is writable:
cd ~/PyroTV && sudo ./deploy/update.sh        # git pull, reinstall, restart
#   (add --allow-other-os if the install needed it)
#   (or edit /etc/pyrotv.env, apt upgrade, ...)
sudo ./deploy/overlay.sh enable && sudo reboot
```

Always use `overlay.sh`, not `raspi-config`'s menu, to switch it: the
installer sets `overlayroot=tmpfs:recurse=0` (see next paragraph), which
`raspi-config` cannot switch off. `/boot/firmware` stays read-only after
`disable`; for `apt upgrade` (kernel updates write there) run
`sudo overlay.sh disable` once more after the first reboot, which also makes
`/boot/firmware` writable; `overlay.sh enable` makes it read-only again.

**Why `recurse=0`**: `raspi-config nonint do_overlayfs 0` adds
`overlayroot=tmpfs` to `cmdline.txt`. overlayroot's default (`recurse=1`)
puts an overlay on top of *every* filesystem in `/etc/fstab`, which would make
`/data` silently non-persistent. `recurse=0` overlays only `/`
(overlayroot.conf: changes to other filesystems are then permanent).

## Simulating the 1 GB Pi on a bigger board

On a Pi 4 with more RAM, add to `/boot/firmware/config.txt` (under `[all]`)

```
total_mem=1024
```

and reboot; `free -m` should then show about 900 MB. Alternative: append
` mem=1G` to the single line in `/boot/firmware/cmdline.txt`. (With the
overlay on, `/boot/firmware` is read-only: disable it first.) Soak-test on a
real 1 GB board before the shop install if possible.

## Verify on the Pi

Nothing below has run on hardware. Tick each item during Task 0 / Task 16 and
fix the files here where reality differs.

- [ ] **OS release**: `cat /etc/os-release` shows `VERSION_CODENAME=bookworm`
      (the Legacy image was flashed, not the default Trixie); note the Imager
      version and the exact menu wording used.
- [ ] **Card prep**: removing `init=/usr/lib/raspberrypi-sys-mods/firstboot`
      keeps the root small; SSH host keys exist (`ls /etc/ssh/ssh_host_*`);
      the Imager user and SSH work. Note what the Imager wrote (`firstrun.sh`
      or `custom.toml`).
- [ ] **Partitioning**: `sfdisk --no-reread -N 2` + `--append` on the mounted
      card, `partx -u`, online `resize2fs`; `lsblk` shows p2 = 12 GiB and p3 =
      the rest; re-running the installer reuses p3 untouched.
- [ ] **Overlay**: after `overlay.sh enable` + reboot, `findmnt /` is
      `overlay`, `findmnt /data` is `ext4` on `mmcblk0p3` (rw),
      `/boot/firmware` is `ro`. A file written to `/data` survives a reboot; a
      file written to `/home/pyrotv` does not.
- [ ] **Power pull**: pull the plug during playback, during an upload and
      during a settings change (several times); the Pi boots back into the app
      and the database is intact.
- [ ] **Memory under the 1 GB cap** (`total_mem=1024` or a real 1 GB board):
      Chromium + squeekboard + sway + backend + mpv on the TV, with
      `free -m` / `top` over a long run (Task 16 soak: `MemAvailable` stays
      >= 150 MB for 8 hours); no OOM kills
      (`journalctl -k | grep -i oom`). Tune the kiosk flags
      (`--renderer-process-limit`, cache size) and zram (`/etc/default/zramswap`).
- [ ] **sway socket name** is `wayland-1` (`ls /run/user/$(id -u pyrotv)`)
      and the backend's mpv appears on the TV.
- [ ] **mpv**: plays fullscreen on `HDMI-A-2`
      (`--fs-screen-name` + the sway `for_window` rule); pick `--hwdec`
      (`v4l2m2m-copy` / `drm`) for smooth 1080p; record `PYROTV_MPV_ARGS`.
- [ ] **Chromium**: the Wayland `app_id` (`swaymsg -t get_tree | grep app_id`)
      matches `^chromium`; 1080p30 `<video>` is smooth on the touchscreen;
      which package/binary is installed (`chromium-browser` or `chromium`).
- [ ] **On-screen keyboard (high risk)**: tapping the search box, the shop
      name field and the custom piece name field makes squeekboard appear, it
      types into Chromium, and the layout is Swedish (å ä ö). This depends on
      Chromium's `--enable-wayland-ime --wayland-text-input-version=3`
      (text-input-v3, the only version sway offers) and on squeekboard's
      settings (`/etc/dconf/db/local.d/00-pyrotv`: a11y
      `screen-keyboard-enabled`, input source `('xkb','se')`; inspect with
      `sudo -u pyrotv dbus-run-session gsettings get org.gnome.desktop.input-sources sources`).
      **Fallback if this cannot be made to work: an in-app HTML keyboard in
      the frontend would be needed (not built).**
- [ ] **Touch**: touches land on the touchscreen, also with the TV connected
      (`input type:touch map_to_output HDMI-A-1`); the cursor stays hidden.
- [ ] **HDMI names / hot-plug**: which physical port is `HDMI-A-1`; plugging
      and unplugging the TV while running switches the app's TV state, moves
      audio, and sway re-places outputs sanely.
- [ ] **Audio sinks**: names from `pactl list short sinks` work with
      `pactl set-default-sink` as user `pyrotv` from the service; record them.
- [ ] **Hotspot**: the profile exists (`nmcli con show pyrotv-hotspot`);
      switching it on/off from Settings works as user `pyrotv` through the
      polkit rule (no "Not authorized" in `journalctl -u pyrotv`); a phone
      joins with the shown PIN/QR and gets a `10.42.0.x` address; the admin
      page loads at `http://10.42.0.1` and `http://pyrotv.local`; brcmfmac AP
      mode is stable with WPA2/CCMP on channel 6. Trim the polkit action list
      to what is really needed.
- [ ] **USB backup**: a FAT/exFAT stick mounts at `/media/usb-sda1` owned by
      `pyrotv`; export and import work; pulling the stick unmounts it.
- [ ] **Service hardening** does not break anything (`ProtectSystem=full`,
      `PrivateTmp`, `NoNewPrivileges`): uploads, thumbnails (ffmpeg), mpv,
      pactl, nmcli all work from the service.
- [ ] **Boot to app time** and that the kiosk waits for the backend instead of
      showing an error page.
- [ ] **zram** active (`swapon --show`), `dphys-swapfile` disabled.
