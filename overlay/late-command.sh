#!/bin/bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# Runs inside the freshly-installed target (via d-i's `in-target`) at the end
# of the Debian install. Installs and wires up Klipper + Moonraker + a web
# UI, the WiFi stack (AP hotspot or station/client, per options.env), and
# the ethernet fallback.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

SETUP_DIR=/opt/klipper-setup
PRINT_USER=print
HOME_DIR=/home/${PRINT_USER}

log() { echo "=== $* ==="; }
fail() { echo "!!! FAILED: $* !!!" >&2; exit 1; }

# shellcheck source=/opt/klipper-setup/options.env
source "$SETUP_DIR/options.env" || fail "options.env missing/unreadable"
: "${WEBUI:=mainsail}" "${CROWSNEST:=0}" "${KLIPPLUG:=0}" "${WIFI_MODE:=ap}" "${COUNTRY:=GB}" "${HOSTNAME_PREFIX:=klipper}"

# Network hiccups are the single most common reason an otherwise-good build
# fails on a batch run (this step needs a real internet connection to clone
# Klipper/Moonraker and download a web UI). A transient DNS blip used to
# abort the whole unattended install with no way to resume — retry instead.
retry() {
  local tries=5 delay=10 n=1
  until "$@"; do
    if [ "$n" -ge "$tries" ]; then
      fail "gave up after $n attempts: $*"
    fi
    echo "    attempt $n/$tries failed, retrying in ${delay}s: $*"
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
  done
}
# Same as retry(), but for optional steps (Crowsnest): gives up quietly by
# returning 1 instead of aborting the whole provisioning run.
retry_soft() {
  local tries=3 delay=10 n=1
  until "$@"; do
    if [ "$n" -ge "$tries" ]; then
      return 1
    fi
    echo "    attempt $n/$tries failed, retrying in ${delay}s: $*"
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
  done
}

log "1/10 Installing packages"
retry apt-get update
PKGS=(
  git curl ca-certificates unzip pkg-config
  python3 python3-venv python3-dev
  gcc make libc6-dev libffi-dev libncurses-dev libusb-dev
  liblmdb-dev libsodium-dev libopenjp2-7 libjpeg-dev
  libcurl4-openssl-dev libssl-dev zlib1g-dev
  iproute2 wireless-tools iw wpasupplicant
  nginx-light avahi-daemon systemd-zram-generator
)
if [ "$WIFI_MODE" = "ap" ]; then
  PKGS+=(hostapd dnsmasq)
fi
retry apt-get install -y --no-install-recommends "${PKGS[@]}"

log "2/10 Ensuring print user exists with the right groups"
id -u "$PRINT_USER" &>/dev/null || useradd -m -s /bin/bash "$PRINT_USER"
usermod -aG sudo,dialout,tty,plugdev "$PRINT_USER"

log "3/10 Cloning Klipper + Moonraker"
retry sudo -u "$PRINT_USER" git clone --depth=1 https://github.com/Klipper3d/klipper.git "$HOME_DIR/klipper"
retry sudo -u "$PRINT_USER" git clone --depth=1 https://github.com/Arksine/moonraker.git "$HOME_DIR/moonraker"

log "4/10 Building Python virtualenvs"
sudo -u "$PRINT_USER" python3 -m venv "$HOME_DIR/klippy-env"
sudo -u "$PRINT_USER" "$HOME_DIR/klippy-env/bin/pip" install -q --upgrade pip
sudo -u "$PRINT_USER" "$HOME_DIR/klippy-env/bin/pip" install -q -r "$HOME_DIR/klipper/scripts/klippy-requirements.txt"

sudo -u "$PRINT_USER" python3 -m venv "$HOME_DIR/moonraker-env"
sudo -u "$PRINT_USER" "$HOME_DIR/moonraker-env/bin/pip" install -q --upgrade pip
sudo -u "$PRINT_USER" "$HOME_DIR/moonraker-env/bin/pip" install -q -r "$HOME_DIR/moonraker/scripts/moonraker-requirements.txt"

log "5/10 Installing the $WEBUI web UI"
case "$WEBUI" in
  fluidd)
    WEBUI_ZIP_URL="https://github.com/fluidd-core/fluidd/releases/latest/download/fluidd.zip"
    ;;
  mainsail|*)
    WEBUI_ZIP_URL="https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip"
    ;;
esac
retry curl -fsSL -o /tmp/webui.zip "$WEBUI_ZIP_URL"
sudo -u "$PRINT_USER" mkdir -p "$HOME_DIR/$WEBUI"
sudo -u "$PRINT_USER" unzip -q -o /tmp/webui.zip -d "$HOME_DIR/$WEBUI"
rm -f /tmp/webui.zip

log "6/10 Laying out printer_data + config files"
sudo -u "$PRINT_USER" mkdir -p "$HOME_DIR/printer_data"/{config,logs,gcodes,comms}
install -o "$PRINT_USER" -g "$PRINT_USER" -m 644 \
  "$SETUP_DIR/moonraker.conf" "$HOME_DIR/printer_data/config/moonraker.conf"
[ -f "$HOME_DIR/printer_data/config/printer.cfg" ] || \
  sudo -u "$PRINT_USER" bash -c "echo '# Fill this in for your printer/board before starting Klipper.' > '$HOME_DIR/printer_data/config/printer.cfg'"

install -m 644 "$SETUP_DIR/systemd/klipper.service" /etc/systemd/system/klipper.service
install -m 644 "$SETUP_DIR/systemd/moonraker.service" /etc/systemd/system/moonraker.service
install -m 644 "$SETUP_DIR/systemd/zram-generator.conf" /etc/systemd/zram-generator.conf

log "7/10 Networking: eth0 (DHCP + link-local fallback) + wlan0 ($WIFI_MODE)"
mkdir -p /etc/systemd/network
install -m 644 "$SETUP_DIR/network/10-eth0.network" /etc/systemd/network/10-eth0.network
systemctl enable systemd-networkd systemd-resolved avahi-daemon

if [ "$WIFI_MODE" = "station" ]; then
  install -m 644 "$SETUP_DIR/network/20-wlan0-station.network" /etc/systemd/network/20-wlan0.network
  install -d -m 700 /etc/wpa_supplicant
  install -m 600 "$SETUP_DIR/wpa_supplicant/wlan0.conf" /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
  systemctl enable wpa_supplicant@wlan0
  # Same "the radio takes a moment to enumerate" race as hostapd below.
  mkdir -p /etc/systemd/system/wpa_supplicant@wlan0.service.d
  cat > /etc/systemd/system/wpa_supplicant@wlan0.service.d/override.conf <<'EOF'
[Unit]
After=sys-subsystem-net-devices-wlan0.device
Wants=sys-subsystem-net-devices-wlan0.device
[Service]
Restart=on-failure
RestartSec=5
EOF
else
  install -m 644 "$SETUP_DIR/network/20-wlan0.network" /etc/systemd/network/20-wlan0.network

  install -d -m 755 /etc/hostapd
  install -m 600 "$SETUP_DIR/hostapd/hostapd.conf" /etc/hostapd/hostapd.conf
  sed -i 's/^#\?DAEMON_CONF=.*/DAEMON_CONF="\/etc\/hostapd\/hostapd.conf"/' /etc/default/hostapd
  systemctl unmask hostapd
  systemctl enable hostapd
  # Wait for the wireless NIC (USB dongle or internal SDIO card) to actually
  # enumerate before hostapd tries to bind it — SDIO wifi in particular can
  # be slow to come up, and hostapd gives up permanently if started too early.
  mkdir -p /etc/systemd/system/hostapd.service.d
  cat > /etc/systemd/system/hostapd.service.d/override.conf <<'EOF'
[Unit]
After=sys-subsystem-net-devices-wlan0.device systemd-networkd.service
Wants=sys-subsystem-net-devices-wlan0.device
[Service]
Restart=on-failure
RestartSec=5
EOF

  install -m 644 "$SETUP_DIR/dnsmasq/wlan0.conf" /etc/dnsmasq.d/wlan0.conf
  systemctl enable dnsmasq
  # Same slow-SDIO-wifi-enumeration race as hostapd above applies here too —
  # dnsmasq's default unit doesn't wait for wlan0 to exist before binding.
  mkdir -p /etc/systemd/system/dnsmasq.service.d
  cat > /etc/systemd/system/dnsmasq.service.d/override.conf <<'EOF'
[Unit]
After=sys-subsystem-net-devices-wlan0.device systemd-networkd.service
Wants=sys-subsystem-net-devices-wlan0.device
[Service]
Restart=on-failure
RestartSec=5
EOF
fi

log "8/10 Web server (nginx serving $WEBUI, proxying Moonraker)"
sed -e "s#__WEBUI_ROOT__#${HOME_DIR}/${WEBUI}#" -e "s#__DIAG_FILE__#${HOME_DIR}/diagnostics.txt#" \
  "$SETUP_DIR/nginx/webui.conf.tmpl" > /etc/nginx/sites-available/webui
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/webui /etc/nginx/sites-enabled/webui

log "9/10 Boot diagnostics + optional Crowsnest webcam + optional KlipPlug"
install -m 755 "$SETUP_DIR/wyse-diag.sh" /usr/local/bin/wyse-diag.sh
install -m 644 "$SETUP_DIR/systemd/wyse-diag.service" /etc/systemd/system/wyse-diag.service
systemctl enable wyse-diag.service

if [ "$CROWSNEST" = "1" ]; then
  if retry_soft sudo -u "$PRINT_USER" git clone --depth=1 https://github.com/mainsail-crew/crowsnest.git "$HOME_DIR/crowsnest"; then
    cd "$HOME_DIR/crowsnest" || fail "crowsnest clone directory missing"
    BASE_USER="$PRINT_USER" CROWSNEST_UNATTENDED=1 CROWSNEST_ADD_CROWSNEST_MOONRAKER=1 \
      CROWSNEST_SKIP_REBOOT_PROMPT=1 make install || \
      echo "    WARNING: Crowsnest install failed — webcam support skipped, everything else continues."
    cd /
  else
    echo "    WARNING: could not download Crowsnest — webcam support skipped, everything else continues."
  fi
fi

# Optional KlipPlug plugin manager (a separate project, AGPL-3.0). Its step
# is its own script and can never fail the install: on any problem it takes
# back what it did and this just records that it isn't there.
KLIPPLUG_RESULT="no"
if [ "$KLIPPLUG" = "1" ]; then
  if bash "$SETUP_DIR/klipplug-step.sh" "$PRINT_USER" "$HOME_DIR" "$SETUP_DIR"; then
    KLIPPLUG_RESULT="installed (http://<this unit>:7130)"
  else
    KLIPPLUG_RESULT="asked for, but NOT installed (see the install log)"
    echo "    WARNING: KlipPlug was not installed — everything else continues."
  fi
  cd /
fi

log "10/10 Per-unit hostname/SSID, boot params, enabling services"
# Derive a short unique suffix from this unit's real eth0 MAC address so a
# batch of units don't all broadcast the same hostname/SSID.
if [ -r /sys/class/net/eth0/address ]; then
  SUFFIX=$(tr -d ':' < /sys/class/net/eth0/address | tail -c 7)
else
  SUFFIX=$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')
fi
NEW_HOSTNAME="${HOSTNAME_PREFIX}-${SUFFIX}"
echo "$NEW_HOSTNAME" > /etc/hostname
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${NEW_HOSTNAME}/" /etc/hosts
if [ "$WIFI_MODE" = "ap" ]; then
  sed -i "s/^ssid=.*/ssid=${HOSTNAME_PREFIX^}-${SUFFIX}/" /etc/hostapd/hostapd.conf
fi

# A small, human-readable record of how this unit was built — read by
# wyse-diag.sh and handy over SSH later ("what did I actually image this
# with?").
cat > /etc/wyse-klipper-options <<EOF
Built: $(date -u +%FT%TZ)
Web UI: ${WEBUI}
Crowsnest: ${CROWSNEST}
KlipPlug: ${KLIPPLUG_RESULT}
WiFi mode: ${WIFI_MODE}
Hostname: ${NEW_HOSTNAME}
EOF

# Disable predictable network interface naming so the interfaces are always
# eth0 / wlan0, matching the network/hostapd/dnsmasq/wpa_supplicant configs
# above.
if grep -q '^GRUB_CMDLINE_LINUX=' /etc/default/grub; then
  sed -i 's/^GRUB_CMDLINE_LINUX=.*/GRUB_CMDLINE_LINUX="net.ifnames=0 biosdevname=0"/' /etc/default/grub
else
  echo 'GRUB_CMDLINE_LINUX="net.ifnames=0 biosdevname=0"' >> /etc/default/grub
fi
update-grub

systemctl enable klipper.service moonraker.service nginx.service

apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/*
rm -rf "$SETUP_DIR"

log "Provisioning complete: hostname=${NEW_HOSTNAME}, wifi=${WIFI_MODE}, webui=${WEBUI}"
