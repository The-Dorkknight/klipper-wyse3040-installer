#!/bin/sh
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# Double-click launcher for Linux. Finds Python 3 + Tkinter and starts the
# GUI; the GUI asks for root itself (via pkexec) only at the moment it
# actually needs to write a disk or install a build dependency - this
# launcher never needs to run as root.
cd "$(dirname "$0")" || exit 1

say_and_wait() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Wyse 3040 Klipper Injector" --text="$1" 2>/dev/null
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center "$1"
    else
        printf '%s\n' "$1"
        echo
        echo "Press Enter to close..."
        read -r _ignored
    fi
}

PY=""
FOUND_ANY=""
for cand in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
        FOUND_ANY="$cand"
        if "$cand" -c "import tkinter" >/dev/null 2>&1; then PY="$cand"; break; fi
    fi
done

if [ -z "$FOUND_ANY" ]; then
    say_and_wait "Python 3 wasn't found on this system.

Install it, then run this launcher again, e.g.:
  sudo apt install python3 python3-tk   (Debian/Ubuntu/Mint)
  sudo dnf install python3 python3-tkinter   (Fedora)
  sudo pacman -S python tk   (Arch)"
    exit 1
fi

if [ -z "$PY" ]; then
    say_and_wait "Python's Tkinter GUI toolkit isn't installed for $FOUND_ANY.

Install it, then run this launcher again:
  sudo apt install python3-tk   (Debian/Ubuntu/Mint)
  sudo dnf install python3-tkinter   (Fedora)
  sudo pacman -S tk   (Arch)

No GUI on this machine? setup.sh in this same folder builds the ISO from
the command line instead - see README.md."
    exit 1
fi

exec "$PY" wyse_injector.py "$@"
