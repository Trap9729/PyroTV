# PyroTV – Design

## Purpose
A kiosk appliance for a fireworks shop. Staff or customers tap a firework on a touchscreen and a demo video plays, either on the touchscreen or on a connected TV. It must boot straight into the app, look good, and survive power being pulled at any time without corruption.

## Goals
- Boots to the app with no login or desktop. Power loss never needs manual repair.
- Fast, good-looking, touch-first UI in **Swedish only** (strings kept in one file so other languages can be added later).
- Smooth 1080p playback with hardware decoding.
- Everything configurable from the touchscreen **and** from a phone over the Pi's own Wi-Fi.

## Non-goals
- No internet features, accounts, cloud sync or remote updates (for now).
- No multi-language support yet.
- No video downloading or fetching. Videos are placed on the SD card by the owner.

## Anti-goals (failure even if it "works")
- A corrupted SD card after a power cut.
- Laggy or janky touch scrolling, or video stutter.
- Someone changing settings without the hotspot PIN.

## Hardware and constraints
- Raspberry Pi 4B 1GB, Raspberry Pi OS Lite 64-bit (Bookworm or newer).
- 10.1" 16:10 touchscreen: HDMI-1 plus USB touch. TV is optional on HDMI-2.
- 64GB SD card; up to about 40GB of videos. Storage use is shown in Settings.
- Audio: Pi 3.5mm jack normally, TV HDMI audio when the TV is connected.
- Hotspot only, no internet.

## Technology choices (my calls)
| Area | Choice | Why |
|---|---|---|
| UI | Web UI (HTML/CSS/JS, no build step), shown fullscreen in Chromium kiosk under the `sway` Wayland compositor | One UI codebase serves both the touchscreen and the phone admin page. `sway` can pin each window to an output (Chromium on the touchscreen, mpv on the TV); a single-app kiosk compositor like `cage` cannot. |
| Backend | Python + FastAPI, run as a systemd service | Simple, reliable, good ffmpeg and process control. |
| Player | **TV:** mpv with hardware decode, controlled over its IPC socket. **Touchscreen:** the browser's own `<video>` element | mpv is a separate process and can target the TV's HDMI output. In the browser, on-video controls (exit button, double-tap) are plain HTML. The spike must confirm Chromium plays 1080p30 smoothly. |
| Data | SQLite (WAL, `synchronous=FULL`) | Safe on power loss. |
| Media prep | ffmpeg, for custom uploads | Normalises uploads to H.264 ≤1080p and makes thumbnails. |
| Hotspot | NetworkManager AP mode | Built in to Pi OS. |

**Known risk, to be checked first:** Chromium kiosk on 1GB RAM. The plan starts with a small spike on the real Pi. If it is too heavy or janky, the fallback is a Qt/QML touchscreen app with the phone page staying web-based.

## Power-loss safety
- Root filesystem set read-only with the Pi OS overlay filesystem, so the OS cannot be corrupted.
- A separate writable data partition holds the database, custom uploads and settings. It uses ext4 with a journal.
- Uploads are written to a temp file and atomically renamed only when complete. Interrupted uploads never show up as a piece.
- View counts are batched in memory and flushed every few minutes, to reduce SD wear.

## Catalogue (built in, owner supplies it later)
I define the format and the owner fills it in.

- `catalogue.json` with manufacturers, each containing categories and pieces. Each piece has an exact `name`, `category` and optional extra fields.
- Videos are stored as `videos/<name>.mp4`, where the filename equals the exact name in the catalogue.
- Images: `images/<name>.jpg`, named the same way as the videos.
- Pieces missing a video file are hidden automatically and listed in a Settings diagnostics view, which is useful for catching typos.

## Development setup: simulating the 1GB Pi on a 4GB board
Add `total_mem=1024` to `/boot/firmware/config.txt` and reboot; `free -m` should then report about 900MB. Optional backup: append `mem=1G` to `/boot/firmware/cmdline.txt`. This matches the 1GB Pi 4 closely (same SoC, CPU speed and memory bandwidth). Before the shop install, soak-test on a real 1GB board if one can be borrowed.

## Screens and behaviour

### First-run setup wizard (also re-runnable from Settings)
1. Shop name.
2. Select manufacturers (multi-select).
3. For each selected manufacturer, a checklist to disable individual pieces.
4. Add custom pieces. You can skip this; it shows the hotspot name, password and QR code for the phone upload page, and lists any custom pieces already added.
5. Finish, then go to the grid.

### Main grid
- Tiles show an image and the name. About 4×3 per screen, smooth touch scroll.
- Top bar: shop name, search, a sort/category selector and a Settings gear in one corner.
- Sort and filter: **Mest sedda** (most viewed, counted locally per piece) and the catalogue's categories (e.g. tårtor, singelskott). Custom pieces choose a category on upload.

### Playback
- **No TV connected:** fullscreen on the touchscreen. Exit button top right. Double-tap the right half to skip +10s and the left half to skip −10s, with a visual ripple. Single tap shows or hides a seek bar and play/pause.
- **TV connected (HDMI-2):** video plays on the TV with sound on HDMI. The touchscreen shows a small popup with the name, −10s, +10s, play/pause and exit.
- TV plug and unplug is detected automatically. Audio follows the active output.
- Playing a video counts as a view once it has played a few seconds.

### Settings (touchscreen and phone)
- Re-run the wizard, change the shop name, and show or hide manufacturers and pieces.
- Manage custom pieces: add, edit, delete. A custom piece goes under an existing manufacturer or under a new custom manufacturer. Custom manufacturers and pieces are kept with the catalogue data and behave like built-in ones (wizard, enable/disable, categories, sorting).
- Storage: used and free space against the 40GB budget.
- Hotspot: on/off. Each time it is switched on the app generates a new 8-digit PIN, which is the Wi-Fi password, and shows it on screen with a QR code. The SSID is fixed per device, set at install.
- There is no separate admin login. The hotspot PIN is the only protection, and the app only answers the touchscreen and clients on the hotspot network (never another network such as Ethernet).
- Settings export and import to a USB stick.

### Phone web page
- Reached at `http://pyrotv.upload` (hotspot DNS), `http://pyrotv.local` or the fixed IP `http://10.42.0.1` after joining the hotspot. The Wi-Fi card shows two QR codes: one joins the Wi-Fi, one opens the page (it carries the IP, which works even when a phone sends DNS over mobile data).
- Everything in Settings, plus upload with a progress bar for video and image, name and category.
- Large uploads are chunked, so a dropped connection resumes.

## Left to the builder
Visual design (dark, firework-themed, large touch targets), exact grid sizing, transcode settings, image placeholder fallbacks, systemd and install scripts, and a one-command installer or SD image build.

## Decisions confirmed by the owner
- **Images:** each catalogue piece comes with a still image of the packaging, stored as `images/<name>.jpg` (or `.png`). Custom pieces upload their own image. The app generates resized thumbnails once, so the grid stays fast on the Pi.
- **Video format:** 1080p30, exported from CapCut on Windows (H.264 MP4). This is hardware-decodable on the Pi 4 as is. Only custom uploads in other formats get transcoded.
- **Hotspot:** off by default, switched on from Settings, with an optional auto-off timer.
