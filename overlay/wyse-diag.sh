#!/bin/bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# Boot diagnostics for a Wyse 3040 Klipper unit. Runs once, a couple of
# minutes after boot, and writes its report where nginx serves it at
# /diagnostics.txt (see overlay/nginx/webui.conf.tmpl) — there's no boot
# partition to pull the card out and read, the way there is on a Pi, so
# "browse to the unit" is the equivalent of pi_injector's Card Inquisitor.
LOG_FILE="/home/print/diagnostics.txt"
exec > "$LOG_FILE" 2>&1

echo "=========================================="
echo "   WYSE 3040 KLIPPER BOOT DIAGNOSTICS      "
echo "=========================================="
echo "Timestamp: $(date)  (uptime: $(uptime -p 2>/dev/null || uptime))"
echo ""
echo "--- 1. OS & KERNEL INFO ---"
grep PRETTY_NAME /etc/os-release
uname -a
echo ""
echo "--- 2. NETWORK INTERFACES ---"
ip -br link show
ip -br addr show
cat /etc/wyse-klipper-options 2>/dev/null
echo ""
echo "--- 3. WIFI STATUS ---"
rfkill list all 2>/dev/null
iw reg get 2>/dev/null || echo "iw command unavailable"
if [ -f /etc/hostapd/hostapd.conf ]; then
    echo "-- AP mode (hostapd) --"
    systemctl status hostapd --no-pager 2>/dev/null
    systemctl status dnsmasq --no-pager 2>/dev/null
    journalctl -u hostapd -u dnsmasq -n 60 --no-pager 2>/dev/null
else
    echo "-- Station mode (wpa_supplicant) --"
    systemctl status wpa_supplicant@wlan0 --no-pager 2>/dev/null
    journalctl -u wpa_supplicant@wlan0 -n 60 --no-pager 2>/dev/null
fi
echo ""
echo "--- 4. KLIPPER / MOONRAKER / NGINX ---"
systemctl status klipper --no-pager 2>/dev/null
systemctl status moonraker --no-pager 2>/dev/null
systemctl status nginx --no-pager 2>/dev/null
echo "-- moonraker API probe --"
curl -sS -m 5 http://127.0.0.1:7125/server/info 2>&1
echo ""
echo "-- journalctl moonraker (last 60) --"
journalctl -u moonraker -n 60 --no-pager 2>/dev/null
echo "-- journalctl klipper (last 40) --"
journalctl -u klipper -n 40 --no-pager 2>/dev/null
echo ""
echo "--- 5. MEMORY / OOM CHECK ---"
free -h
swapon --show 2>/dev/null || echo "No swap"
ps aux --sort=-%mem | head -n 15
dmesg | grep -iE "oom|killed process|out of memory" | tail -n 30
echo ""
echo "--- 6. DISK SPACE ---"
df -h /
echo ""
echo "--- 7. CROWSNEST (if installed) ---"
systemctl status crowsnest --no-pager 2>/dev/null || echo "not installed"
echo ""
echo "--- 8. KLIPPLUG (if installed) ---"
if [ -f /etc/systemd/system/klipplug.service ]; then
    systemctl status klipplug --no-pager 2>/dev/null
    echo "-- klipplug API probe --"
    curl -sS -m 5 http://127.0.0.1:7130/api/status 2>&1
    echo ""
    journalctl -u klipplug -n 40 --no-pager 2>/dev/null
else
    echo "not installed"
fi
sync
