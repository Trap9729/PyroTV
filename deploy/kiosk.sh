#!/usr/bin/env bash
# PyroTV kiosk browser loop, started by sway (`exec /opt/pyrotv/deploy/kiosk.sh`).
#
# Waits for the backend, then runs Chromium fullscreen on the touchscreen and
# restarts it (after a short delay) whenever it exits or crashes.
# Output goes to sway's stdout, i.e. the journal (`journalctl -t sway`).
#
# UNVERIFIED on hardware: the Chromium flags (especially the Wayland IME ones
# and the memory limits) must be checked on the real 1 GB Pi.
set -euo pipefail

readonly URL="http://127.0.0.1/"          # loopback IP: allowed by the app's guard
readonly STATE_URL="http://127.0.0.1/api/state"
readonly ENV_FILE="/etc/pyrotv.env"
readonly PROFILE_DIR="/tmp/chromium-kiosk"
readonly CACHE_DIR="/tmp/chromium-kiosk-cache"
readonly RESTART_DELAY=2

log() { printf 'kiosk: %s\n' "$*"; }
die() { log "ERROR: $*"; exit 1; }

# Read one KEY=value from the env file without sourcing it (it is also a
# systemd EnvironmentFile; surrounding double quotes are stripped).
env_value() {
    local key="$1" line value
    [[ -r "$ENV_FILE" ]] || return 0
    line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
    value="${line#*=}"
    value="${value#\"}"
    value="${value%\"}"
    printf '%s' "$value"
}

find_chromium() {
    local c
    for c in chromium-browser chromium; do
        if command -v "$c" >/dev/null 2>&1; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

wait_for_backend() {
    local n=0
    until curl -sf -o /dev/null --max-time 2 "$STATE_URL"; do
        n=$((n + 1))
        if (( n % 30 == 0 )); then
            log "still waiting for the backend at $STATE_URL (journalctl -u pyrotv)"
        fi
        sleep 1
    done
}

main() {
    local chromium extra
    chromium="$(find_chromium)" || die "no chromium / chromium-browser binary found"

    extra="$(env_value PYROTV_CHROMIUM_EXTRA_FLAGS)"
    local -a extra_flags=()
    if [[ -n "$extra" ]]; then
        read -r -a extra_flags <<<"$extra"
    fi

    local -a flags=(
        --kiosk
        --ozone-platform=wayland
        # On-screen keyboard: sway offers text-input-v3 only (not v1), and
        # squeekboard listens through input-method-v2. Unknown flags are
        # ignored by Chromium, so the version flag is harmless if absent.
        --enable-wayland-ime
        --wayland-text-input-version=3
        --noerrdialogs
        --disable-infobars
        --disable-pinch
        --overscroll-history-navigation=0
        --autoplay-policy=no-user-gesture-required
        --disable-translate
        --disable-features=Translate
        --password-store=basic
        --no-first-run
        --user-data-dir="$PROFILE_DIR"
        --disk-cache-dir="$CACHE_DIR"
        --disk-cache-size=33554432
        --check-for-update-interval=31536000
        # Memory on a 1 GB Pi: one site, so few renderers are needed; no
        # background services, sync, extensions or component updates.
        --renderer-process-limit=2
        --disable-extensions
        --disable-sync
        --disable-background-networking
        --disable-component-update
        --disable-default-apps
    )

    wait_for_backend
    log "backend is up; starting $chromium"

    while true; do
        # A fresh profile each start: no "restore pages?" bubble after a crash.
        # /tmp lives in RAM under the overlay root, so keep it small.
        rm -rf -- "$PROFILE_DIR" "$CACHE_DIR"
        set +e
        "$chromium" "${flags[@]}" "${extra_flags[@]}" "$URL"
        local rc=$?
        set -e
        log "chromium exited with status $rc; restarting in ${RESTART_DELAY}s"
        sleep "$RESTART_DELAY"
        wait_for_backend
    done
}

main "$@"
