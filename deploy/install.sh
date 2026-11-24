#!/usr/bin/env bash
# PyroTV appliance installer for Raspberry Pi OS Lite 64-bit (Bookworm), Pi 4B.
#
#   sudo ./deploy/install.sh [--dry-run] [--yes] [--dev] [options]
#
#   --dry-run          print every step and command, change nothing (needs no
#                      root and no Linux tools; reads the disk only if it can)
#   --yes              do not ask before changing the partition table
#   --dev              development mode: skip the read-only overlay root and
#                      read-only boot partition (the SD card stays writable)
#   --disk DEV         the card holding root (default /dev/mmcblk0)
#   --root-size N      grow the root partition to N GiB (default 12)
#   --ssid NAME        hotspot SSID (default PyroTV-<last 4 hex of wlan0 MAC>)
#   --country CC       Wi-Fi regulatory country (default SE)
#   --hostname NAME    host name, for http://NAME.local (default pyrotv)
#   --app-only         only (re)install the app files, venv, unit and the
#                      shipped config (polkit, Chromium policy, sway session),
#                      then restart the service (used by deploy/update.sh)
#   --allow-other-os   run on a release other than Bookworm (UNTESTED; the
#                      installer refuses by default, see README "Which OS image")
#
# Idempotent: safe to re-run. The data partition (/data, label "data") is
# created once and is NEVER reformatted. Read deploy/README.md first: the
# root partition must NOT have been expanded to fill the card (edit
# cmdline.txt before the first boot).
#
# UNVERIFIED on hardware: written on a PC; acceptance happens on the Pi (Task 16).
set -euo pipefail

# --- settings -----------------------------------------------------------------
readonly APP_USER="pyrotv"
readonly APP_DIR="/opt/pyrotv"
readonly DATA_MNT="/data"
readonly DATA_LABEL="data"
readonly ENV_FILE="/etc/pyrotv.env"
readonly MIN_DATA_GIB=4
readonly MIN_ROOT_GIB=6
readonly ALIGN=2048                 # sectors (1 MiB with 512-byte sectors)
# OS releases (/etc/os-release VERSION_CODENAME, space-separated) this installer
# was written and reviewed for. Anything else is refused (see --allow-other-os).
readonly ALLOWED_CODENAMES="bookworm"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SRC_ROOT

DRY_RUN=0
ASSUME_YES=0
DEV_MODE=0
APP_ONLY=0
ALLOW_OTHER_OS=0
DISK="/dev/mmcblk0"
ROOT_SIZE_GIB=12
SSID=""
COUNTRY="SE"
NEW_HOSTNAME="pyrotv"
APP_UID=""
APP_GID=""
WARNINGS=()

# --- helpers ------------------------------------------------------------------
log() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; WARNINGS+=("$*"); }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# run CMD...: execute, or only print it in --dry-run.
run() {
    if (( DRY_RUN )); then
        printf '    [dry-run] %s\n' "$*"
    else
        printf '    + %s\n' "$*"
        "$@"
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# write_file PATH MODE OWNER: write stdin to PATH (atomically), or print it.
write_file() {
    local path="$1" mode="$2" owner="$3" tmp
    if (( DRY_RUN )); then
        printf '    [dry-run] write %s (mode %s, owner %s):\n' "$path" "$mode" "$owner"
        sed 's/^/        | /'
        return 0
    fi
    install -d "$(dirname "$path")"
    tmp="$(mktemp "${path}.XXXXXX")"
    cat >"$tmp"
    chmod "$mode" "$tmp"
    chown "$owner" "$tmp"
    mv -f "$tmp" "$path"
    info "wrote $path"
}

# part_dev DISK N -> /dev/mmcblk0p2 or /dev/sda2
part_dev() {
    if [[ "$1" =~ [0-9]$ ]]; then printf '%sp%s' "$1" "$2"; else printf '%s%s' "$1" "$2"; fi
}

usage() {
    sed -n '2,24p' "$0"
    exit "${1:-0}"
}

parse_args() {
    while (( $# )); do
        case "$1" in
            --dry-run) DRY_RUN=1 ;;
            --yes|-y) ASSUME_YES=1 ;;
            --dev) DEV_MODE=1 ;;
            --app-only) APP_ONLY=1 ;;
            --allow-other-os) ALLOW_OTHER_OS=1 ;;
            --disk) DISK="${2:?--disk needs a value}"; shift ;;
            --root-size)
                ROOT_SIZE_GIB="${2:?--root-size needs a value}"; shift
                ROOT_SIZE_GIB="${ROOT_SIZE_GIB%GiB}"
                ROOT_SIZE_GIB="${ROOT_SIZE_GIB%G}"
                ;;
            --ssid) SSID="${2:?--ssid needs a value}"; shift ;;
            --country) COUNTRY="${2:?--country needs a value}"; shift ;;
            --hostname) NEW_HOSTNAME="${2:?--hostname needs a value}"; shift ;;
            -h|--help) usage 0 ;;
            *) die "unknown option: $1 (see --help)" ;;
        esac
        shift
    done
    [[ "$ROOT_SIZE_GIB" =~ ^[0-9]+$ ]] || die "--root-size must be a whole number of GiB, e.g. 12GiB"
    (( ROOT_SIZE_GIB >= MIN_ROOT_GIB )) || die "--root-size must be at least ${MIN_ROOT_GIB}GiB"
    [[ "$DISK" =~ ^/dev/[A-Za-z0-9]+$ ]] || die "--disk must look like /dev/mmcblk0"
    [[ "$NEW_HOSTNAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "invalid --hostname"
}

overlay_active() {
    grep -q 'overlayroot=tmpfs' /proc/cmdline 2>/dev/null \
        || [[ "$(findmnt -no FSTYPE / 2>/dev/null || true)" == "overlay" ]]
}

# Hard stop unless the OS codename is one this installer was written and
# reviewed for. Trixie (Raspberry Pi OS since October 2025, Imager's default)
# differs in exactly what this script relies on: the first-boot resize
# (`resize` token + resize_early/rpi-resize.service instead of
# init=.../firstboot), Imager customisation (cloud-init) and package names.
check_os() {
    local codename="" pretty="unknown" allowed
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
        # shellcheck disable=SC1091
        pretty="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
    fi
    info "OS: $pretty (VERSION_CODENAME=${codename:-?})"
    for allowed in $ALLOWED_CODENAMES; do
        if [[ "$codename" == "$allowed" ]]; then
            return 0
        fi
    done
    local msg="this installer supports only Raspberry Pi OS with VERSION_CODENAME in: $ALLOWED_CODENAMES
(this system: '${codename:-unknown}'). Re-flash the card with Raspberry Pi Imager:
  Raspberry Pi OS (other) -> Raspberry Pi OS (Legacy, 64-bit) Lite   (Debian 12 Bookworm)
See deploy/README.md, 'Which OS image'. To run anyway (untested): --allow-other-os"
    if (( ALLOW_OTHER_OS )); then
        warn "!!! --allow-other-os: '${codename:-unknown}' is NOT a tested release ($ALLOWED_CODENAMES).
             The first-boot resize, package names and overlay handling may differ. Continuing at your own risk."
    elif (( DRY_RUN )); then
        warn "a real run would STOP here: $msg"
    else
        die "$msg"
    fi
}

preflight() {
    log "Preflight"
    if (( DRY_RUN )); then
        info "dry-run: nothing will be changed."
        if [[ "$(uname -s 2>/dev/null || true)" != "Linux" ]]; then
            info "not running on Linux: system checks skipped, plan shown with placeholders."
            return 0
        fi
    else
        [[ "$(uname -s)" == "Linux" ]] || die "this installer runs on the Raspberry Pi (Linux) only; try --dry-run"
        [[ "$(id -u)" -eq 0 ]] || die "run as root: sudo $0 $*"
    fi
    if overlay_active; then
        if (( DRY_RUN )); then
            warn "the overlay root filesystem is ACTIVE: a real run would refuse (changes would vanish on reboot)"
        else
            die "the overlay root filesystem is active, so every change would be lost on reboot.
Disable it first: sudo $SCRIPT_DIR/overlay.sh disable && sudo reboot"
        fi
    fi
    check_os
    [[ "$(uname -m)" == "aarch64" ]] || warn "expected aarch64 (64-bit Pi OS), got $(uname -m)"
    [[ -f "$SRC_ROOT/backend/pyproject.toml" ]] || die "cannot find the app sources at $SRC_ROOT"
}

# --- packages -------------------------------------------------------------------
chromium_package() {
    # Raspberry Pi OS has shipped its (hardware-accelerated) build as
    # `chromium-browser`, newer images as `chromium` (with chromium-browser as
    # a transitional package). Prefer chromium-browser when it exists so we
    # never swap the Pi build for plain Debian's.
    local p cand
    for p in chromium-browser chromium; do
        cand="$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate:/ {print $2}')"
        if [[ -n "$cand" && "$cand" != "(none)" ]]; then
            printf '%s' "$p"
            return 0
        fi
    done
    printf 'chromium'
}

install_packages() {
    log "Installing packages (apt)"
    local chromium="chromium-browser|chromium"
    if (( ! DRY_RUN )); then
        run apt-get update
        chromium="$(chromium_package)"
    fi
    local -a pkgs=(
        sway "$chromium" mpv ffmpeg
        pipewire pipewire-pulse wireplumber pulseaudio-utils
        network-manager dnsmasq-base polkitd avahi-daemon
        python3-venv python3-pip curl rsync
        parted e2fsprogs util-linux
        dbus-user-session dconf-cli dconf-gsettings-backend
        fonts-dejavu-core zram-tools
    )
    (( DEV_MODE )) || pkgs+=(overlayroot)
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}"
}

# --- user and session -----------------------------------------------------------
create_user() {
    log "User $APP_USER"
    if (( ! DRY_RUN )) && id "$APP_USER" >/dev/null 2>&1; then
        info "user $APP_USER exists"
    else
        run useradd --create-home --user-group --shell /bin/bash "$APP_USER"
    fi
    local g
    local -a groups=()
    for g in video audio render input netdev; do
        if (( DRY_RUN )) || getent group "$g" >/dev/null; then
            groups+=("$g")
        else
            warn "group $g does not exist; $APP_USER not added to it"
        fi
    done
    if (( ${#groups[@]} )); then
        run usermod -aG "$(IFS=,; echo "${groups[*]}")" "$APP_USER"
    fi
    if (( DRY_RUN )) && ! id "$APP_USER" >/dev/null 2>&1; then
        APP_UID="<uid>"; APP_GID="<gid>"
    else
        APP_UID="$(id -u "$APP_USER")"; APP_GID="$(id -g "$APP_USER")"
    fi
    info "$APP_USER uid=$APP_UID gid=$APP_GID"
    # /run/user/<uid> and the user's PipeWire exist from boot, before autologin,
    # so the backend's pactl/mpv always find them.
    run loginctl enable-linger "$APP_USER"
}

setup_session() {
    log "Kiosk session: autologin on tty1 -> sway -> Chromium"
    local home="/home/$APP_USER"
    run systemctl set-default multi-user.target

    write_file /etc/systemd/system/getty@tty1.service.d/autologin.conf 0644 root:root <<EOF
# PyroTV: log in user $APP_USER on tty1 without a password; its
# ~/.bash_profile starts sway.
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $APP_USER --noclear %I \$TERM
EOF

    write_file "$home/.bash_profile" 0644 "$APP_USER:$APP_USER" <<'EOF'
# PyroTV (written by deploy/install.sh): start the kiosk compositor on tty1.
[ -f "$HOME/.profile" ] && . "$HOME/.profile"
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
    # sway's output (and kiosk.sh's) goes to the journal: journalctl -t sway
    exec systemd-cat --identifier=sway sway
fi
EOF

    if (( DRY_RUN )); then
        info "[dry-run] install $SCRIPT_DIR/sway.conf -> $home/.config/sway/config"
    else
        install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$home/.config" "$home/.config/sway"
        install -o "$APP_USER" -g "$APP_USER" -m 0644 "$SCRIPT_DIR/sway.conf" "$home/.config/sway/config"
        info "installed $home/.config/sway/config"
    fi

    # Chromium managed policy: no "Translate this page?" bubble over the kiosk
    # (the page is Swedish but Chromium's own UI language is not). Both policy
    # directories are written: Debian's chromium reads /etc/chromium/..., some
    # Raspberry Pi builds read /etc/chromium-browser/....
    local dir
    for dir in /etc/chromium/policies/managed /etc/chromium-browser/policies/managed; do
        write_file "$dir/pyrotv.json" 0644 root:root <<'EOF'
{
  "TranslateEnabled": false,
  "PasswordManagerEnabled": false,
  "BrowserSignin": 0,
  "MetricsReportingEnabled": false
}
EOF
    done
}

# --- data partition -------------------------------------------------------------
# Globals filled by inspect_disk.
P_LABEL="" P1_TYPE="" P2_START=0 P2_SIZE=0 NPARTS=0 P3_START=0 P3_SIZE=0 DISK_SECTORS=0

inspect_disk() {
    local line node start size type n=0
    DISK_SECTORS="$(cat "/sys/block/$(basename "$DISK")/size")"
    [[ "$(blockdev --getss "$DISK")" == "512" ]] || die "$DISK does not use 512-byte sectors; refusing"
    while read -r line; do
        line="${line%$'\r'}"
        if (( n == 0 )); then
            P_LABEL="$line"
        else
            read -r node start size type <<<"$line"
            case "$node" in
                "$(part_dev "$DISK" 1)") P1_TYPE="$type" ;;
                "$(part_dev "$DISK" 2)") P2_START="$start"; P2_SIZE="$size"; [[ "$type" == "83" ]] || die "partition 2 has type $type, expected 83 (Linux)" ;;
                "$(part_dev "$DISK" 3)") P3_START="$start"; P3_SIZE="$size" ;;
                *) die "unexpected partition $node on $DISK; refusing to touch the disk" ;;
            esac
        fi
        n=$((n + 1))
    done < <(sfdisk --json "$DISK" | python3 -c '
import json, sys
t = json.load(sys.stdin)["partitiontable"]
print(t["label"])
for p in t.get("partitions", []):
    print(p["node"], p["start"], p["size"], p["type"])
')
    NPARTS=$((n - 1))
    [[ "$P_LABEL" == "dos" ]] || die "$DISK has a '$P_LABEL' partition table, expected 'dos' (MBR) as on Pi OS images"
    [[ "$P1_TYPE" =~ ^(b|c|e)$ ]] || die "partition 1 is not the FAT boot partition (type '$P1_TYPE')"
    (( P2_SIZE > 0 )) || die "no root partition 2 on $DISK"
    (( NPARTS == 2 || NPARTS == 3 )) || die "$DISK has $NPARTS partitions, expected 2 or 3"
}

describe_disk() {
    info "disk $DISK: $((DISK_SECTORS / 2048)) MiB, $NPARTS partitions ($P_LABEL)"
    info "root $(part_dev "$DISK" 2): start $P2_START, $((P2_SIZE / 2048)) MiB"
    if (( NPARTS == 3 )); then info "data $(part_dev "$DISK" 3): start $P3_START, $((P3_SIZE / 2048)) MiB"; fi
}

confirm_disk_change() {
    if (( DRY_RUN )); then
        info "[dry-run] a real run asks for confirmation here (or needs --yes)"
        return 0
    fi
    if (( ASSUME_YES )); then
        info "--yes given: proceeding"
        return 0
    fi
    if [[ -t 0 ]]; then
        local answer
        printf '\n    The partition table of %s will be changed as shown above.\n' "$DISK"
        printf '    Type YES (capitals) to continue: '
        read -r answer
        [[ "$answer" == "YES" ]] || die "aborted by user; nothing changed on $DISK"
    else
        die "disk changes need confirmation: re-run with --yes (after reading the plan with --dry-run)"
    fi
}

# Signature check: 0 = something is on the device, 1 = blank. Dies on errors.
has_signature() {
    # Plain `blkid -p` also prints PART_ENTRY_* lines (the partition table
    # entry), so even a blank partition "has output". Ask only for the
    # on-disk signature usage (filesystem, raid, crypto, ...).
    local rc=0 usage
    usage="$(blkid -p -o value -s USAGE "$1" 2>/dev/null)" || rc=$?
    case "$rc" in
        0) [[ -n "$usage" ]] && return 0 || return 1 ;;
        2) return 1 ;;
        *) die "blkid -p $1 failed (exit $rc)" ;;
    esac
}

format_data_partition() {
    local p3="$1"
    if (( ! DRY_RUN )) && has_signature "$p3"; then
        die "$p3 is not blank (blkid -p $p3 shows a signature): refusing to format it.
Inspect it with: sudo blkid -p $p3. If it is an old PyroTV data filesystem
(ext4, label '$DATA_LABEL'), just re-run this installer: it will be reused as is.
Only if its content is unwanted: sudo wipefs -a $p3, then re-run."
    fi
    run mkfs.ext4 -L "$DATA_LABEL" -m 1 "$p3"
}

wait_for_node() {
    local node="$1"
    for _ in $(seq 1 20); do
        [[ -b "$node" ]] && return 0
        sleep 0.5
    done
    die "the kernel does not show $node yet. Reboot (sudo reboot) and re-run the installer:
the new partition is in the table and will then be formatted."
}

setup_data_partition() {
    log "Data partition ($DATA_MNT, ext4, label $DATA_LABEL)"
    local p2 p3
    p2="$(part_dev "$DISK" 2)"
    p3="$(part_dev "$DISK" 3)"

    if (( DRY_RUN )) && { [[ "$(uname -s 2>/dev/null || true)" != "Linux" ]] || [[ ! -b "$DISK" ]] || [[ "$(id -u)" -ne 0 ]]; }; then
        info "cannot inspect $DISK here (not root / not the Pi). On the Pi the installer will:"
        info "  1. refuse unless / is $p2 and $DISK has exactly: p1 FAT boot, p2 ext4 root [, p3 ext4 label '$DATA_LABEL']"
        info "  2. if $p3 exists with label '$DATA_LABEL': reuse it untouched (never reformatted)"
        info "  3. otherwise, if the root already fills the card: abort (see README: cmdline.txt before first boot)"
        info "  4. otherwise (after --yes / typed YES):"
        info "       sfdisk --dump $DISK > /root/pyrotv-ptable-<date>.sfdisk"
        info "       echo '<p2 start>,<${ROOT_SIZE_GIB} GiB in sectors>,83' | sfdisk --no-reread --wipe never --wipe-partitions never -N 2 $DISK"
        info "       echo '<p3 start>,<rest of card>,83' | sfdisk --no-reread --wipe never --wipe-partitions never --append $DISK"
        info "       partx -u $DISK; resize2fs $p2; mkfs.ext4 -L $DATA_LABEL -m 1 $p3 (only if blank)"
        add_fstab_and_mount "$p3"
        return 0
    fi

    # Root must be partition 2 of exactly this disk: this is what stops a
    # wrong --disk (say, a USB stick) from ever being touched.
    local root_src
    root_src="$(findmnt -no SOURCE /)"
    [[ "$root_src" == "$p2" ]] || die "/ is mounted from '$root_src', not $p2. Refusing to touch $DISK.
(--disk must name the card that holds the running root filesystem.)"
    [[ "$(lsblk -dno TYPE "$DISK")" == "disk" ]] || die "$DISK is not a whole disk"

    inspect_disk
    describe_disk

    if (( NPARTS == 3 )); then
        local label type
        label="$(blkid -p -o value -s LABEL "$p3" 2>/dev/null || true)"
        type="$(blkid -p -o value -s TYPE "$p3" 2>/dev/null || true)"
        if [[ "$label" == "$DATA_LABEL" && "$type" == "ext4" ]]; then
            info "$p3 is the existing data partition: reusing it untouched"
        elif [[ -z "$label$type" ]] && ! has_signature "$p3" \
            && (( P3_START >= P2_START + P2_SIZE )) && (( P3_START + P3_SIZE >= DISK_SECTORS - ALIGN )); then
            # A blank partition 3 filling the rest of the card: left by an
            # earlier run that stopped (e.g. kernel did not see it until reboot).
            info "$p3 is blank and fills the rest of the card (earlier interrupted run)"
            info "plan: resize2fs $p2; mkfs.ext4 -L $DATA_LABEL -m 1 $p3"
            confirm_disk_change
            run resize2fs "$p2"
            format_data_partition "$p3"
        else
            die "$p3 exists but is not an ext4 filesystem labelled '$DATA_LABEL' (label='$label' type='$type').
Refusing to touch it. Inspect with: sudo blkid -p $p3"
        fi
        add_fstab_and_mount "$p3"
        return 0
    fi

    # Two partitions: plan growing root to ROOT_SIZE_GIB and p3 in the rest.
    local target new_p2_size p3_start p3_size free_after min_data
    target=$(( ROOT_SIZE_GIB * 1024 * 1024 * 2 ))               # sectors
    min_data=$(( MIN_DATA_GIB * 1024 * 1024 * 2 ))
    free_after=$(( DISK_SECTORS - (P2_START + P2_SIZE) ))
    if (( P2_SIZE >= target )); then
        new_p2_size=$P2_SIZE
        info "root is already $((P2_SIZE / 2048)) MiB (>= ${ROOT_SIZE_GIB} GiB): it is not resized"
    else
        new_p2_size=$(( (target + ALIGN - 1) / ALIGN * ALIGN ))
    fi
    p3_start=$(( (P2_START + new_p2_size + ALIGN - 1) / ALIGN * ALIGN ))
    p3_size=$(( DISK_SECTORS - p3_start ))
    if (( free_after < min_data )) && (( P2_SIZE >= target )); then
        die "the root partition already fills the card ($((free_after / 2048)) MiB free after it):
Raspberry Pi OS expanded it on the first boot, so there is no room for $DATA_MNT.
Re-flash the card and, BEFORE the first boot, remove the token
    init=/usr/lib/raspberrypi-sys-mods/firstboot
from cmdline.txt on the boot partition (see deploy/README.md), then re-run."
    fi
    (( p3_size >= min_data )) || die "only $((p3_size / 2048)) MiB would be left for $DATA_MNT (minimum ${MIN_DATA_GIB} GiB); use a bigger card or a smaller --root-size"

    local backup
    backup="/root/pyrotv-ptable-$(date +%Y%m%d-%H%M%S).sfdisk"
    info "plan for $DISK (sectors of 512 bytes):"
    info "  root $p2: start $P2_START, size $P2_SIZE -> $new_p2_size ($((new_p2_size / 2048)) MiB)"
    info "  data $p3: start $p3_start, size $p3_size ($((p3_size / 2048)) MiB), ext4 label '$DATA_LABEL'"
    info "commands:"
    info "  sfdisk --dump $DISK > $backup"
    if (( new_p2_size > P2_SIZE )); then
        info "  echo '$P2_START,$new_p2_size,83' | sfdisk --no-reread --wipe never --wipe-partitions never -N 2 $DISK"
    fi
    info "  echo '$p3_start,$p3_size,83' | sfdisk --no-reread --wipe never --wipe-partitions never --append $DISK"
    info "  partx -u $DISK; udevadm settle; resize2fs $p2"
    info "  mkfs.ext4 -L $DATA_LABEL -m 1 $p3   (only if blkid finds nothing on it)"
    confirm_disk_change

    if (( DRY_RUN )); then
        info "[dry-run] (the commands above would run now)"
    else
        sfdisk --dump "$DISK" >"$backup"
        info "partition table backup: $backup (restore: sfdisk --no-reread $DISK < $backup)"
        if (( new_p2_size > P2_SIZE )); then
            printf '%s,%s,83\n' "$P2_START" "$new_p2_size" | sfdisk --no-reread --wipe never --wipe-partitions never -N 2 "$DISK" \
                || die "sfdisk could not grow partition 2 (backup: $backup)"
        fi
        printf '%s,%s,83\n' "$p3_start" "$p3_size" | sfdisk --no-reread --wipe never --wipe-partitions never --append "$DISK" \
            || die "sfdisk could not add partition 3 (backup: $backup); reboot and re-run"
        partx -u "$DISK" || warn "partx -u $DISK failed; the kernel may need a reboot to see the changes"
        udevadm settle || true
        resize2fs "$p2" || warn "resize2fs $p2 failed: reboot, then run: sudo resize2fs $p2"
        wait_for_node "$p3"
        format_data_partition "$p3"
    fi
    add_fstab_and_mount "$p3"
}

add_fstab_and_mount() {
    local p3="$1" fs_uuid="<uuid of $1>"
    if (( ! DRY_RUN )); then
        fs_uuid="$(blkid -p -o value -s UUID "$p3")"
        [[ -n "$fs_uuid" ]] || die "cannot read the filesystem UUID of $p3"
    fi
    # UUID (not LABEL=data): a USB stick that happens to be labelled "data"
    # must never be mounted as /data at boot.
    # nofail: if /data is missing the Pi still boots (SSH works for repair)
    # instead of hanging in emergency mode with no keyboard attached;
    # pyrotv.service Requires=data.mount, so the app does not start without it.
    local line="UUID=$fs_uuid  $DATA_MNT  ext4  defaults,noatime,commit=30,nofail  0  2"
    if (( ! DRY_RUN )) && awk '$1 !~ /^#/ && $2 == "'"$DATA_MNT"'" {found=1} END {exit !found}' /etc/fstab; then
        info "/etc/fstab already has an entry for $DATA_MNT (kept as is):"
        info "  $(awk '$1 !~ /^#/ && $2 == "'"$DATA_MNT"'"' /etc/fstab)"
    elif (( DRY_RUN )); then
        info "[dry-run] add to /etc/fstab (if no $DATA_MNT entry): $line"
    else
        cp -p /etc/fstab /etc/fstab.pyrotv.bak
        printf '%s\n' "$line" >>/etc/fstab
        info "added to /etc/fstab: $line"
    fi
    run install -d -m 0755 "$DATA_MNT"
    run systemctl daemon-reload
    if (( DRY_RUN )); then
        info "[dry-run] mount $DATA_MNT (via data.mount) if not mounted"
    elif ! mountpoint -q "$DATA_MNT"; then
        run mount "$DATA_MNT"
    fi
    if (( ! DRY_RUN )); then
        local src
        src="$(findmnt -no SOURCE "$DATA_MNT")"
        [[ "$src" == "$p3" ]] || die "$DATA_MNT is mounted from '$src', expected $p3; fix /etc/fstab"
    fi
}

setup_data_dirs() {
    log "Data directories in $DATA_MNT (owner $APP_USER)"
    local d
    for d in media/videos media/images custom/videos custom/images cache/thumbs tmp/uploads; do
        run install -d -m 0755 "$DATA_MNT/$d"
    done
    # Everything except the (possibly huge, read-only for the app) media files
    # and lost+found belongs to the app user.
    if (( DRY_RUN )); then
        info "[dry-run] chown $APP_USER: $DATA_MNT and everything below it except media files and lost+found"
    else
        find "$DATA_MNT" -xdev \( -path "$DATA_MNT/media/*/*" -o -path "$DATA_MNT/lost+found" \) -prune \
            -o -exec chown "$APP_USER:$APP_USER" {} +
    fi
}

# --- app ---------------------------------------------------------------------------
install_app() {
    log "App -> $APP_DIR (venv $APP_DIR/venv)"
    run install -d -m 0755 "$APP_DIR"
    local -a rs=(rsync -a --delete --chown=root:root
        --exclude __pycache__ --exclude '*.pyc' --exclude '*.egg-info'
        --exclude .pytest_cache --exclude build --exclude tests --exclude node_modules)
    run "${rs[@]}" "$SRC_ROOT/backend/" "$APP_DIR/backend/"
    run "${rs[@]}" "$SRC_ROOT/frontend/" "$APP_DIR/frontend/"
    run "${rs[@]}" "$SRC_ROOT/catalogue/" "$APP_DIR/catalogue/"
    run "${rs[@]}" "$SCRIPT_DIR/" "$APP_DIR/deploy/"
    # Windows checkouts may lose the executable bit.
    run chmod 0755 "$APP_DIR/deploy/install.sh" "$APP_DIR/deploy/kiosk.sh" \
        "$APP_DIR/deploy/hotspot-setup.sh" "$APP_DIR/deploy/overlay.sh" "$APP_DIR/deploy/update.sh"
    if (( DRY_RUN )) || [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
        run python3 -m venv "$APP_DIR/venv"
    fi
    # Runtime dependencies only (no `dev` extra). The second call forces the
    # app itself to be reinstalled even when its version number is unchanged.
    run "$APP_DIR/venv/bin/pip" install --no-cache-dir --upgrade pip
    run "$APP_DIR/venv/bin/pip" install --no-cache-dir "$APP_DIR/backend"
    run "$APP_DIR/venv/bin/pip" install --no-cache-dir --no-deps --force-reinstall "$APP_DIR/backend"
}

setup_env_file() {
    log "Environment file $ENV_FILE"
    if (( ! DRY_RUN )) && [[ -f "$ENV_FILE" ]]; then
        info "$ENV_FILE exists: kept (only placeholders are filled in)"
    else
        run install -m 0644 -o root -g root "$SCRIPT_DIR/pyrotv.env.example" "$ENV_FILE"
    fi
    run sed -i "s/@PYROTV_UID@/$APP_UID/g" "$ENV_FILE"

    if [[ -f "$SRC_ROOT/docs/spike-results.md" ]]; then
        info "docs/spike-results.md exists: copy its sink names / mpv args into $ENV_FILE"
    fi
    local sink_hdmi="" sink_jack=""
    if [[ -f "$ENV_FILE" ]]; then
        sink_hdmi="$(grep -E '^PYROTV_SINK_HDMI=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"' || true)"
        sink_jack="$(grep -E '^PYROTV_SINK_JACK=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"' || true)"
    fi
    if [[ -z "$sink_hdmi" || -z "$sink_jack" ]]; then
        warn "PYROTV_SINK_HDMI / PYROTV_SINK_JACK are empty in $ENV_FILE: audio will NOT switch
             between TV and speakers. After the first reboot (session running), list sinks with:
               sudo -u $APP_USER XDG_RUNTIME_DIR=/run/user/$APP_UID pactl list short sinks
             put the names into $ENV_FILE and: sudo systemctl restart pyrotv
             (with the overlay active: disable it first, see deploy/README.md)"
    fi
}

install_unit() {
    log "systemd unit pyrotv.service"
    sed "s/@PYROTV_UID@/$APP_UID/g" "$SCRIPT_DIR/pyrotv.service" \
        | write_file /etc/systemd/system/pyrotv.service 0644 root:root
    run systemctl daemon-reload
    run systemctl enable pyrotv.service
}

install_system_config() {
    log "System configuration (polkit, USB automount, journald, swap)"
    if (( DRY_RUN )); then
        info "[dry-run] install $SCRIPT_DIR/00-pyrotv-nm.rules -> /etc/polkit-1/rules.d/"
    else
        # The "00-" prefix matters: polkit stops at the first rule that decides, and
        # rules run in filename order, so a "50-" rule was answered (denied) by an
        # earlier stock rule before it was ever consulted. Drop the old name.
        rm -f /etc/polkit-1/rules.d/50-pyrotv-nm.rules
        install -D -m 0644 "$SCRIPT_DIR/00-pyrotv-nm.rules" /etc/polkit-1/rules.d/00-pyrotv-nm.rules
        info "installed /etc/polkit-1/rules.d/00-pyrotv-nm.rules"
    fi

    sed -e "s/@PYROTV_UID@/$APP_UID/g" -e "s/@PYROTV_GID@/$APP_GID/g" "$SCRIPT_DIR/99-pyrotv-usb.rules" \
        | write_file /etc/udev/rules.d/99-pyrotv-usb.rules 0644 root:root
    run udevadm control --reload-rules

    # Under the overlay root every write to / lands in RAM: keep the journal
    # in RAM on purpose, and small.
    write_file /etc/systemd/journald.conf.d/50-pyrotv.conf 0644 root:root <<'EOF'
# PyroTV: journal in RAM only (the root filesystem is a RAM overlay anyway).
[Journal]
Storage=volatile
RuntimeMaxUse=32M
EOF

    # Swap: a swap FILE cannot live on the overlay root, so use compressed RAM
    # swap (zram) instead of dphys-swapfile. UNVERIFIED: tune on the 1 GB Pi.
    if (( DRY_RUN )) || [[ -f /etc/default/zramswap ]]; then
        run sed -i -E 's/^#?[[:space:]]*PERCENT=.*/PERCENT=50/; s/^#?[[:space:]]*ALGO=.*/ALGO=lz4/' /etc/default/zramswap
        run systemctl enable zramswap.service
    else
        warn "/etc/default/zramswap missing (zram-tools not installed?): no swap"
    fi
    if (( ! DRY_RUN )) && systemctl cat dphys-swapfile.service >/dev/null 2>&1; then
        run dphys-swapfile swapoff || true
        run dphys-swapfile uninstall || true
        run systemctl disable dphys-swapfile.service
    elif (( DRY_RUN )); then
        info "[dry-run] disable dphys-swapfile if present (swapoff, uninstall, systemctl disable)"
    fi
}

set_hostname() {
    log "Host name $NEW_HOSTNAME (http://$NEW_HOSTNAME.local via avahi)"
    if (( ! DRY_RUN )) && [[ "$(hostname)" == "$NEW_HOSTNAME" ]]; then
        info "already $NEW_HOSTNAME"
    elif (( DRY_RUN )) || have raspi-config; then
        run raspi-config nonint do_hostname "$NEW_HOSTNAME"
    else
        run hostnamectl set-hostname "$NEW_HOSTNAME"
        warn "raspi-config not found: check /etc/hosts has 127.0.1.1 $NEW_HOSTNAME"
    fi
    run systemctl enable avahi-daemon.service
}

setup_hotspot() {
    log "Hotspot profile"
    local -a args=(--country "$COUNTRY" --data-dir "$DATA_MNT")
    if [[ -n "$SSID" ]]; then args+=(--ssid "$SSID"); fi
    if (( DRY_RUN )); then args+=(--dry-run); fi
    run systemctl enable NetworkManager.service
    bash "$SCRIPT_DIR/hotspot-setup.sh" "${args[@]}"
}

start_backend() {
    log "Starting the backend now (it also starts on every boot)"
    run systemctl restart pyrotv.service
    if (( ! DRY_RUN )); then
        sleep 3
        systemctl --no-pager --lines=5 status pyrotv.service || warn "pyrotv.service is not running: journalctl -u pyrotv"
    fi
}

enable_overlay() {
    if (( DEV_MODE )); then
        log "--dev: overlay root and read-only boot partition NOT enabled"
        return 0
    fi
    log "Read-only root (overlay) + read-only boot partition: LAST step, so all config above is kept"
    local -a args=(enable)
    if (( DRY_RUN )); then args+=(--dry-run); fi
    bash "$SCRIPT_DIR/overlay.sh" "${args[@]}"
}

summary() {
    log "Summary"
    if (( ${#WARNINGS[@]} )); then
        local w
        for w in "${WARNINGS[@]}"; do printf '    ! %s\n' "$w"; done
    else
        info "no warnings"
    fi
    if (( DRY_RUN )); then
        info "dry-run finished: nothing was changed."
    elif (( APP_ONLY )); then
        info "app updated."
    else
        info "done. Reboot now: sudo reboot"
        info "after the reboot: sudo $APP_DIR/deploy/overlay.sh status   (check /data is ext4, not overlay)"
    fi
}

main() {
    parse_args "$@"
    preflight "$@"
    if (( APP_ONLY )); then
        if (( DRY_RUN )) && ! id "$APP_USER" >/dev/null 2>&1; then
            APP_UID="<uid>"
        else
            APP_UID="$(id -u "$APP_USER")" || die "user $APP_USER missing: run the full installer"
        fi
        install_app
        setup_env_file
        install_unit
        # Config that ships in this repo and changes between releases: the
        # polkit rule, Chromium policy, udev rule and the sway/kiosk session.
        # All idempotent; none touches the data partition, hotspot or overlay.
        install_system_config
        setup_session
        start_backend
        summary
        return 0
    fi
    install_packages
    create_user
    setup_data_partition
    setup_data_dirs
    install_app
    setup_env_file
    install_unit
    install_system_config
    setup_session
    set_hostname
    setup_hotspot
    start_backend
    enable_overlay
    summary
}

main "$@"
