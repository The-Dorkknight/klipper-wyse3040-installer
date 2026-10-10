#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# This tool cannot run on macOS: it depends on Linux-only system tools
# (apt-get, lsblk, xorriso, 7z, rsync, isolinux) and writes directly to
# Linux block devices (/dev/sdX) - there is no safe macOS equivalent to
# fall back to, so this launcher explains that instead of pretending to work.
cd "$(dirname "$0")" || exit 1
echo "========================================================"
echo " Wyse 3040 Klipper Injector - can't run on macOS"
echo "========================================================"
echo
echo "This tool depends on Linux-only tools (apt-get, lsblk, xorriso, 7z,"
echo "rsync, isolinux) and writes directly to Linux block devices. There's"
echo "no safe macOS equivalent, so it can't run here."
echo
echo "Run it instead on:"
echo "  - a real Linux PC (e.g. your Linux Mint laptop), or"
echo "  - a Linux VM on this Mac (UTM, Parallels, VirtualBox - Debian/Ubuntu)"
echo
echo "Once there, from this project's folder:"
echo "  sudo apt install python3-tk"
echo "  python3 wyse_injector.py"
echo
echo "Press Enter to close..."
read -r _ignored
