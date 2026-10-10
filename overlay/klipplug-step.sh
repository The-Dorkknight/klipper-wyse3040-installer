#!/bin/bash
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
# Optional extra: add the KlipPlug plugin manager to this unit. Called by
# late-command.sh inside the freshly-installed target when KLIPPLUG=1.
#
#   klipplug-step.sh <user> <home-dir> <setup-dir>
#
# KlipPlug is a separate project (https://github.com/The-Dorkknight/klipplug,
# AGPL-3.0). It brings its own installer for systems that aren't running yet
# ("image mode"), which is all this script calls: that installer writes the
# service, links it to start at boot, and runs KlipPlug once on this
# system's Python to prove it starts.
#
# This step must never spoil an otherwise-good unit. On any problem it takes
# back what it did, says why, and exits 3; late-command.sh carries on.
# Exit 0 means installed and checked.
set -uo pipefail

PRINT_USER="${1:?user}"
HOME_DIR="${2:?home dir}"
SETUP_DIR="${3:?setup dir}"
DEST="${HOME_DIR}/klipplug"
REPO_URL="${KLIPPLUG_REPO_URL:-https://github.com/The-Dorkknight/klipplug.git}"
BAKED="${SETUP_DIR}/klipplug"

say() { echo "    klipplug: $*"; }

is_klipplug() { [ -f "$1/tools/printer_install.py" ] && [ -f "$1/src/klipplug/__main__.py" ]; }

give_up() {
  say "NOT installed - $*"
  say "everything else on this unit is unaffected."
  if is_klipplug "$DEST"; then
    (cd "$DEST" && python3 tools/printer_install.py image --user "$PRINT_USER" --remove) >/dev/null 2>&1 || true
  fi
  rm -f /etc/systemd/system/multi-user.target.wants/klipplug.service /etc/systemd/system/klipplug.service
  exit 3
}

case "$HOME_DIR" in
  /home/?*) ;;
  *) say "NOT installed - unexpected home directory '$HOME_DIR'"; exit 3 ;;
esac
id -u "$PRINT_USER" >/dev/null 2>&1 || give_up "user '$PRINT_USER' doesn't exist"
command -v python3 >/dev/null 2>&1 || give_up "python3 is missing"

# 1. Get KlipPlug: a copy baked into the installer wins (no download, and
#    it's how a version that isn't on GitHub gets onto a unit); otherwise
#    clone it.
if is_klipplug "$DEST"; then
  say "already present at $DEST"
  SOURCE="an earlier run"
elif is_klipplug "$BAKED"; then
  rm -rf "$DEST"
  cp -R "$BAKED" "$DEST" || give_up "couldn't copy it from the installer"
  # files on the installer medium are read-only
  chmod -R u+rwX,go+rX "$DEST"
  SOURCE="the copy on the installer"
else
  command -v git >/dev/null 2>&1 || give_up "git is missing and there is no copy on the installer"
  ok=0
  for attempt in 1 2 3; do
    rm -rf "$DEST"
    if GIT_TERMINAL_PROMPT=0 git clone --quiet "$REPO_URL" "$DEST" 2>&1; then
      ok=1
      break
    fi
    say "download attempt $attempt/3 failed"
    [ "$attempt" -lt 3 ] && sleep $((attempt * 10))
  done
  if [ "$ok" != 1 ] || ! is_klipplug "$DEST"; then
    rm -rf "$DEST"
    give_up "couldn't download it from ${REPO_URL%.git} (no internet, or it isn't published)"
  fi
  SOURCE="${REPO_URL%.git}"
fi
chown -R "$PRINT_USER:$(id -gn "$PRINT_USER")" "$DEST" || give_up "couldn't hand $DEST to $PRINT_USER"

# 2. Lets KlipPlug show the settings pages of ported OctoPrint plugins.
#    Nice to have, never a reason to fail.
if command -v apt-get >/dev/null 2>&1 && ! python3 -c "import jinja2" >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-jinja2 >/dev/null 2>&1 \
    || say "note: python3-jinja2 couldn't be installed (plugin settings pages will be plain)"
fi

# 3. KlipPlug's own installer: writes, links, checks.
if (cd "$DEST" && python3 tools/printer_install.py image --user "$PRINT_USER"); then
  [ -f /etc/systemd/system/klipplug.service ] || give_up "its installer finished but left no service"
  say "installed from ${SOURCE}; its page will be on port 7130"
  exit 0
fi
give_up "its installer reported a problem (see the lines above)"
