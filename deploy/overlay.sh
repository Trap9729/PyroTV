#!/usr/bin/env bash
# PyroTV: read-only root (overlayfs) and read-only boot partition.
#
#   sudo deploy/overlay.sh status
#   sudo deploy/overlay.sh enable  [--dry-run]   # then reboot
#   sudo deploy/overlay.sh disable [--dry-run]   # then reboot
#
# enable  = `raspi-config nonint do_overlayfs 0` (installs overlayroot, adds
#           `overlayroot=tmpfs` to cmdline.txt and, because the overlay is not
#           active yet, also makes /boot/firmware read-only in /etc/fstab),
#           then changes the token to `overlayroot=tmpfs:recurse=0`.
#
# WHY recurse=0: overlayroot's default (recurse=1) overlays EVERY filesystem
# from /etc/fstab, so writes to /data would silently go to RAM and be lost on
# reboot. With recurse=0 only / is overlaid and /data stays a real, writable
# ext4 mount (overlayroot.conf: "if set to 0, only root will be set to
# read-only, and changes to other filesystems will be permanent").
#
# disable = removes the overlayroot token from cmdline.txt (works while the
#           overlay is active, because /boot/firmware is a real mount; it is
#           remounted read-write for the edit). raspi-config's own disable
#           would NOT match our `:recurse=0` token, so always use this script.
#           When the overlay is NOT active (second run after the reboot) it
#           also makes /boot/firmware writable again (needed for apt kernel
#           upgrades, not for app updates).
#
# UNVERIFIED on hardware: run `overlay.sh status` after each reboot.
set -euo pipefail

CMDLINE="/boot/firmware/cmdline.txt"
BOOT="/boot/firmware"
DRY_RUN=0

log() { printf '[overlay] %s\n' "$*"; }
warn() { printf '[overlay] WARNING: %s\n' "$*" >&2; }
die() { printf '[overlay] ERROR: %s\n' "$*" >&2; exit 1; }

run() {
    if (( DRY_RUN )); then
        log "[dry-run] $*"
    else
        "$@"
    fi
}

usage() {
    sed -n '2,8p' "$0"
    exit "${1:-0}"
}

overlay_active() { grep -q 'overlayroot=tmpfs' /proc/cmdline 2>/dev/null; }
overlay_configured() { grep -q 'overlayroot=tmpfs' "$CMDLINE" 2>/dev/null; }
boot_ro_now() { findmnt -no OPTIONS "$BOOT" 2>/dev/null | grep -qE '(^|,)ro(,|$)'; }

# Run "$@" with /boot/firmware writable, restoring read-only afterwards.
with_boot_rw() {
    local was_ro=0
    if boot_ro_now; then
        was_ro=1
        run mount -o remount,rw "$BOOT"
    fi
    "$@"
    if (( was_ro )); then
        run mount -o remount,ro "$BOOT" || warn "could not remount $BOOT read-only"
    fi
}

edit_cmdline() {
    local expr="$1"
    if (( DRY_RUN )); then
        log "[dry-run] sed -i -E '$expr' $CMDLINE"
        return 0
    fi
    cp -p "$CMDLINE" "$CMDLINE.pyrotv.bak"
    sed -i -E "$expr" "$CMDLINE"
    # cmdline.txt must stay ONE line; refuse to leave anything else behind.
    if [[ "$(wc -l <"$CMDLINE")" -gt 1 ]] || ! grep -q 'root=' "$CMDLINE"; then
        cp -p "$CMDLINE.pyrotv.bak" "$CMDLINE"
        die "cmdline.txt edit went wrong; restored the backup"
    fi
}

cmd_status() {
    log "cmdline.txt (next boot): $(cat "$CMDLINE" 2>/dev/null || echo '?')"
    if overlay_active; then log "overlay root: ACTIVE now"; else log "overlay root: not active now"; fi
    if overlay_configured; then log "overlay root: enabled for next boot"; else log "overlay root: disabled for next boot"; fi
    if grep -q 'overlayroot=tmpfs' "$CMDLINE" 2>/dev/null && ! grep -q 'overlayroot=tmpfs:recurse=0' "$CMDLINE"; then
        warn "cmdline.txt has overlayroot WITHOUT recurse=0: /data would be overlaid too! Run: overlay.sh enable"
    fi
    if boot_ro_now; then log "$BOOT: read-only now"; else log "$BOOT: writable now"; fi
    log "/      : $(findmnt -no FSTYPE,SOURCE,OPTIONS / 2>/dev/null || echo '?')"
    log "/data  : $(findmnt -no FSTYPE,SOURCE,OPTIONS /data 2>/dev/null || echo 'NOT MOUNTED')"
    if [[ "$(findmnt -no FSTYPE /data 2>/dev/null)" == "overlay" ]]; then
        warn "/data is an overlay: writes are NOT persistent. Fix cmdline.txt (recurse=0)."
    fi
}

cmd_enable() {
    command -v raspi-config >/dev/null 2>&1 || (( DRY_RUN )) || die "raspi-config not found"
    log "enabling overlay root + read-only boot partition (raspi-config nonint do_overlayfs 0)"
    run raspi-config nonint do_overlayfs 0
    log "setting overlayroot=tmpfs:recurse=0 so /data stays a real writable mount"
    # Only the bare token is rewritten, so this is idempotent.
    with_boot_rw edit_cmdline 's/(^| )overlayroot=tmpfs( |$)/\1overlayroot=tmpfs:recurse=0\2/'
    if (( ! DRY_RUN )); then
        grep -q 'overlayroot=tmpfs:recurse=0' "$CMDLINE" \
            || die "cmdline.txt does not contain overlayroot=tmpfs:recurse=0; check it by hand"
    fi
    log "done: reboot to activate (sudo reboot), then run: sudo $0 status"
}

cmd_disable() {
    log "removing the overlayroot token from $CMDLINE"
    with_boot_rw edit_cmdline 's/(^| )overlayroot=tmpfs[^ ]*( |$)/\1/; s/^ +//'
    if overlay_active; then
        log "the overlay is active now: reboot, and the root filesystem is writable again."
        log "($BOOT stays read-only; run '$0 disable' once more after the reboot if you need it writable)"
    else
        if grep -E "[[:space:]]${BOOT}[[:space:]]" /etc/fstab | grep -qE 'defaults,ro([[:space:],]|$)'; then
            log "making $BOOT writable again in /etc/fstab"
            if (( DRY_RUN )); then
                log "[dry-run] sed -i 's#\\(.*$BOOT.*\\)defaults,ro\\(.*\\)#\\1defaults\\2#' /etc/fstab"
            else
                sed -i /etc/fstab -e "s#\(.*$BOOT.*\)defaults,ro\(.*\)#\1defaults\2#"
            fi
        fi
    fi
    log "done: reboot (sudo reboot)"
}

main() {
    local cmd="${1:-}"
    [[ -n "$cmd" ]] || usage 1
    shift
    while (( $# )); do
        case "$1" in
            --dry-run) DRY_RUN=1 ;;
            -h|--help) usage 0 ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done
    if (( ! DRY_RUN )) && [[ "$cmd" != status ]] && [[ "$(id -u)" -ne 0 ]]; then
        die "run as root (sudo)"
    fi
    if (( ! DRY_RUN )) && [[ ! -f "$CMDLINE" ]]; then
        die "$CMDLINE not found (is this Raspberry Pi OS Bookworm?)"
    fi
    case "$cmd" in
        status) cmd_status ;;
        enable) cmd_enable ;;
        disable) cmd_disable ;;
        -h|--help) usage 0 ;;
        *) die "unknown command: $cmd" ;;
    esac
}

main "$@"
