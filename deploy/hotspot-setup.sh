#!/usr/bin/env bash
# PyroTV: create the NetworkManager Wi-Fi hotspot profile `pyrotv-hotspot`.
#
#   sudo deploy/hotspot-setup.sh [--ssid NAME] [--country CC] [--data-dir DIR]
#                                [--ifname wlan0] [--dry-run]
#
# - Sets the Wi-Fi regulatory country first (default SE).
# - (Re)creates the profile: AP mode, WPA2-PSK, 2.4 GHz (band bg), shared IPv4
#   10.42.0.1/24 (NetworkManager runs DHCP via dnsmasq), autoconnect OFF.
#   The password is a random throwaway: the app sets a fresh 8-digit PIN with
#   `nmcli con modify pyrotv-hotspot wifi-sec.psk <pin>` every time staff
#   switch the hotspot on.
# - SSID: PyroTV-<last 4 hex digits of the wlan0 MAC> unless --ssid is given.
#   It is written ONCE to <data-dir>/hotspot.json ({"ssid": ...}, read by the
#   app); an existing file is never overwritten and its SSID is reused, so the
#   SSID stays stable across re-runs. Delete the file to change the SSID.
#
# Idempotent. Run it while the root filesystem is writable (before the overlay
# is enabled), otherwise the profile is lost on reboot.
# UNVERIFIED on hardware: AP mode on the Pi 4's brcmfmac Wi-Fi.
set -euo pipefail

readonly CONNECTION="pyrotv-hotspot"
readonly APP_USER="pyrotv"
SSID=""
COUNTRY="SE"
DATA_DIR="/data"
IFNAME="wlan0"
DRY_RUN=0

log() { printf '[hotspot] %s\n' "$*"; }
warn() { printf '[hotspot] WARNING: %s\n' "$*" >&2; }
die() { printf '[hotspot] ERROR: %s\n' "$*" >&2; exit 1; }

run() {
    if (( DRY_RUN )); then
        log "[dry-run] $*"
    else
        "$@"
    fi
}

usage() {
    sed -n '2,6p' "$0"
    exit "${1:-0}"
}

parse_args() {
    while (( $# )); do
        case "$1" in
            --ssid) SSID="${2:?--ssid needs a value}"; shift ;;
            --country) COUNTRY="${2:?--country needs a value}"; shift ;;
            --data-dir) DATA_DIR="${2:?--data-dir needs a value}"; shift ;;
            --ifname) IFNAME="${2:?--ifname needs a value}"; shift ;;
            --dry-run) DRY_RUN=1 ;;
            -h|--help) usage 0 ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done
    [[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || die "--country must be a 2-letter code like SE"
    if [[ -n "$SSID" ]] && (( $(printf '%s' "$SSID" | wc -c) > 32 )); then
        die "--ssid is longer than 32 bytes"
    fi
}

preflight() {
    if (( DRY_RUN )); then
        return 0
    fi
    [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
    command -v nmcli >/dev/null 2>&1 || die "nmcli not found (apt install network-manager)"
    command -v python3 >/dev/null 2>&1 || die "python3 not found"
    [[ -d "/sys/class/net/$IFNAME" ]] || die "no network interface $IFNAME"
    [[ -d "$DATA_DIR" ]] || die "$DATA_DIR does not exist (is the data partition mounted?)"
    systemctl is-active --quiet NetworkManager || die "NetworkManager is not running"
    if nmcli -t -f DEVICE,STATE device status | grep -q "^${IFNAME}:unmanaged"; then
        die "NetworkManager does not manage $IFNAME (check /etc/NetworkManager/NetworkManager.conf)"
    fi
}

set_country() {
    log "setting the Wi-Fi regulatory country to $COUNTRY"
    if command -v raspi-config >/dev/null 2>&1 || (( DRY_RUN )); then
        # Also unblocks Wi-Fi (rfkill) and persists cfg80211.ieee80211_regdom.
        run raspi-config nonint do_wifi_country "$COUNTRY"
    elif command -v iw >/dev/null 2>&1; then
        run iw reg set "$COUNTRY"
    else
        warn "neither raspi-config nor iw found; regulatory country not set"
    fi
}

# SSID from an existing hotspot.json, else --ssid, else PyroTV-<MAC tail>.
resolve_ssid() {
    local file="$DATA_DIR/hotspot.json" existing mac
    if [[ -f "$file" ]]; then
        existing="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("ssid", ""))' "$file" 2>/dev/null || true)"
        if [[ -n "$existing" ]]; then
            if [[ -n "$SSID" && "$SSID" != "$existing" ]]; then
                warn "$file already sets SSID '$existing'; keeping it (delete the file to change it)"
            fi
            SSID="$existing"
            return 0
        fi
        warn "$file exists but has no usable ssid; it will NOT be overwritten"
    fi
    if [[ -z "$SSID" ]]; then
        if (( DRY_RUN )) && [[ ! -r "/sys/class/net/$IFNAME/address" ]]; then
            SSID="PyroTV-XXXX"
        else
            mac="$(tr -d ':\n' <"/sys/class/net/$IFNAME/address")"
            mac="${mac: -4}"
            SSID="PyroTV-${mac^^}"
        fi
    fi
}

write_hotspot_json() {
    local file="$DATA_DIR/hotspot.json"
    if [[ -e "$file" ]]; then
        log "$file exists; leaving it untouched"
        return 0
    fi
    log "writing $file with SSID $SSID"
    if (( DRY_RUN )); then
        log "[dry-run] write {\"ssid\": \"$SSID\"} to $file (owner $APP_USER)"
        return 0
    fi
    python3 - "$file" "$SSID" <<'PY'
import json, os, sys
path, ssid = sys.argv[1], sys.argv[2]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"ssid": ssid}, f, ensure_ascii=False)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, path)
PY
    chown "$APP_USER:$APP_USER" "$file"
    chmod 0644 "$file"
}

random_password() {
    python3 -c 'import secrets; print(secrets.token_urlsafe(18))'
}

create_connection() {
    local password
    if (( DRY_RUN )); then
        password="<random>"
    else
        password="$(random_password)"
    fi
    if (( ! DRY_RUN )) && nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION"; then
        log "deleting the existing $CONNECTION profile"
        nmcli connection delete "$CONNECTION" >/dev/null
    fi
    log "creating $CONNECTION on $IFNAME (SSID $SSID)"
    # WPA2 only (rsn + ccmp): brcmfmac is known to misbehave in AP mode with
    # mixed WPA/WPA2. Channel 6 keeps the AP on a channel every phone supports.
    local -a args=(
        connection add type wifi ifname "$IFNAME" con-name "$CONNECTION"
        autoconnect no
        ssid "$SSID"
        802-11-wireless.mode ap
        802-11-wireless.band bg
        802-11-wireless.channel 6
        ipv4.method shared
        ipv4.addresses 10.42.0.1/24
        ipv6.method disabled
        wifi-sec.key-mgmt wpa-psk
        wifi-sec.proto rsn
        wifi-sec.pairwise ccmp
        wifi-sec.group ccmp
        wifi-sec.psk "$password"
    )
    if (( DRY_RUN )); then
        log "[dry-run] nmcli ${args[*]}"
    else
        nmcli "${args[@]}" >/dev/null
    fi
}

main() {
    parse_args "$@"
    preflight
    set_country
    resolve_ssid
    create_connection
    write_hotspot_json
    log "done. Test: nmcli con up $CONNECTION   (then: nmcli con down $CONNECTION)"
}

main "$@"
