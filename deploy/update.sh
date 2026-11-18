#!/usr/bin/env bash
# PyroTV: update the app from this git checkout (git pull, reinstall, restart).
#
#   sudo ./deploy/update.sh [--no-pull] [--dry-run]
#
# Only the app is updated: files in /opt/pyrotv, the venv (pip) and the
# systemd unit; then pyrotv.service is restarted and the kiosk browser is
# reloaded. Refuses to run while the overlay root is active, because the
# update would vanish on the next reboot. The full procedure is:
#   sudo deploy/overlay.sh disable && sudo reboot
#   sudo deploy/update.sh
#   sudo deploy/overlay.sh enable  && sudo reboot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO
PULL=1
DRY_RUN=0

log() { printf '[update] %s\n' "$*"; }
die() { printf '[update] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,12p' "$0"
    exit "${1:-0}"
}

while (( $# )); do
    case "$1" in
        --no-pull) PULL=0 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

if grep -q 'overlayroot=tmpfs' /proc/cmdline 2>/dev/null \
    || [[ "$(findmnt -no FSTYPE / 2>/dev/null || true)" == "overlay" ]]; then
    die "the overlay root is active: an update now would be lost on reboot.
First: sudo $SCRIPT_DIR/overlay.sh disable && sudo reboot"
fi
if (( ! DRY_RUN )); then
    [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
fi
[[ -d "$REPO/.git" ]] || die "$REPO is not a git checkout; copy the new sources there or use --no-pull"

if (( PULL )); then
    # Pull as the checkout's owner (not root) so the files stay theirs.
    owner="$(stat -c %U "$REPO" 2>/dev/null || echo root)"
    log "git pull --ff-only in $REPO (as $owner)"
    if (( DRY_RUN )); then
        log "[dry-run] sudo -u $owner git -C $REPO pull --ff-only"
    elif [[ "$owner" == "root" ]]; then
        git -C "$REPO" pull --ff-only
    else
        sudo -u "$owner" git -C "$REPO" pull --ff-only
    fi
fi

args=(--app-only --yes)
if (( DRY_RUN )); then args+=(--dry-run); fi
bash "$SCRIPT_DIR/install.sh" "${args[@]}"

log "reloading the kiosk browser (kiosk.sh restarts it)"
if (( DRY_RUN )); then
    log "[dry-run] pkill -u pyrotv -f -- --user-data-dir=/tmp/chromium-kiosk"
else
    pkill -u pyrotv -f -- '--user-data-dir=/tmp/chromium-kiosk' || log "browser not running"
fi
log "done"
