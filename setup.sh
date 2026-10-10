#!/usr/bin/env bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# One-click build: installs the tools it needs, generates a login password
# and WiFi passphrase, renders them into the install config, downloads the
# official Debian netinst ISO, and produces klipper-wyse3040-installer.iso.
#
# Run this on a Linux machine with internet access (Debian/Ubuntu, or WSL2
# Ubuntu on Windows). The only step left after this is flashing the ISO to
# a USB stick, e.g. with Balena Etcher. (Prefer a GUI instead? See
# wyse_injector.py in this same repo — it wraps this exact build.)
#
# Safe to re-run — it downloads a fresh Debian ISO once and reuses it, and
# rebuilds klipper-wyse3040-installer.iso each time. Re-running generates a
# NEW random password/passphrase unless you pin them. All options are set
# via environment variables, all optional except COUNTRY:
#   PRINT_PASSWORD=mypw WIFI_PASSPHRASE=myotherpw COUNTRY=GB ./setup.sh
#
# WIFI_MODE=ap (default) broadcasts a hotspot; WIFI_MODE=station joins an
# existing router's WiFi instead, in which case set STATION_SSID and
# STATION_PASSWORD too:
#   WIFI_MODE=station STATION_SSID=MyRouter STATION_PASSWORD=routerpw COUNTRY=GB ./setup.sh
#
# Other options: WEBUI=mainsail|fluidd (default mainsail), CROWSNEST=1 to
# add webcam support (default 0), HOSTNAME_PREFIX (default klipper),
# KLIPPLUG=1 to add the KlipPlug plugin manager (default 0; experimental).
# With KLIPPLUG=1, a klipplug folder or klipplug*.zip placed next to this
# script is baked into the ISO; otherwise each unit downloads KlipPlug
# from GitHub while it installs.
set -euo pipefail
cd "$(dirname "$0")"

ARCH="amd64"
ISO_DIR_URL="https://cdimage.debian.org/debian-cd/current/${ARCH}/iso-cd/"
OUT_ISO="klipper-wyse3040-installer.iso"
WORK="$(pwd)/.build"
EXTRACT="$WORK/extracted"
CREDS_FILE="credentials.txt"

WIFI_MODE="${WIFI_MODE:-ap}"
WEBUI="${WEBUI:-mainsail}"
CROWSNEST="${CROWSNEST:-0}"
KLIPPLUG="${KLIPPLUG:-0}"
COUNTRY="${COUNTRY:-GB}"
HOSTNAME_PREFIX="${HOSTNAME_PREFIX:-klipper}"
STATION_SSID="${STATION_SSID:-}"
STATION_PASSWORD="${STATION_PASSWORD:-}"

if [ "$WIFI_MODE" != "ap" ] && [ "$WIFI_MODE" != "station" ]; then
  echo "WIFI_MODE must be 'ap' or 'station' (got '$WIFI_MODE')"; exit 1
fi
if [ "$WIFI_MODE" = "station" ] && { [ -z "$STATION_SSID" ] || [ -z "$STATION_PASSWORD" ]; }; then
  echo "WIFI_MODE=station needs STATION_SSID and STATION_PASSWORD set too."; exit 1
fi
if ! [[ "$COUNTRY" =~ ^[A-Za-z]{2}$ ]]; then
  echo "COUNTRY must be a 2-letter WiFi regulatory domain, e.g. GB, US, DE (got '$COUNTRY')"; exit 1
fi
COUNTRY="${COUNTRY^^}"

rand_alnum() {
  local n="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 64 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c "$n" || true
  else
    tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$n" || true
  fi
}

echo "==> [1/6] Checking build dependencies"
NEED_PKGS=()
command -v xorriso >/dev/null 2>&1 || NEED_PKGS+=("xorriso")
command -v 7z      >/dev/null 2>&1 || NEED_PKGS+=("p7zip-full")
command -v wget     >/dev/null 2>&1 || NEED_PKGS+=("wget")
command -v rsync    >/dev/null 2>&1 || NEED_PKGS+=("rsync")
{ [ -f /usr/lib/ISOLINUX/isohdpfx.bin ] || [ -f /usr/lib/syslinux/isohdpfx.bin ]; } || NEED_PKGS+=("isolinux")

if [ "${#NEED_PKGS[@]}" -gt 0 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "    installing: ${NEED_PKGS[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${NEED_PKGS[@]}"
  else
    echo "    Missing tools: ${NEED_PKGS[*]}"
    echo "    This script auto-installs deps via apt (Debian/Ubuntu/WSL2 Ubuntu)."
    echo "    On another distro, install the equivalent packages yourself and re-run."
    exit 1
  fi
else
  echo "    all present"
fi

echo "==> [2/6] Generating credentials (or reusing PRINT_PASSWORD / WIFI_PASSPHRASE if you set them)"
PRINT_PASSWORD="${PRINT_PASSWORD:-$(rand_alnum 16)}"
if [ "$WIFI_MODE" = "ap" ]; then
  WIFI_PASSPHRASE="${WIFI_PASSPHRASE:-$(rand_alnum 16)}"
fi
{
  echo "# Credentials baked into every unit built from this ISO."
  echo "# Keep this file safe: anyone who has it can log in over SSH$( [ "$WIFI_MODE" = "ap" ] && echo ", or join the WiFi hotspot," ) on any unit imaged from this build."
  echo "print user login: print / ${PRINT_PASSWORD}"
  if [ "$WIFI_MODE" = "ap" ]; then
    echo "WiFi hotspot passphrase: ${WIFI_PASSPHRASE}"
  else
    echo "Joins WiFi network: ${STATION_SSID}"
  fi
} > "$CREDS_FILE"
chmod 600 "$CREDS_FILE"
echo "    saved to ./$CREDS_FILE"

echo "==> [3/6] Rendering preseed.cfg, options.env and $( [ "$WIFI_MODE" = "ap" ] && echo hostapd.conf || echo wpa_supplicant/wlan0.conf )"
sed "s/__PRINT_PASSWORD__/${PRINT_PASSWORD}/g" preseed.cfg.tmpl > preseed.cfg
sed -e "s/__WEBUI__/${WEBUI}/g" -e "s/__CROWSNEST__/${CROWSNEST}/g" -e "s/__KLIPPLUG__/${KLIPPLUG}/g" \
    -e "s/__WIFI_MODE__/${WIFI_MODE}/g" \
    -e "s/__STATION_SSID__/${STATION_SSID}/g" -e "s/__STATION_PASSWORD__/${STATION_PASSWORD}/g" \
    -e "s/__COUNTRY__/${COUNTRY}/g" -e "s/__HOSTNAME_PREFIX__/${HOSTNAME_PREFIX}/g" \
    overlay/options.env.tmpl > overlay/options.env
if [ "$WIFI_MODE" = "ap" ]; then
  sed -e "s/__WIFI_PASSPHRASE__/${WIFI_PASSPHRASE}/g" -e "s/__COUNTRY__/${COUNTRY}/g" \
    overlay/hostapd/hostapd.conf.tmpl > overlay/hostapd/hostapd.conf
else
  sed -e "s/__STATION_SSID__/${STATION_SSID}/g" -e "s/__STATION_PASSWORD__/${STATION_PASSWORD}/g" \
    -e "s/__COUNTRY__/${COUNTRY}/g" overlay/wpa_supplicant/wlan0.conf.tmpl > overlay/wpa_supplicant/wlan0.conf
fi

# Optional KlipPlug: bake a local copy into the ISO if there is one next to
# this script (a klipplug folder, or a klipplug*.zip). Without one, each
# unit clones it from GitHub during its install.
is_klipplug() { [ -f "$1/tools/printer_install.py" ] && [ -f "$1/src/klipplug/__main__.py" ]; }
rm -rf overlay/klipplug
KLIPPLUG_SOURCE="GitHub, downloaded by each unit while it installs"
if [ "$KLIPPLUG" = "1" ]; then
  mkdir -p "$WORK"
  KP_LOCAL=""
  for cand in ./*[Kk]lip[Pp]lug*/; do
    if [ -d "$cand" ] && is_klipplug "$cand"; then KP_LOCAL="${cand%/}"; fi
  done
  if [ -z "$KP_LOCAL" ]; then
    for zip in ./*[Kk]lip[Pp]lug*.zip; do
      [ -f "$zip" ] || continue
      rm -rf "$WORK/klipplug-unpack"
      mkdir -p "$WORK/klipplug-unpack"
      if 7z -y x "$zip" -o"$WORK/klipplug-unpack" >/dev/null; then
        for cand in "$WORK/klipplug-unpack" "$WORK"/klipplug-unpack/*/; do
          if [ -d "$cand" ] && is_klipplug "$cand"; then KP_LOCAL="${cand%/}"; fi
        done
      fi
    done
  fi
  if [ -n "$KP_LOCAL" ]; then
    # .git is left out on purpose: a clone's settings can hold a login token.
    rsync -a --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' "$KP_LOCAL"/ overlay/klipplug/
    KLIPPLUG_SOURCE="the local copy ($(basename "$KP_LOCAL")), baked into this ISO"
    echo "    KlipPlug: using $KLIPPLUG_SOURCE"
  else
    echo "    KlipPlug: no local copy next to setup.sh - units will download it from GitHub"
  fi
  rm -rf "$WORK/klipplug-unpack"
fi

echo "==> [4/6] Fetching the latest Debian ${ARCH} netinst ISO"
mkdir -p "$WORK"
NETINST_NAME=$(wget -qO- "$ISO_DIR_URL" | grep -oE "debian-[0-9.]+-${ARCH}-netinst\.iso" | sort -V | tail -n1)
if [ -z "$NETINST_NAME" ]; then
  echo "    Could not auto-detect the netinst ISO name from ${ISO_DIR_URL}."
  echo "    Browse that URL yourself and set NETINST_NAME manually near the top of this script."
  exit 1
fi
if [ ! -f "$WORK/$NETINST_NAME" ]; then
  echo "    downloading $NETINST_NAME"
  wget -q --show-progress -O "$WORK/$NETINST_NAME" "${ISO_DIR_URL}${NETINST_NAME}"
else
  echo "    already have $NETINST_NAME, reusing it"
fi

echo "==> [5/6] Extracting and patching the installer"
rm -rf "$EXTRACT"
mkdir -p "$EXTRACT"
7z -y x "$WORK/$NETINST_NAME" -o"$EXTRACT" >/dev/null
chmod -R u+w "$EXTRACT"

cp preseed.cfg "$EXTRACT/preseed.cfg"
rsync -a overlay "$EXTRACT/"

if [ -f "$EXTRACT/isolinux/txt.cfg" ]; then
  sed -i 's#\(append.*\)#\1 auto=true priority=critical preseed/file=/cdrom/preseed.cfg#' \
    "$EXTRACT/isolinux/txt.cfg"
fi
if [ -f "$EXTRACT/isolinux/isolinux.cfg" ]; then
  sed -i 's/^timeout .*/timeout 10/' "$EXTRACT/isolinux/isolinux.cfg" || true
  sed -i 's/^default .*/default auto/' "$EXTRACT/isolinux/isolinux.cfg" || true
fi
if [ -f "$EXTRACT/boot/grub/grub.cfg" ]; then
  sed -i 's#\(linux .*vmlinuz.*\)#\1 auto=true priority=critical preseed/file=/cdrom/preseed.cfg#' \
    "$EXTRACT/boot/grub/grub.cfg"
  sed -i 's/^set timeout=.*/set timeout=10/' "$EXTRACT/boot/grub/grub.cfg" || true
fi

PATCH_OK=1
grep -q 'preseed/file' "$EXTRACT/isolinux/txt.cfg" 2>/dev/null || PATCH_OK=0
grep -q 'preseed/file' "$EXTRACT/boot/grub/grub.cfg" 2>/dev/null || PATCH_OK=0
if [ "$PATCH_OK" -eq 0 ]; then
  echo "    !!! Could not confirm the boot-menu patch took in one or both boot paths."
  echo "        Debian may have reshuffled file layouts since this script was written."
  echo "        Check $EXTRACT/isolinux/txt.cfg and $EXTRACT/boot/grub/grub.cfg by hand"
  echo "        before trusting this ISO on real hardware."
fi

echo "==> [6/6] Building $OUT_ISO"
ISOHDPFX="/usr/lib/ISOLINUX/isohdpfx.bin"
[ -f "$ISOHDPFX" ] || ISOHDPFX="/usr/lib/syslinux/isohdpfx.bin"
xorriso -as mkisofs \
  -r -V "KLIPPER_AUTO_INSTALL" \
  -o "$OUT_ISO" \
  -J -joliet-long -cache-inodes \
  -isohybrid-mbr "$ISOHDPFX" \
  -b isolinux/isolinux.bin -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot \
    -e boot/grub/efi.img -no-emul-boot -isohybrid-gpt-basdat \
  "$EXTRACT"

echo
echo "==================================================================="
echo " Built: $OUT_ISO"
echo " Credentials saved to: $CREDS_FILE"
echo "   print user login (SSH, port 22) : print / ${PRINT_PASSWORD}"
if [ "$WIFI_MODE" = "ap" ]; then
  echo "   WiFi hotspot passphrase         : ${WIFI_PASSPHRASE}"
else
  echo "   Joins WiFi network              : ${STATION_SSID}"
fi
echo " Web UI: ${WEBUI}$( [ "$CROWSNEST" = "1" ] && echo " + Crowsnest" )   WiFi mode: ${WIFI_MODE}   Country: ${COUNTRY}"
if [ "$KLIPPLUG" = "1" ]; then
  echo " KlipPlug (experimental): from ${KLIPPLUG_SOURCE}; page on port 7130"
fi
echo "==================================================================="
echo
echo "Next: open Balena Etcher, flash $OUT_ISO to a USB stick, then boot"
echo "each Wyse 3040 from it. Test in a VM first if you can — see README.md."
