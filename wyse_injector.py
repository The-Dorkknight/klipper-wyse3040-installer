#!/usr/bin/env python3
"""
Wyse 3040 Klipper Injector
===========================
WORK IN PROGRESS: not yet verified to boot real Wyse 3040 hardware
end-to-end. See "Known limitations" in README.md before you image real units.

Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
(Full text embedded below as LICENSE_TEXT, and in LICENSE.md.)

A point-and-click Linux tool that builds an unattended Debian 12 installer
ISO for turning a Dell Wyse 3040 thin client into a headless Klipper /
Moonraker / Mainsail-or-Fluidd 3D-printer controller, and writes that ISO
to a USB stick - modeled on this project's sibling tool, the Pi Hotspot
Injector (pi_injector.py), reusing its drive-safety machinery wholesale.

Where it differs from the Pi tool, and why:

  * The Wyse's storage (eMMC) is soldered in - there's no way to pull it
    out and mount it on this PC the way an SD card can be. So everything
    the Pi tool does by mounting a *finished* card and poking files onto
    it (Hotspot Setup, Card Inquisitor, the Clone tab) has to happen here
    the other way round: baked into the ISO/installer *before* a unit is
    ever imaged, not injected afterward. That's why this is a single
    "Build" tab with all the choices up front, rather than several tabs
    for different post-hoc injections.
  * The build/target machine are the SAME CPU architecture (amd64), so
    unlike the Pi tool there is no qemu-user chroot step at all here.
  * Boot diagnostics (this project's answer to Card Inquisitor) can't be
    written to a boot partition you pull out afterward either - they're
    served over HTTP instead, at http://<unit>/diagnostics.txt.

What it can do:

  1. Build a Klipper installer ISO (optionally with Crowsnest webcam
     support and the experimental KlipPlug plugin manager): renders the
     preseed + WiFi + web UI config, downloads and checksum-verifies the official Debian netinst
     ISO (resumable), patches its boot menu for a fully unattended install,
     and produces klipper-wyse3040-installer.iso.
  2. Write that ISO (or any disk image) to a USB stick. For safety it only
     offers removable drives sized within a sane window, and asks twice
     before erasing anything, then verifies what was written.

Run it from a terminal:   python3 wyse_injector.py
It asks for your password itself (via pkexec) because it needs root to
write to disks and install build dependencies. Once it's run at least once,
use its Extras tab ("Add to applications menu") to add a proper launcher
for next time.

Log file: wyse_injector.log in your home folder (passwords are never logged).
"""

import sys

if sys.version_info < (3, 6):
    sys.stderr.write("Wyse 3040 Klipper Injector needs Python 3.6 or newer.\n")
    sys.exit(1)

import atexit
import gzip
import hashlib
import json
import locale
import logging
import os
import pwd
import queue
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
import zlib

try:
    import lzma
except ImportError:
    lzma = None
try:
    import bz2
except ImportError:
    bz2 = None


def _desktop_error(title, message):
    sys.stderr.write("%s: %s\n" % (title, message))
    for cmd in (
        ["zenity", "--error", "--title", title, "--text", message],
        ["kdialog", "--title", title, "--error", message],
        ["xmessage", "-center", "%s\n\n%s" % (title, message)],
        ["notify-send", "-u", "critical", title, message],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, timeout=600)
                return
            except Exception:
                continue


try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    _desktop_error(
        "Wyse 3040 Klipper Injector",
        "Python's Tkinter GUI library isn't installed.\n\n"
        "On Ubuntu / Linux Mint / Debian run:\n"
        "    sudo apt install python3-tk\n\n"
        "then start this tool again.",
    )
    sys.exit(1)

APP_TITLE = "Wyse 3040 Klipper Injector"
APP_VERSION = "0.2.0-wip"  # WORK IN PROGRESS: not yet verified to boot real Wyse 3040 hardware end-to-end
__version__ = APP_VERSION
__build_date__ = "2026-10-07 16:02"  # set at release time, not computed at runtime - a screenshot's
                                      # title/status bar should say which build it was taken from
COPYRIGHT_HOLDER = "Matthew Armstrong"


def real_user_info():
    for var in ("PKEXEC_UID", "SUDO_UID"):
        value = os.environ.get(var)
        if value and value.isdigit():
            try:
                pw = pwd.getpwuid(int(value))
                return pw.pw_uid, pw.pw_gid, pw.pw_dir
            except KeyError:
                pass
    return os.getuid(), os.getgid(), os.path.expanduser("~")


REAL_UID, REAL_GID, REAL_HOME = real_user_info()
LOG_FILE = os.path.join(REAL_HOME, "wyse_injector.log")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# USB installer sticks: a much wider window than an SD card, since the ISO
# itself is under 1 GB but people image from whatever stick they have to
# hand. Still bounded, so an internal disk can never be offered.
WRITE_TARGET_LABEL = "1-64 GB"
MIN_WRITE_TARGET_BYTES = 900 * 1000 ** 2   # ~0.9 GB
MAX_WRITE_TARGET_BYTES = 65 * 1000 ** 3    # 64 GB + a bit of leeway
WRITE_CHUNK = 4 * 1024 * 1024

REQUIRED_TOOLS = ("lsblk", "findmnt", "mount", "umount", "sync", "mountpoint")
BUILD_DEPS = ("xorriso", "p7zip-full", "wget", "rsync")  # isolinux checked separately (no single binary name)

ISO_DIR_URL = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"

FONT_HEADER = ("Sans", 16, "bold")
FONT_LABEL = ("Sans", 11)
FONT_SMALL = ("Sans", 9)
FONT_BTN = ("Sans", 11, "bold")

log = logging.getLogger("wyse_injector")
ACTIVE_SESSIONS = []
ACTIVE_CHILDREN = []
CHILDREN_LOCK = threading.Lock()
CANCEL = threading.Event()
CURRENT_WORKER = [None]


# --------------------------------------------------------------------------- #
# Small helpers (ported from this project's sibling tool, pi_injector.py -
# generic disk/network/GUI plumbing, nothing Pi-specific in any of this)
# --------------------------------------------------------------------------- #
class InjectError(Exception):
    """A problem we can explain to the user in plain words."""


class CancelledError(InjectError):
    pass


class DownloadPaused(InjectError):
    pass


def human_size(num_bytes):
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1000.0


def default_country_code():
    candidates = [os.environ.get(v, "") for v in ("LC_ALL", "LC_CTYPE", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except (ValueError, TypeError):
        pass
    for value in candidates:
        m = re.match(r"^[a-z]{2,3}_([A-Z]{2})\b", value or "")
        if m:
            return m.group(1)
    return ""


def load_country_codes():
    path = "/usr/share/zoneinfo/iso3166.tab"
    codes = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                codes.add(line.split("\t", 1)[0].strip().upper())
    except OSError:
        return None
    return codes or None


def rand_alnum(n=16):
    """Ambiguous characters (0/O, 1/l/I) are left out so a printed password
    is easy to type correctly on a phone."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def truthy(value):
    return value in (True, 1, "1", "true", "True")


def run(cmd, input_text=None, check=True, timeout=90, quiet=False):
    if not quiet:
        log.info("run: %s", " ".join(cmd))
    try:
        res = subprocess.run(
            cmd, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("%s did not finish within %ds." % (cmd[0], timeout))
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise RuntimeError("%s failed: %s" % (cmd[0], detail))
    return res


def run_tracked(cmd, timeout, env=None, cwd=None):
    log.info("run: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
        start_new_session=True, env=env, cwd=cwd,
    )
    with CHILDREN_LOCK:
        ACTIVE_CHILDREN.append(proc)
    try:
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_process_group(proc)
            proc.communicate()
            raise
    finally:
        with CHILDREN_LOCK:
            if proc in ACTIVE_CHILDREN:
                ACTIVE_CHILDREN.remove(proc)
    return proc.returncode, out or ""


def kill_process_group(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def kill_active_children():
    with CHILDREN_LOCK:
        procs = list(ACTIVE_CHILDREN)
    for proc in procs:
        kill_process_group(proc)


def stop_background_work(timeout=30):
    CANCEL.set()
    kill_active_children()
    worker = CURRENT_WORKER[0]
    if worker is not None and worker.is_alive() and worker is not threading.current_thread():
        worker.join(timeout)


def check_internet():
    import socket

    hosts = ("deb.debian.org", "cdimage.debian.org", "github.com")
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(8)
    try:
        for host in hosts:
            try:
                socket.getaddrinfo(host, 443)
                return
            except OSError:
                continue
    finally:
        socket.setdefaulttimeout(old_timeout)
    raise InjectError(
        "This PC doesn't seem to be online (couldn't look up %s).\n\n"
        "Downloading the Debian ISO and packages both need this PC's own "
        "internet connection. Connect it and try again." % hosts[0]
    )


def explain_apt_failure(output):
    text = output or ""
    if "Could not get lock" in text or "Unable to acquire the dpkg frontend lock" in text:
        return ("\n\nIn plain English: another program (probably this PC's automatic "
                "updates) is using apt right now. Wait a few minutes and try again.")
    if "Temporary failure resolving" in text or "Could not resolve" in text:
        return "\n\nIn plain English: no internet connection (DNS lookup failed)."
    if "No space left on device" in text:
        return "\n\nIn plain English: this PC ran out of disk space."
    return ""


@atexit.register
def global_cleanup_safety_net():
    for session in list(ACTIVE_SESSIONS):
        try:
            session.cleanup()
        except Exception:
            pass


def write_file(path, data, mode=0o600, posix=True):
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", newline="\n") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if posix:
            os.chown(tmp, 0, 0)
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _fmt_eta(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return ""
    if seconds < 60:
        return "under a minute left"
    if seconds < 3600:
        return "about %d min left" % round(seconds / 60.0)
    return "about %dh %02dm left" % (seconds // 3600, (seconds % 3600) // 60)


def resumable_download(url, dest, say, progress, cancel_event, expected_sha256=None, expected_name=None,
                       label=None, checkpoint=10 * 1024 * 1024, backoff=(5, 10, 20, 30, 45, 60, 60, 60)):
    """Download url to dest, surviving dropouts and resuming from a
    checkpoint. Ported from pi_injector.py - generic, nothing Pi-specific."""
    import http.client
    import socket
    import urllib.error
    import urllib.request

    label = label or os.path.basename(dest)
    part, state_path = dest + ".part", dest + ".part.json"
    state = {}
    try:
        with open(state_path) as f:
            state = json.load(f)
        if state.get("url") != url:
            state = {}
    except (OSError, ValueError):
        state = {}
    good = int(state.get("checkpoint", 0)) if os.path.isfile(part) else 0
    good = min(good, os.path.getsize(part)) if os.path.isfile(part) else 0

    def save_state():
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_path)

    def rollback(to):
        with open(part, "ab") as f:
            f.truncate(to)
            f.flush()
            os.fsync(f.fileno())

    def restart_from_zero(why):
        log.info("download %s: starting again from zero (%s)", label, why)
        state.update({"checkpoint": 0, "validator": None, "total": None})
        rollback(0)
        return 0

    if not os.path.isfile(part):
        open(part, "wb").close()
        state = {"url": url, "checkpoint": 0}
    rollback(good)
    state.update({"url": url, "checkpoint": good})
    if good:
        say("Resuming the download of %s from %s..." % (label, human_size(good)))

    failures, last_error, start_time, start_bytes = 0, "", time.time(), good
    while True:
        if cancel_event.is_set():
            raise DownloadPaused("Download paused at %s - press Download again to carry on." % human_size(good))
        headers = {"User-Agent": "WyseInjector/%s" % APP_VERSION}
        pos = good
        if good:
            headers["Range"] = "bytes=%d-" % good
            if state.get("validator"):
                headers["If-Range"] = state["validator"]
        try:
            req = urllib.request.Request(state.get("resolved") or url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                final_name = os.path.basename(resp.geturl().split("?", 1)[0])
                if expected_name and final_name != expected_name:
                    raise InjectError("The server seems to have just published a new file (%s) - press "
                                      "Download again." % final_name)
                status = resp.getcode()
                validator = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
                if good and status == 206:
                    m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", resp.headers.get("Content-Range", ""))
                    if not m or int(m.group(1)) != good:
                        raise InjectError("The server resumed from the wrong place - please try again.")
                    total = int(m.group(3)) if m.group(3) != "*" else None
                else:
                    if good:
                        good = pos = restart_from_zero("server sent the whole file (HTTP %s)" % status)
                    length = resp.headers.get("Content-Length")
                    total = int(length) if length and length.isdigit() else None
                if state.get("total") and total and state["total"] != total:
                    good = pos = restart_from_zero("file size on the server changed")
                    continue
                state.update({"resolved": resp.geturl(), "validator": validator, "total": total})
                save_state()
                if total and good == 0 and shutil.disk_usage(os.path.dirname(dest) or ".").free < total + 200 * 1024 * 1024:
                    raise InjectError("Not enough free space for %s (%s needed)." % (label, human_size(total)))

                last_report = 0.0
                with open(part, "r+b") as out:
                    out.seek(pos)
                    while True:
                        if cancel_event.is_set():
                            out.flush()
                            os.fsync(out.fileno())
                            out.truncate(pos)
                            good = pos
                            state["checkpoint"] = good
                            save_state()
                            raise DownloadPaused("Download paused at %s." % human_size(good))
                        block = resp.read(256 * 1024)
                        if not block:
                            break
                        out.write(block)
                        pos += len(block)
                        if pos - good >= checkpoint:
                            out.flush()
                            os.fsync(out.fileno())
                            good = pos
                            state["checkpoint"] = good
                            save_state()
                            failures = 0
                        now = time.time()
                        if now - last_report >= 0.4:
                            last_report = now
                            elapsed = max(now - start_time, 0.001)
                            speed = (pos - start_bytes) / elapsed
                            frac = pos / float(total) if total else None
                            eta = _fmt_eta((total - pos) / speed) if total and speed > 0 and pos > start_bytes else ""
                            progress(frac, ("Downloading %s: %s%s - %s/s %s" % (
                                label, human_size(pos), (" of %s" % human_size(total)) if total else "",
                                human_size(speed), ("- " + eta) if eta else "")).strip())
                    out.flush()
                    os.fsync(out.fileno())
                if total and pos != total:
                    raise http.client.IncompleteRead(b"", total - pos)
                good = pos
                state["checkpoint"] = good
                save_state()
                break
        except (DownloadPaused, CancelledError):
            raise
        except InjectError:
            raise
        except (urllib.error.URLError, socket.timeout, http.client.HTTPException, ConnectionError, OSError) as exc:
            last_error = "%s: %s" % (type(exc).__name__, getattr(exc, "reason", exc))
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416 and good:
                good = restart_from_zero("server refused the resume point")
                continue
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                raise InjectError("The download address doesn't work any more (HTTP %d): %s"
                                  % (exc.code, state.get("resolved") or url))
            if pos > good:
                with open(part, "r+b") as f:
                    f.truncate(pos)
                    f.flush()
                    os.fsync(f.fileno())
                good = pos
                state["checkpoint"] = good
                save_state()
                failures = 0
            else:
                rollback(good)
            failures += 1
            if failures > len(backoff):
                raise DownloadPaused("The internet connection keeps dropping. Paused at %s - check the "
                                     "connection and press Download again.\n\nLast error: %s"
                                     % (human_size(good), last_error))
            wait = backoff[failures - 1]
            for left in range(wait, 0, -1):
                if cancel_event.is_set():
                    break
                progress(None, "Connection dropped - saved at %s. Resuming in %d s..." % (human_size(good), left))
                time.sleep(1)

    if expected_sha256:
        progress(None, "Checking %s against its published checksum..." % label)
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != expected_sha256.lower():
            for p in (part, state_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
            raise InjectError("The downloaded file is damaged (checksum mismatch) - press Download again.")
    os.replace(part, dest)
    try:
        os.remove(state_path)
    except OSError:
        pass
    if os.geteuid() == 0 and REAL_UID != 0:
        try:
            os.chown(dest, REAL_UID, REAL_GID)
        except OSError:
            pass
    log.info("downloaded %s (%d bytes)", dest, os.path.getsize(dest))
    return dest


# --------------------------------------------------------------------------- #
# Disk safety (ported near-verbatim from pi_injector.py: this is the part
# that must not be re-derived casually - it's what stops the wrong drive
# ever being offered or erased)
# --------------------------------------------------------------------------- #
def missing_required_tools():
    return [t for t in REQUIRED_TOOLS if shutil.which(t) is None]


SYSTEM_MOUNT_ROOTS = (
    "/home", "/usr", "/var", "/etc", "/opt", "/root", "/srv", "/boot", "/snap",
    "/cdrom", "/run/live", "/run/initramfs", "/lib/live",
)


def is_critical_mount(mountpoint):
    if mountpoint in ("/", "[SWAP]"):
        return True
    return any(mountpoint == root or mountpoint.startswith(root + "/") for root in SYSTEM_MOUNT_ROOTS)


def node_mountpoints(node):
    mps = node.get("mountpoints")
    if mps is None:
        mps = [node.get("mountpoint")]
    return [m for m in mps if m]


def walk(node):
    yield node
    for child in node.get("children") or []:
        for sub in walk(child):
            yield sub


def lsblk_devices(quiet=False):
    attempts = [
        "NAME,TYPE,FSTYPE,LABEL,MOUNTPOINTS,RM,HOTPLUG,TRAN,SIZE,MODEL,RO,SERIAL,VENDOR",
        "NAME,TYPE,FSTYPE,LABEL,MOUNTPOINT,RM,HOTPLUG,TRAN,SIZE,MODEL,RO,SERIAL,VENDOR",
        "NAME,TYPE,FSTYPE,LABEL,MOUNTPOINT,RM,TRAN,SIZE,MODEL,RO",
    ]
    last_error = None
    for cols in attempts:
        try:
            out = run(["lsblk", "-J", "-p", "-b", "-o", cols], quiet=quiet).stdout
            return json.loads(out).get("blockdevices", [])
        except (RuntimeError, ValueError) as exc:
            last_error = exc
    raise InjectError("Could not read block devices with lsblk: %s" % last_error)


def is_emmc(disk):
    """This PC's own built-in storage (a laptop) can show up as
    /dev/mmcblkN too - never offer that as a write target."""
    base = os.path.basename(disk)
    try:
        with open("/sys/block/%s/device/type" % base) as f:
            return f.read().strip().upper() == "MMC"
    except OSError:
        return False


def is_external_disk(dev):
    name = dev.get("name", "")
    base = os.path.basename(name)
    tran = str(dev.get("tran") or "").lower()
    if base.startswith("mmcblk"):
        return not is_emmc(name)
    if tran in ("usb", "mmc"):
        return True
    return truthy(dev.get("rm")) and tran in ("", "none")


def device_model(dev):
    vendor = (dev.get("vendor") or "").strip()
    model = (dev.get("model") or "").strip()
    text = ("%s %s" % (vendor, model)).strip() if vendor and vendor not in model else model
    if not text and "mmcblk" in (dev.get("name") or ""):
        return "SD/MMC card"
    return text or "Unknown device"


def device_size_bytes(dev):
    try:
        return int(dev.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def classify_write_targets(quiet=False):
    eligible, rejected = [], []
    for dev in lsblk_devices(quiet=quiet):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        base = os.path.basename(name)
        if base.startswith(("loop", "zram", "ram", "sr", "fd", "dm-", "md", "nbd")):
            continue
        if re.match(r"^mmcblk\d+(boot\d+|rpmb)$", base):
            continue

        size = device_size_bytes(dev)
        desc = "%s, %s (%s)" % (device_model(dev), human_size(size), name)
        is_mmc = base.startswith("mmcblk")

        if is_mmc and is_emmc(name):
            rejected.append((desc, "built-in eMMC storage - part of this computer"))
            continue
        if not is_external_disk(dev):
            rejected.append((desc, "internal drive (not a USB stick or SD card)"))
            continue

        in_use = [mp for node in walk(dev) for mp in node_mountpoints(node) if is_critical_mount(mp)]
        if in_use:
            rejected.append((desc, "in use by this computer (%s)" % ", ".join(in_use)))
            continue
        if size <= 0:
            rejected.append((desc, "no drive inserted in this reader"))
            continue
        if size > MAX_WRITE_TARGET_BYTES:
            rejected.append((desc, "bigger than the %s safety limit" % WRITE_TARGET_LABEL))
            continue
        if size < MIN_WRITE_TARGET_BYTES:
            rejected.append((desc, "smaller than the %s safety limit" % WRITE_TARGET_LABEL))
            continue
        if truthy(dev.get("ro")):
            rejected.append((desc, "write-protected (check the SD card's LOCK switch)"))
            continue

        partitions = [c for c in walk(dev) if c is not dev]
        contents = []
        for p in partitions:
            if p.get("type") != "part":
                continue
            label = (p.get("label") or "").strip()
            bits = [label or "(no name)", p.get("fstype") or "unknown format", human_size(p.get("size"))]
            contents.append("%s: %s" % (os.path.basename(p.get("name", "")), ", ".join(bits)))

        eligible.append({
            "disk": name, "size": size, "serial": (dev.get("serial") or "").strip(),
            "model": device_model(dev), "desc": desc,
            "partitions": [p.get("name") for p in partitions if p.get("name")],
            "contents": contents,
            "mounted": sorted({mp for node in walk(dev) for mp in node_mountpoints(node)}),
        })
    return eligible, rejected


def unmount_existing(dev):
    last_error = None
    for _ in range(6):
        res = run(["findmnt", "-rn", "-S", dev, "-o", "TARGET"], check=False)
        targets = [t.strip() for t in res.stdout.splitlines() if t.strip()]
        if not targets:
            return
        for target in targets:
            if is_critical_mount(target):
                raise InjectError("%s is mounted at %s, which looks like part of this computer. Refusing "
                                  "to continue." % (dev, target))
        try:
            run(["umount", dev])
        except RuntimeError as exc:
            last_error = exc
            time.sleep(1)
            continue
    raise InjectError("Could not fully unmount %s after several attempts%s."
                      % (dev, (" (last error: %s)" % last_error) if last_error else ""))


def ensure_writable_drive(target):
    if truthy(target.get("ro")):
        raise InjectError("%s is write-protected. If it's an SD card, check the LOCK switch." % target["desc"])


def disks_holding_path(path):
    res = run(["findmnt", "-n", "-o", "SOURCE", "--target", path], check=False)
    source = re.sub(r"\[.*\]$", "", res.stdout.strip())
    if not source.startswith("/dev/"):
        return set()
    res = run(["lsblk", "-s", "-n", "-p", "-r", "-o", "NAME,TYPE", source], check=False)
    disks = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "disk":
            disks.add(parts[0])
    return disks


IMAGE_FILE_TYPES = [
    ("Disk images", "*.iso *.img *.img.xz *.img.gz *.img.bz2 *.img.zst *.zip *.xz *.gz *.bz2 *.zst *.ISO"),
    ("All files", "*"),
]


def sniff_image(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if head[:2] == b"\x1f\x8b":
        return "gz"
    if head[:3] == b"BZh":
        return "bz2"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zst"
    return "raw"


_LZMA_ERROR = lzma.LZMAError if lzma is not None else EOFError


class ImageSource:
    """A readable stream of the *decompressed* image, plus whatever we can
    learn about its final size. Ported from pi_injector.py."""

    def __init__(self, path):
        self.path = path
        self.kind = sniff_image(path)
        self.file_size = os.path.getsize(path)
        self.size = None
        self.inner_name = None
        self._raw = None
        self._zip = None
        self._proc = None
        self.stream = None

    def open(self):
        k = self.kind
        if k == "raw":
            self._raw = open(self.path, "rb")
            self.stream = self._raw
            self.size = self.file_size
        elif k in ("xz", "gz", "bz2"):
            module = {"xz": lzma, "gz": gzip, "bz2": bz2}[k]
            if module is None:
                raise InjectError("This Python can't read .%s files (its module is missing)." % k)
            self._raw = open(self.path, "rb")
            self.stream = module.open(self._raw, "rb")
        elif k == "zip":
            try:
                self._zip = zipfile.ZipFile(self.path)
            except zipfile.BadZipFile as exc:
                raise InjectError("The .zip file is damaged (%s)." % exc)
            files = [i for i in self._zip.infolist() if not i.filename.endswith("/")]
            images = [i for i in files if i.filename.lower().endswith((".img", ".iso", ".raw", ".bin"))]
            if not images:
                raise InjectError("No disk image found inside that .zip.")
            pick = max(images, key=lambda i: i.file_size)
            self.inner_name = pick.filename
            self.size = pick.file_size
            self.stream = self._zip.open(pick)
        elif k == "zst":
            if shutil.which("zstd") is None:
                raise InjectError("This image is .zst and 'zstd' isn't installed (sudo apt install zstd).")
            self._raw = open(self.path, "rb")
            self._proc = subprocess.Popen(["zstd", "-dcq"], stdin=self._raw, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL)
            self.stream = self._proc.stdout
        return self

    def read(self, n):
        try:
            return self.stream.read(n)
        except (EOFError, OSError, zipfile.BadZipFile, zlib.error, _LZMA_ERROR) as exc:
            raise InjectError("The image file is damaged or incomplete (%s). Re-download and retry." % exc)

    def check_complete(self):
        if self._proc is not None:
            rc = self._proc.wait(timeout=60)
            if rc != 0:
                raise InjectError("The image file is incomplete (zstd exit %d). Re-download and retry." % rc)

    def fraction_consumed(self):
        if self._raw is None or self.file_size <= 0:
            return None
        try:
            pos = os.lseek(self._raw.fileno(), 0, os.SEEK_CUR)
        except OSError:
            return None
        return min(1.0, pos / float(self.file_size))

    def close(self):
        for thing in (self.stream, self._zip, self._raw):
            try:
                if thing is not None:
                    thing.close()
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass


def peek_image(path):
    src = ImageSource(path)
    try:
        src.open()
        first = src.read(512)
        size = src.size
        kind = src.kind
    finally:
        src.close()
    if not first:
        raise InjectError("The image file is empty.")
    looks_ok = len(first) == 512 and first[510:512] == b"\x55\xaa"
    what = {"raw": "uncompressed image", "xz": "xz-compressed image", "gz": "gzip-compressed image",
            "bz2": "bzip2-compressed image", "zip": "zip archive", "zst": "zstd-compressed image"}.get(kind, kind)
    return looks_ok, size, what


def verify_written(fd, disk, length, expected_digest, progress, cancel_event):
    os.fsync(fd)
    try:
        run(["blockdev", "--flushbufs", disk], check=False, timeout=60)
    except RuntimeError:
        pass
    if hasattr(os, "posix_fadvise"):
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    done = 0
    start = time.time()
    last_report = 0.0
    while done < length:
        if cancel_event.is_set():
            raise CancelledError("Verification cancelled. The image WAS fully written, just not checked.")
        data = os.read(fd, min(WRITE_CHUNK, length - done))
        if not data:
            break
        hasher.update(data)
        done += len(data)
        now = time.time()
        if now - last_report >= 0.4:
            last_report = now
            elapsed = max(now - start, 0.001)
            frac = done / float(length)
            eta = _fmt_eta(elapsed / frac - elapsed) if frac > 0.02 else ""
            progress(frac, ("Verifying: %s of %s - %s/s %s" % (
                human_size(done), human_size(length), human_size(done / elapsed),
                ("- " + eta) if eta else "")).strip())
    if done != length or hasher.hexdigest() != expected_digest:
        raise InjectError(
            "VERIFICATION FAILED: what's on the drive doesn't match the image.\n\nThe stick/card is "
            "probably faulty or counterfeit, or the reader/cable is flaky. Try a different one - and "
            "don't use this stick to image a Wyse until it passes."
        )
    return True


def write_image(image_path, target, verify, say, progress, cancel_event):
    disk = target["disk"]
    say("Re-checking the selected drive...")
    current = {t["disk"]: t for t in classify_write_targets()[0]}.get(disk)
    if (current is None or current["size"] != target["size"] or current["serial"] != target["serial"]
            or current["contents"] != target["contents"]):
        raise InjectError("The selected drive (%s) changed or disappeared since you chose it. NOTHING was "
                          "written.\n\nPress Refresh and select it again." % target["desc"])
    if disk in disks_holding_path(image_path):
        raise InjectError("The image file is stored ON the drive you're about to overwrite. NOTHING was "
                          "written.\n\nCopy the image to this computer first.")

    src = ImageSource(image_path)
    fd = None
    try:
        src.open()
        if src.size is not None and src.size > target["size"]:
            raise InjectError("This image needs %s but %s only holds %s. NOTHING was written."
                              % (human_size(src.size), target["desc"], human_size(target["size"])))

        say("Unmounting the drive's partitions...")
        for part in current["partitions"]:
            unmount_existing(part)
        run(["sync"], check=False)

        try:
            fd = os.open(disk, os.O_RDWR | os.O_EXCL | getattr(os, "O_CLOEXEC", 0))
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (ImportError, OSError):
                pass
        except OSError as exc:
            raise InjectError("Couldn't open %s for writing: %s. NOTHING was written."
                              % (target["desc"], exc.strerror or exc))

        hasher = hashlib.sha256()
        written = 0
        last_sync = 0
        last_report = 0.0
        start = time.time()
        total_text = human_size(src.size) if src.size else None
        progress(0.0, "Writing...")

        while True:
            if cancel_event.is_set():
                raise CancelledError("Writing was cancelled. The drive now holds a PARTIAL image and won't "
                                     "boot - write it again before using it.")
            chunk = src.read(WRITE_CHUNK)
            if not chunk:
                break
            if written + len(chunk) > target["size"]:
                raise InjectError("The image turned out bigger than %s. Use a bigger drive." % target["desc"])
            view = memoryview(chunk)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            hasher.update(chunk)
            written += len(chunk)
            if written - last_sync >= 64 * 1024 * 1024:
                os.fsync(fd)
                last_sync = written
            now = time.time()
            if now - last_report >= 0.4:
                last_report = now
                elapsed = max(now - start, 0.001)
                speed = written / elapsed
                frac = (written / float(src.size)) if src.size else src.fraction_consumed()
                eta = _fmt_eta(elapsed / frac - elapsed) if frac and frac > 0.02 else ""
                progress(frac, ("Writing: %s%s - %s/s %s" % (
                    human_size(written), (" of %s" % total_text) if total_text else "",
                    human_size(speed), ("- " + eta) if eta else "")).strip())

        if written == 0:
            raise InjectError("The image file contained no data. NOTHING useful was written.")
        src.check_complete()

        progress(None, "Finishing - flushing the last data to the drive...")
        os.fsync(fd)

        tail = 1024 * 1024
        if target["size"] - written >= 2 * tail:
            os.lseek(fd, target["size"] - tail, os.SEEK_SET)
            view = memoryview(b"\0" * tail)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)

        digest = hasher.hexdigest()
        verified = False
        if verify:
            verified = verify_written(fd, disk, written, digest, progress, cancel_event)
        os.close(fd)
        fd = None

        say("Asking the system to re-read the new partitions...")
        try:
            run(["blockdev", "--rereadpt", disk], check=False, timeout=30)
            if shutil.which("udevadm"):
                run(["udevadm", "settle", "--timeout=15"], check=False, timeout=30)
        except RuntimeError:
            pass

        elapsed = time.time() - start
        log.info("wrote %d bytes to %s in %.0fs (verified=%s)", written, disk, elapsed, verified)
        return {"written": written, "seconds": elapsed, "verified": verified}
    finally:
        if fd is not None:
            try:
                os.fsync(fd)
            except OSError:
                pass
            os.close(fd)
        src.close()


# --------------------------------------------------------------------------- #
# The Klipper ISO build pipeline (this project's own logic - a Python port
# of setup.sh, with the gaps the CLI version had now closed: a validated
# country code instead of a hard-coded one, checksum-verified + resumable
# download, and choices - WiFi mode, web UI, Crowsnest - exposed in the GUI
# instead of only via environment variables)
# --------------------------------------------------------------------------- #
def ensure_build_deps(say):
    need = [p for p in BUILD_DEPS if shutil.which({"p7zip-full": "7z", "xorriso": "xorriso",
                                                     "wget": "wget", "rsync": "rsync"}[p]) is None]
    have_isolinux = os.path.isfile("/usr/lib/ISOLINUX/isohdpfx.bin") or \
        os.path.isfile("/usr/lib/syslinux/isohdpfx.bin")
    if not have_isolinux:
        need.append("isolinux")
    if not need:
        return
    if shutil.which("apt-get") is None:
        raise InjectError("This machine doesn't use apt, so build dependencies can't be auto-installed. "
                          "Install these yourself: %s" % ", ".join(need))
    say("Installing build tools on this machine (%s)..." % ", ".join(need))
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    subprocess.run(["apt-get", "update"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    res = subprocess.run(["apt-get", "install", "-y"] + need, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, universal_newlines=True, env=env)
    if res.returncode != 0:
        raise InjectError("Couldn't install build tools:\n\n%s%s" % (res.stdout[-1500:],
                          explain_apt_failure(res.stdout)))


def fetch_netinst_iso(work_dir, say, progress, cancel_event):
    """Find, download and checksum-verify the current Debian amd64 netinst
    ISO. Re-uses an already-downloaded, already-verified copy."""
    import urllib.request

    say("Asking cdimage.debian.org for the latest Debian netinst ISO...")
    try:
        with urllib.request.urlopen(ISO_DIR_URL, timeout=30) as resp:
            listing = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        raise InjectError("Couldn't reach cdimage.debian.org (%s). Check this PC's internet connection." % exc)
    names = sorted(set(re.findall(r"debian-[0-9.]+-amd64-netinst\.iso", listing)))
    if not names:
        raise InjectError("Couldn't find a netinst ISO name on cdimage.debian.org's listing page - the page "
                          "layout may have changed. Browse %s yourself and check." % ISO_DIR_URL)
    name = names[-1]  # sorted names -> the highest point release sorts last for same-width version numbers

    say("Fetching the published checksum for %s..." % name)
    try:
        with urllib.request.urlopen(ISO_DIR_URL + "SHA256SUMS", timeout=30) as resp:
            sums_text = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        raise InjectError("Couldn't fetch SHA256SUMS from cdimage.debian.org (%s)." % exc)
    expected = None
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            expected = parts[0].lower()
            break
    if not expected or not re.match(r"^[0-9a-f]{64}$", expected):
        raise InjectError("Couldn't find %s's checksum in the published SHA256SUMS file." % name)

    dest = os.path.join(work_dir, name)

    def sha256_of(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
                if cancel_event.is_set():
                    raise CancelledError("Cancelled.")
                h.update(block)
        return h.hexdigest()

    if os.path.isfile(dest):
        say("Checking the copy already downloaded...")
        if sha256_of(dest) == expected:
            log.info("netinst ISO already downloaded and verified: %s", dest)
            return dest
        log.info("existing %s failed checksum - re-downloading", dest)

    return resumable_download(ISO_DIR_URL + name, dest, say, progress, cancel_event,
                              expected_sha256=expected, expected_name=name, label=name)


def render_template(src_path, dest_path, mapping):
    with open(src_path, encoding="utf-8") as f:
        text = f.read()
    for key, value in mapping.items():
        text = text.replace("__%s__" % key, value)
    remaining = re.findall(r"__[A-Z0-9_]+__", text)
    if remaining:
        raise InjectError("Internal error: %s still has unfilled placeholders: %s"
                          % (os.path.basename(src_path), ", ".join(sorted(set(remaining)))))
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    write_file(dest_path, text, mode=0o644, posix=False)


def patch_boot_menu(extract_dir):
    """Point the installer's boot menu (both BIOS/isolinux and UEFI/GRUB
    paths, since either might be what the Wyse actually uses) at the
    preseed file and make it boot automatically with no prompts."""
    txt_cfg = os.path.join(extract_dir, "isolinux", "txt.cfg")
    isolinux_cfg = os.path.join(extract_dir, "isolinux", "isolinux.cfg")
    grub_cfg = os.path.join(extract_dir, "boot", "grub", "grub.cfg")

    def sub_file(path, pattern, repl, flags=0):
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new_text, n = re.subn(pattern, repl, text, flags=flags)
        if n:
            write_file(path, new_text, mode=0o644, posix=False)
        return n > 0

    sub_file(txt_cfg, r"^(\s*append\s+.*)$",
             r"\1 auto=true priority=critical preseed/file=/cdrom/preseed.cfg", flags=re.M)
    sub_file(isolinux_cfg, r"^timeout .*$", "timeout 10", flags=re.M)
    sub_file(isolinux_cfg, r"^default .*$", "default auto", flags=re.M)
    sub_file(grub_cfg, r"^(\s*linux\s+.*vmlinuz.*)$",
             r"\1 auto=true priority=critical preseed/file=/cdrom/preseed.cfg", flags=re.M)
    sub_file(grub_cfg, r"^set timeout=.*$", "set timeout=10", flags=re.M)

    ok = True
    for path, needle in ((txt_cfg, "preseed/file"), (grub_cfg, "preseed/file")):
        try:
            with open(path, encoding="utf-8") as f:
                if needle not in f.read():
                    ok = False
        except OSError:
            ok = False
    return ok


# --------------------------------------------------------------------------- #
# Optional KlipPlug (plugin manager; a separate project under AGPL-3.0)
# --------------------------------------------------------------------------- #
KLIPPLUG_URL = "https://github.com/The-Dorkknight/klipplug"
KLIPPLUG_PORT = 7130


def _is_klipplug_folder(path):
    return (os.path.isfile(os.path.join(path, "tools", "printer_install.py"))
            and os.path.isfile(os.path.join(path, "src", "klipplug", "__main__.py")))


def find_local_klipplug(folder=None):
    """A copy of KlipPlug placed next to this tool: ("folder", path) or
    ("zip", path), newest first, or None. A local copy gets baked into the
    ISO, so units need no download for it (and a version that isn't on
    GitHub can be used)."""
    folder = folder or PROJECT_DIR
    found = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in names:
        if "klipplug" not in name.lower():
            continue
        full = os.path.join(folder, name)
        try:
            if os.path.isdir(full) and _is_klipplug_folder(full):
                found.append((os.path.getmtime(full), "folder", full))
            elif os.path.isfile(full) and name.lower().endswith(".zip") and zipfile.is_zipfile(full):
                found.append((os.path.getmtime(full), "zip", full))
        except OSError:
            continue
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1], found[0][2]


def _skip_in_klipplug_copy(parts):
    """Leave out caches, and .git: a clone's settings can hold a login token,
    and the ISO may be passed around."""
    return any(part in (".git", "__pycache__", ".venv", "venv") for part in parts)


def _extract_klipplug_zip(zip_path, dest):
    """Unpack a KlipPlug zip into dest. Refuses anything that would land
    outside dest, and links. The zip may hold the project in one top folder."""
    staging = dest + ".unpack"
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    try:
        base = os.path.realpath(staging)
        with zipfile.ZipFile(zip_path) as zf:
            total = sum(info.file_size for info in zf.infolist())
            if total > 300 * 1024 * 1024:
                raise InjectError("%s unpacks to %s - that isn't a KlipPlug package."
                                  % (os.path.basename(zip_path), human_size(total)))
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                target = os.path.realpath(os.path.join(staging, name))
                if name.startswith("/") or not (target + os.sep).startswith(base + os.sep):
                    raise InjectError("%s contains a file path that points outside the package (%s) - "
                                      "not using it." % (os.path.basename(zip_path), info.filename))
                mode = (info.external_attr >> 16) & 0o177777
                if (mode & 0o170000) == 0o120000:
                    continue  # never recreate links from a zip
                if _skip_in_klipplug_copy(name.split("/")):
                    continue
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)
        root = None
        if _is_klipplug_folder(staging):
            root = staging
        else:
            for name in sorted(os.listdir(staging)):
                if _is_klipplug_folder(os.path.join(staging, name)):
                    root = os.path.join(staging, name)
                    break
        if root is None:
            raise InjectError("%s isn't a KlipPlug package (no tools/printer_install.py inside)."
                              % os.path.basename(zip_path))
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.move(root, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def stage_klipplug(overlay_dir, want, say, local_folder=None):
    """Put (or clear) the copy of KlipPlug that rides on the ISO, at
    <overlay>/klipplug. Returns "" when the option is off, otherwise a
    short text saying where units will get KlipPlug from. A problem with a
    local copy is an error here, at build time, where it can be fixed -
    not something to find out on the fifth unit."""
    dest = os.path.join(overlay_dir, "klipplug")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    if not want:
        return ""
    local = find_local_klipplug(local_folder)
    if not local:
        return "GitHub, downloaded by each unit while it installs"
    kind, path = local
    say("Adding KlipPlug from %s to the installer..." % os.path.basename(path))
    os.makedirs(overlay_dir, exist_ok=True)
    if kind == "zip":
        _extract_klipplug_zip(path, dest)
    else:
        shutil.copytree(path, dest, symlinks=False,
                        ignore=lambda _dir, names: [n for n in names if _skip_in_klipplug_copy([n])
                                                    or n.endswith(".pyc")])
    return "the copy next to this tool (%s), baked into the ISO" % os.path.basename(path)


def build_klipper_iso(opts, say, progress, cancel_event):
    """opts: print_password, wifi_mode ('ap'/'station'), wifi_passphrase,
    station_ssid, station_password, country, webui ('mainsail'/'fluidd'),
    crowsnest (bool), klipplug (bool, optional), hostname_prefix, out_dir.
    Returns a result dict."""
    say("Checking this PC's internet connection...")
    check_internet()
    ensure_build_deps(say)

    work_dir = os.path.join(PROJECT_DIR, ".build")
    extract_dir = os.path.join(work_dir, "extracted")
    os.makedirs(work_dir, exist_ok=True)
    out_iso = os.path.join(opts["out_dir"], "klipper-wyse3040-installer.iso")
    creds_path = os.path.join(opts["out_dir"], "credentials.txt")

    print_password = opts.get("print_password") or rand_alnum(16)
    wifi_passphrase = opts.get("wifi_passphrase") or (rand_alnum(16) if opts["wifi_mode"] == "ap" else "")

    say("Rendering preseed.cfg and network config...")
    render_template(os.path.join(PROJECT_DIR, "preseed.cfg.tmpl"), os.path.join(work_dir, "preseed.cfg"),
                    {"PRINT_PASSWORD": print_password})
    render_template(os.path.join(PROJECT_DIR, "overlay", "options.env.tmpl"),
                    os.path.join(work_dir, "overlay", "options.env"), {
                        "WEBUI": opts["webui"], "CROWSNEST": "1" if opts["crowsnest"] else "0",
                        "KLIPPLUG": "1" if opts.get("klipplug") else "0",
                        "WIFI_MODE": opts["wifi_mode"],
                        "STATION_SSID": opts.get("station_ssid", ""),
                        "STATION_PASSWORD": opts.get("station_password", ""),
                        "COUNTRY": opts["country"], "HOSTNAME_PREFIX": opts["hostname_prefix"],
                    })
    if opts["wifi_mode"] == "ap":
        render_template(os.path.join(PROJECT_DIR, "overlay", "hostapd", "hostapd.conf.tmpl"),
                        os.path.join(work_dir, "overlay", "hostapd", "hostapd.conf"),
                        {"WIFI_PASSPHRASE": wifi_passphrase, "COUNTRY": opts["country"]})
    else:
        render_template(os.path.join(PROJECT_DIR, "overlay", "wpa_supplicant", "wlan0.conf.tmpl"),
                        os.path.join(work_dir, "overlay", "wpa_supplicant", "wlan0.conf"),
                        {"STATION_SSID": opts["station_ssid"], "STATION_PASSWORD": opts["station_password"],
                         "COUNTRY": opts["country"]})

    klipplug_source = stage_klipplug(os.path.join(work_dir, "overlay"), bool(opts.get("klipplug")), say)

    write_file(creds_path,
              "# Credentials baked into every unit built from this ISO.\n"
              "# Keep this file safe: anyone who has it can log in over SSH"
              + (", or join the WiFi hotspot," if opts["wifi_mode"] == "ap" else "") +
              " on any unit imaged from this build.\n"
              "print user login: print / %s\n" % print_password
              + ("WiFi hotspot passphrase: %s\n" % wifi_passphrase if opts["wifi_mode"] == "ap" else
                 "Joins WiFi network: %s\n" % opts["station_ssid"]),
              mode=0o600, posix=False)

    if CANCEL.is_set():
        raise CancelledError("Cancelled before downloading anything.")
    iso_path = fetch_netinst_iso(work_dir, say, progress, cancel_event)

    say("Extracting the installer (a minute or two)...")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)
    rc, out = run_tracked(["7z", "-y", "x", iso_path, "-o" + extract_dir], timeout=600)
    if rc != 0:
        raise InjectError("Couldn't extract the Debian ISO:\n\n%s" % out[-1500:])
    run(["chmod", "-R", "u+w", extract_dir], check=False)

    say("Copying the preseed and Klipper overlay onto the installer...")
    shutil.copy2(os.path.join(work_dir, "preseed.cfg"), os.path.join(extract_dir, "preseed.cfg"))
    # rsync the whole overlay/ (the rendered secrets plus every static file:
    # late-command.sh, systemd units, nginx template, etc.) onto the ISO.
    # (overlay/klipplug is left out: this build stages its own copy above,
    # or none - a copy setup.sh left behind mustn't ride along uninvited.)
    rc, out = run_tracked(["rsync", "-a", "--exclude", "/klipplug/", os.path.join(PROJECT_DIR, "overlay") + "/",
                           os.path.join(work_dir, "overlay") + "/"], timeout=120)
    if rc != 0:
        raise InjectError("Couldn't assemble the overlay:\n\n%s" % out[-1000:])
    rc, out = run_tracked(["rsync", "-a", os.path.join(work_dir, "overlay"), extract_dir + "/"], timeout=120)
    if rc != 0:
        raise InjectError("Couldn't copy the overlay onto the installer:\n\n%s" % out[-1000:])

    say("Patching the boot menu for a fully unattended install...")
    boot_menu_patched = patch_boot_menu(extract_dir)
    if not boot_menu_patched:
        log.warning("Could not confirm the boot-menu patch took in one or both boot paths - "
                    "Debian may have reshuffled its file layout since this was written.")

    say("Building %s..." % os.path.basename(out_iso))
    isohdpfx = "/usr/lib/ISOLINUX/isohdpfx.bin"
    if not os.path.isfile(isohdpfx):
        isohdpfx = "/usr/lib/syslinux/isohdpfx.bin"
    cmd = [
        "xorriso", "-as", "mkisofs", "-r", "-V", "KLIPPER_AUTO_INSTALL", "-o", out_iso,
        "-J", "-joliet-long", "-cache-inodes",
        "-isohybrid-mbr", isohdpfx,
        "-b", "isolinux/isolinux.bin", "-c", "isolinux/boot.cat",
        "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
        "-eltorito-alt-boot",
        "-e", "boot/grub/efi.img", "-no-emul-boot", "-isohybrid-gpt-basdat",
        extract_dir,
    ]
    rc, out = run_tracked(cmd, timeout=600)
    if rc != 0:
        raise InjectError("xorriso failed to build the ISO:\n\n%s" % out[-1500:])
    if os.geteuid() == 0 and REAL_UID != 0:
        for p in (out_iso, creds_path):
            try:
                os.chown(p, REAL_UID, REAL_GID)
            except OSError:
                pass

    log.info("Built %s (webui=%s crowsnest=%s klipplug=%s wifi_mode=%s country=%s)", out_iso, opts["webui"],
             opts["crowsnest"], klipplug_source or "no", opts["wifi_mode"], opts["country"])
    return {
        "iso_path": out_iso, "creds_path": creds_path, "print_password": print_password,
        "wifi_passphrase": wifi_passphrase, "boot_menu_patched": boot_menu_patched,
        "klipplug_source": klipplug_source,
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_build_inputs(opts, countries):
    country = opts["country"]
    if not country:
        return ("Please enter your 2-letter WiFi country code (e.g. GB, US, DE, FR, AU).\n\nIt's the law in "
                "most places: it selects which WiFi channels and power levels the radio may legally use.")
    if len(country) != 2 or not country.isalpha():
        return "Country code must be exactly 2 letters (e.g. GB, US, DE)."
    if countries is not None and country not in countries:
        return "'%s' is not a valid ISO country code (UK users: use GB)." % country

    prefix = opts["hostname_prefix"]
    if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,20}[a-zA-Z0-9])?$", prefix or ""):
        return "Hostname prefix may only use letters, digits and '-' (e.g. 'klipper'), and can't start/end with '-'."

    pw = opts.get("print_password")
    if pw and not (8 <= len(pw) <= 63):
        return "The print-user password must be 8-63 characters (leave it blank for a random one)."

    if opts["wifi_mode"] == "ap":
        wp = opts.get("wifi_passphrase")
        if wp and not (8 <= len(wp) <= 63):
            return "The WiFi hotspot passphrase must be 8-63 characters (leave it blank for a random one)."
        if wp and "#" in wp:
            return "The WiFi passphrase may not contain '#' (it breaks the hostapd config file)."
    else:
        ssid = (opts.get("station_ssid") or "").strip()
        if not ssid:
            return "Enter the WiFi network name (SSID) to join in station mode."
        if len(ssid.encode("utf-8")) > 32:
            return "SSID is too long (maximum 32 bytes)."
        spw = opts.get("station_password") or ""
        if not (8 <= len(spw) <= 63):
            return "The WiFi network's password must be 8-63 characters."
    return None


# --------------------------------------------------------------------------- #
# Licence (PolyForm Noncommercial 1.0.0, verbatim - same terms this
# project's sibling tool (pi_injector.py) publishes under) + disclaimer
# --------------------------------------------------------------------------- #
REQUIRED_NOTICE = "Required Notice: Copyright %s (https://github.com/The-Dorkknight)" % COPYRIGHT_HOLDER

LICENSE_TEXT = r"""# PolyForm Noncommercial License 1.0.0

<https://polyformproject.org/licenses/noncommercial/1.0.0>

## Acceptance

In order to get any license under these terms, you must agree to them as both strict obligations and conditions to all your licenses.

## Copyright License

The licensor grants you a copyright license for the software to do everything you might do with the software that would otherwise infringe the licensor's copyright in it for any permitted purpose.  However, you may only distribute the software according to [Distribution License](#distribution-license) and make changes or new works based on the software according to [Changes and New Works License](#changes-and-new-works-license).

## Distribution License

The licensor grants you an additional copyright license to distribute copies of the software.  Your license to distribute covers distributing the software with changes and new works permitted by [Changes and New Works License](#changes-and-new-works-license).

## Notices

You must ensure that anyone who gets a copy of any part of the software from you also gets a copy of these terms or the URL for them above, as well as copies of any plain-text lines beginning with `Required Notice:` that the licensor provided with the software.  For example:

> Required Notice: Copyright Yoyodyne, Inc. (http://example.com)

## Changes and New Works License

The licensor grants you an additional copyright license to make changes and new works based on the software for any permitted purpose.

## Patent License

The licensor grants you a patent license for the software that covers patent claims the licensor can license, or becomes able to license, that you would infringe by using the software.

## Noncommercial Purposes

Any noncommercial purpose is a permitted purpose.

## Personal Uses

Personal use for research, experiment, and testing for the benefit of public knowledge, personal study, private entertainment, hobby projects, amateur pursuits, or religious observance, without any anticipated commercial application, is use for a permitted purpose.

## Noncommercial Organizations

Use by any charitable organization, educational institution, public research organization, public safety or health organization, environmental protection organization, or government institution is use for a permitted purpose regardless of the source of funding or obligations resulting from the funding.

## Fair Use

You may have "fair use" rights for the software under the law. These terms do not limit them.

## No Other Rights

These terms do not allow you to sublicense or transfer any of your licenses to anyone else, or prevent the licensor from granting licenses to anyone else.  These terms do not imply any other licenses.

## Patent Defense

If you make any written claim that the software infringes or contributes to infringement of any patent, your patent license for the software granted under these terms ends immediately. If your company makes such a claim, your patent license ends immediately for work on behalf of your company.

## Violations

The first time you are notified in writing that you have violated any of these terms, or done anything with the software not covered by your licenses, your licenses can nonetheless continue if you come into full compliance with these terms, and take practical steps to correct past violations, within 32 days of receiving notice.  Otherwise, all your licenses end immediately.

## No Liability

***As far as the law allows, the software comes as is, without any warranty or condition, and the licensor will not be liable to you for any damages arising out of these terms or the use or nature of the software, under any kind of legal claim.***

## Definitions

The **licensor** is the individual or entity offering these terms, and the **software** is the software the licensor makes available under these terms.

**You** refers to the individual or entity agreeing to these terms.

**Your company** is any legal entity, sole proprietorship, or other kind of organization that you work for, plus all organizations that have control over, are under the control of, or are under common control with that organization.  **Control** means ownership of substantially all the assets of an entity, or the power to direct its management and policies by vote, contract, or otherwise.  Control can be direct or indirect.

**Your licenses** are all the licenses granted to you for the software under these terms.

**Use** means anything you do with the software requiring one of your licenses.
"""

DISCLAIMER_TEXT = (
    "This tool writes directly to disks and downloads/installs software as root. Pointed at the wrong "
    "drive, it can permanently erase that drive's data. The unattended installer it builds ERASES the "
    "Wyse 3040's internal eMMC without asking again once it boots.\n\n"
    "•  It's free for NON-COMMERCIAL use only: personal, hobby, educational and charity use. Commercial "
    "use needs the author's permission.\n\n"
    "•  It comes AS IS, with NO WARRANTY of any kind. You use it entirely at your own risk.\n\n"
    "•  The author accepts NO LIABILITY for any loss or damage, including lost data, damaged drives, "
    "computers, Wyse units or printers.\n\n"
    "•  This tool has not been verified to boot real Wyse 3040 hardware end-to-end - test in a VM first "
    "where you can (UEFI mode enabled).\n\n"
    "Full terms: PolyForm Noncommercial License 1.0.0. Reading them is optional."
)


def licence_for_display():
    text = LICENSE_TEXT
    text = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)
    text = text.replace("***", "").replace("**", "").replace("`", "")
    text = re.sub(r"^## (.+)$", lambda m: m.group(1).upper(), text, flags=re.M)
    text = re.sub(r"^# ", "", text, flags=re.M)
    text = re.sub(r"^<(.+)>$", r"\1", text, flags=re.M)
    return "%s\n\n%s" % (REQUIRED_NOTICE, text.strip())


def center_window(win, width=None, height=None):
    win.update_idletasks()
    w = width or win.winfo_reqwidth()
    h = height or win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(w, sw - 20), min(h, sh - 60)
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 3)
    win.geometry("%dx%d+%d+%d" % (w, h, x, y))


def make_modal(win):
    try:
        win.wait_visibility()
        win.grab_set()
    except tk.TclError:
        pass
    win.lift()
    win.focus_force()


def _mb(fn, title, text, **kw):
    head, _sep, rest = str(text).partition("\n\n")
    if rest.strip():
        kw["detail"] = rest
    return fn(title, head, **kw)


def mb_error(title, text, **kw):
    return _mb(messagebox.showerror, title, text, **kw)


def mb_info(title, text, **kw):
    return _mb(messagebox.showinfo, title, text, **kw)


def mb_warning(title, text, **kw):
    return _mb(messagebox.showwarning, title, text, **kw)


def mb_yesno(title, text, **kw):
    return _mb(messagebox.askyesno, title, text, **kw)


def show_licence(parent):
    win = tk.Toplevel(parent)
    win.title("Licence - PolyForm Noncommercial 1.0.0")
    frame = tk.Frame(win, padx=10, pady=10)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", font=FONT_LABEL, width=78, height=28, padx=8, pady=8)
    sb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.insert("1.0", licence_for_display())
    text.configure(state="disabled")
    tk.Button(win, text="Close", font=FONT_BTN, width=12, command=win.destroy).pack(pady=(0, 10))
    center_window(win, 720, 620)
    make_modal(win)
    win.wait_window()


class DisclaimerDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.accepted = False
        self.title("Before you start - %s" % APP_TITLE)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._quit)

        body = tk.Frame(self, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Before you start", font=FONT_HEADER).pack(anchor="w")
        tk.Label(body, text="Please read this - it's short.", font=FONT_SMALL, fg="#4a5568").pack(
            anchor="w", pady=(0, 10))
        tk.Label(body, text=DISCLAIMER_TEXT, font=FONT_LABEL, justify="left", wraplength=540, anchor="w").pack(
            anchor="w")

        self.ack_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body, text="I understand and accept these terms, and I've backed up anything important on the "
            "drives I'll use with this tool.", variable=self.ack_var, command=self._update, font=FONT_LABEL,
            wraplength=520, justify="left",
        ).pack(anchor="w", pady=(14, 0))

        btns = tk.Frame(body)
        btns.pack(fill="x", pady=(16, 0))
        tk.Button(btns, text="View licence (optional)", font=FONT_LABEL, command=lambda: show_licence(self)).pack(
            side="left")
        self.agree_btn = tk.Button(btns, text="I agree - continue", font=FONT_BTN, bg="#2f855a", fg="white",
                                   state="disabled", command=self._agree)
        self.agree_btn.pack(side="right")
        tk.Button(btns, text="Quit", font=FONT_LABEL, width=8, command=self._quit).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self._quit())
        center_window(self)
        make_modal(self)

    def _update(self):
        self.agree_btn.config(state="normal" if self.ack_var.get() else "disabled")

    def _agree(self):
        if not self.ack_var.get():
            return
        self.accepted = True
        log.info("Disclaimer accepted.")
        self.destroy()

    def _quit(self):
        self.accepted = False
        self.destroy()


class ReallySureDialog(tk.Toplevel):
    def __init__(self, master, target):
        super().__init__(master)
        self.confirmed = False
        self.expected = os.path.basename(target["disk"])
        self.title("Sanity check 2 of 2")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        body = tk.Frame(self, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Are you reeeeley sure you want to overwrite disk\n%s ?" % target["desc"],
                font=("Sans", 14, "bold"), fg="#c53030", justify="left").pack(anchor="w")
        tk.Label(body, text="Everything on it will be gone for good. This cannot be undone.\n\n"
                "To confirm, type the drive's name below:   %s" % self.expected,
                font=FONT_LABEL, justify="left").pack(anchor="w", pady=(12, 6))

        self.entry_var = tk.StringVar()
        self.entry_var.trace_add("write", lambda *a: self._update())
        entry = tk.Entry(body, textvariable=self.entry_var, font=("Monospace", 13), width=14)
        entry.pack(anchor="w")

        btns = tk.Frame(body)
        btns.pack(fill="x", pady=(16, 0))
        self.go_btn = tk.Button(btns, text="Yes - overwrite it", font=FONT_BTN, bg="#c53030", fg="white",
                                state="disabled", command=self._go)
        self.go_btn.pack(side="right")
        tk.Button(btns, text="Cancel", font=FONT_BTN, width=10, command=self.destroy).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self.destroy())
        entry.bind("<Return>", lambda e: self._go())
        center_window(self)
        make_modal(self)
        entry.focus_set()

    def _matches(self):
        typed = self.entry_var.get().strip()
        return typed in (self.expected, "/dev/" + self.expected)

    def _update(self):
        self.go_btn.config(state="normal" if self._matches() else "disabled")

    def _go(self):
        if self._matches():
            self.confirmed = True
            self.destroy()


class ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, padx=14, pady=10)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event=None):
        self.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        self.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _unbind_wheel(self, _event=None):
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.unbind_all(seq)


def desktop_quote(arg):
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"%s"' % escaped.replace("%", "%%")


def install_menu_launcher():
    if REAL_UID == 0:
        raise InjectError("Couldn't work out which user account to add the menu entry for.")
    script = os.path.abspath(__file__)
    apps_dir = os.path.join(REAL_HOME, ".local", "share", "applications")
    created = []
    path = apps_dir
    while not os.path.isdir(path):
        created.append(path)
        path = os.path.dirname(path)
    os.makedirs(apps_dir, exist_ok=True)
    for d in created:
        os.chown(d, REAL_UID, REAL_GID)
    entry = (
        "[Desktop Entry]\nType=Application\nVersion=1.0\nName=Wyse 3040 Klipper Injector\n"
        "Comment=Build an unattended Klipper installer ISO for Dell Wyse 3040 thin clients\n"
        "Exec=%s %s\nPath=%s\nIcon=printer\nTerminal=false\nCategories=Utility;System;\n"
        % (desktop_quote(sys.executable or "python3"), desktop_quote(script), os.path.dirname(script))
    )
    dest = os.path.join(apps_dir, "wyse-klipper-injector.desktop")
    with open(dest, "w") as f:
        f.write(entry)
    os.chown(dest, REAL_UID, REAL_GID)
    os.chmod(dest, 0o755)
    return dest


# --------------------------------------------------------------------------- #
# GUI Application
# --------------------------------------------------------------------------- #
TAB_BUILD, TAB_WRITE, TAB_EXTRAS = 0, 1, 2
COLOR_INFO = "#2b6cb0"
COLOR_OK = "#2f855a"
COLOR_BAD = "#c53030"
COLOR_HINT = "#4a5568"


class WyseInjectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title("%s v%s (Built %s)" % (APP_TITLE, APP_VERSION, __build_date__))
        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass
        self.countries = load_country_codes()
        self.busy = False
        self.busy_kind = None
        self.cancel_event = CANCEL
        self._ui_queue = queue.Queue()
        self.write_targets = []
        self.rejected_targets = []
        self._targets_signature = None
        self.action_widgets = []
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_queue()

    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        log.error("Unexpected error in the window", exc_info=(exc_type, exc_value, exc_tb))
        try:
            mb_error("Unexpected error", "Something unexpected went wrong:\n\n%s: %s\n\n(Details are in the "
                    "log file: %s)" % (exc_type.__name__, exc_value, LOG_FILE))
        except Exception:
            pass

    def start(self):
        self.build_ui()
        self.refresh_targets()
        self.after(3000, self._auto_refresh_targets)
        sh = self.winfo_screenheight()
        center_window(self, 700, min(760, sh - 80))
        self.minsize(620, 480)
        self.deiconify()
        self.lift()

    # ---- layout -------------------------------------------------------- #
    def build_ui(self):
        header = tk.Frame(self, padx=14, pady=8)
        header.pack(fill="x")
        tk.Label(header, text="Wyse 3040 Klipper Injector", font=FONT_HEADER).pack(anchor="w")
        tk.Label(header, text="1. Build the installer ISO   →   2. Write it to a USB stick   →   "
                "3. Boot the Wyse from it", font=FONT_SMALL, fg=COLOR_HINT).pack(anchor="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8)

        tabs = []
        for label in ("  1. Build ISO  ", "  2. Write to USB  ", "  Extras  "):
            outer = tk.Frame(self.notebook)
            footer = tk.Frame(outer, pady=6)
            footer.pack(side="bottom", fill="x")
            ttk.Separator(outer, orient="horizontal").pack(side="bottom", fill="x")
            sf = ScrollFrame(outer)
            sf.pack(side="top", fill="both", expand=True)
            self.notebook.add(outer, text=label)
            tabs.append((sf.inner, footer))
        self.build_build_tab(*tabs[TAB_BUILD])
        self.build_write_tab(*tabs[TAB_WRITE])
        self.build_extras_tab(tabs[TAB_EXTRAS][0])
        tabs[TAB_EXTRAS][1].pack_forget()

        bottom = tk.Frame(self, padx=14, pady=8)
        bottom.pack(fill="x")
        row = tk.Frame(bottom)
        row.pack(fill="x")
        self.progress = ttk.Progressbar(row, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.cancel_btn = tk.Button(row, text="Cancel", font=FONT_LABEL, state="disabled", command=self.on_cancel)
        self.cancel_btn.pack(side="left", padx=(8, 0))
        status_row = tk.Frame(bottom)
        status_row.pack(fill="x", pady=(4, 0))
        self.status_lbl = tk.Label(status_row, text="Ready.", font=FONT_LABEL, fg=COLOR_INFO, anchor="w",
                                   justify="left", wraplength=560)
        self.status_lbl.pack(side="left", fill="x", expand=True)
        tk.Label(status_row, text="v%s • %s" % (APP_VERSION, __build_date__), font=FONT_SMALL,
                fg=COLOR_HINT, anchor="e").pack(side="right")

    def _hint(self, parent, text, **grid_or_pack):
        wrap = 440 if grid_or_pack else 600
        lbl = tk.Label(parent, text=text, font=FONT_SMALL, fg=COLOR_HINT, justify="left", anchor="w", wraplength=wrap)
        if grid_or_pack:
            lbl.grid(**grid_or_pack)
        else:
            lbl.pack(anchor="w", fill="x")
        return lbl

    def build_build_tab(self, f, footer):
        tk.Label(f, text="Build an unattended Debian 12 installer ISO that turns a Wyse 3040 into a Klipper "
                "controller.", font=FONT_LABEL, anchor="w", justify="left", wraplength=600).pack(anchor="w", fill="x")
        self._hint(f, "One ISO images as many units as you like - every unit built from it gets its own "
                  "hostname (derived from its ethernet MAC), but shares the login password and (in AP mode) "
                  "WiFi passphrase set here. Booting a unit from it ERASES its internal eMMC without asking "
                  "again.")

        form = tk.Frame(f)
        form.pack(fill="x", pady=(10, 4))
        form.columnconfigure(1, weight=1)
        r = 0

        tk.Label(form, text="Print user password:", font=FONT_LABEL).grid(row=r, column=0, sticky="e", pady=3,
                                                                          padx=(0, 6))
        pw_row = tk.Frame(form)
        pw_row.grid(row=r, column=1, sticky="w", pady=3)
        self.print_pw_entry = tk.Entry(pw_row, font=FONT_LABEL, width=20)
        self.print_pw_entry.insert(0, rand_alnum(16))
        self.print_pw_entry.pack(side="left")
        self.print_show_pw = tk.BooleanVar(value=True)
        tk.Checkbutton(pw_row, text="Show", variable=self.print_show_pw, font=FONT_SMALL,
                      command=lambda: self.print_pw_entry.config(show="" if self.print_show_pw.get() else "*")
                      ).pack(side="left", padx=4)
        tk.Button(pw_row, text="New random", font=FONT_SMALL,
                 command=lambda: (self.print_pw_entry.delete(0, "end"), self.print_pw_entry.insert(0, rand_alnum(16)))
                 ).pack(side="left")
        r += 1
        self._hint(form, "Login: user 'print' over SSH (installed and enabled automatically - port 22 is open "
                  "on both ethernet and WiFi). Write it down!", row=r, column=1, sticky="w")
        r += 1

        tk.Label(form, text="Hostname prefix:", font=FONT_LABEL).grid(row=r, column=0, sticky="e", pady=3, padx=(0, 6))
        self.hostname_entry = tk.Entry(form, font=FONT_LABEL, width=16)
        self.hostname_entry.insert(0, "klipper")
        self.hostname_entry.grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        self._hint(form, "Each unit becomes <prefix>-<unique suffix>, e.g. klipper-a1b2c3.", row=r, column=1,
                  sticky="w")
        r += 1

        tk.Label(form, text="Country code:", font=FONT_LABEL).grid(row=r, column=0, sticky="e", pady=3, padx=(0, 6))
        self.country_entry = tk.Entry(form, font=FONT_LABEL, width=6)
        self.country_entry.insert(0, default_country_code())
        self.country_entry.grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        self._hint(form, "Where the units will actually run: GB, US, DE, FR, AU... Sets the legal WiFi "
                  "channels/power - not cosmetic, and no longer hard-coded to US.", row=r, column=1, sticky="w")
        r += 1

        wifi = tk.LabelFrame(f, text=" WiFi ", font=FONT_LABEL, padx=10, pady=8)
        wifi.pack(fill="x", pady=8)
        self.wifi_mode_var = tk.StringVar(value="ap")
        tk.Radiobutton(wifi, text="Hotspot / AP mode (default - broadcasts its own network)",
                      variable=self.wifi_mode_var, value="ap", font=FONT_LABEL,
                      command=self._toggle_wifi_mode).pack(anchor="w")
        tk.Radiobutton(wifi, text="Station mode (joins an existing router's WiFi instead)",
                      variable=self.wifi_mode_var, value="station", font=FONT_LABEL,
                      command=self._toggle_wifi_mode).pack(anchor="w")

        self.ap_frame = tk.Frame(wifi)
        self.ap_frame.pack(fill="x", pady=(6, 0))
        ap_pw_row = tk.Frame(self.ap_frame)
        ap_pw_row.pack(anchor="w")
        tk.Label(ap_pw_row, text="Hotspot passphrase:", font=FONT_LABEL).pack(side="left")
        self.ap_pw_entry = tk.Entry(ap_pw_row, font=FONT_LABEL, width=20)
        self.ap_pw_entry.insert(0, rand_alnum(16))
        self.ap_pw_entry.pack(side="left", padx=(6, 4))
        tk.Button(ap_pw_row, text="New random", font=FONT_SMALL,
                 command=lambda: (self.ap_pw_entry.delete(0, "end"), self.ap_pw_entry.insert(0, rand_alnum(16)))
                 ).pack(side="left")
        self._hint(self.ap_frame, "Every unit broadcasts Klipper-<suffix> using this same passphrase. ethernet "
                  "still works too (DHCP, or link-local when plugged straight into a PC).")

        self.station_frame = tk.Frame(wifi)
        s_ssid_row = tk.Frame(self.station_frame)
        s_ssid_row.pack(anchor="w", pady=(0, 2))
        tk.Label(s_ssid_row, text="Network name (SSID):", font=FONT_LABEL).pack(side="left")
        self.station_ssid_entry = tk.Entry(s_ssid_row, font=FONT_LABEL, width=22)
        self.station_ssid_entry.pack(side="left", padx=(6, 0))
        s_pw_row = tk.Frame(self.station_frame)
        s_pw_row.pack(anchor="w")
        tk.Label(s_pw_row, text="Network password:", font=FONT_LABEL).pack(side="left")
        self.station_pw_entry = tk.Entry(s_pw_row, font=FONT_LABEL, width=22)
        self.station_pw_entry.pack(side="left", padx=(6, 0))
        self._hint(self.station_frame, "Every unit joins this router's WiFi as a normal client instead of "
                  "making its own hotspot. Same credentials baked into every unit from this ISO.")

        webui = tk.LabelFrame(f, text=" Web UI & extras ", font=FONT_LABEL, padx=10, pady=6)
        webui.pack(fill="x", pady=8)
        self.webui_var = tk.StringVar(value="mainsail")
        row = tk.Frame(webui)
        row.pack(anchor="w")
        tk.Radiobutton(row, text="Mainsail", variable=self.webui_var, value="mainsail", font=FONT_LABEL).pack(
            side="left")
        tk.Radiobutton(row, text="Fluidd", variable=self.webui_var, value="fluidd", font=FONT_LABEL).pack(
            side="left", padx=(12, 0))
        self.crowsnest_var = tk.BooleanVar(value=False)
        tk.Checkbutton(webui, text="Crowsnest (webcam support)", variable=self.crowsnest_var, font=FONT_LABEL).pack(
            anchor="w", pady=(4, 0))
        self.klipplug_var = tk.BooleanVar(value=False)
        tk.Checkbutton(webui, text="KlipPlug (plugin manager) - experimental", variable=self.klipplug_var,
                       font=FONT_LABEL).pack(anchor="w")
        self._hint(webui, "Adds a plugin page next to the web UI (port %d) that can run many OctoPrint plugins "
                  "on Klipper. Work in progress: not yet tried on a real printer. A klipplug folder or zip "
                  "placed next to this tool is baked into the ISO; otherwise each unit downloads it from "
                  "GitHub while it installs. If a unit can't get it, the rest still installs." % KLIPPLUG_PORT)
        self._hint(webui, "Every unit also gets a boot-diagnostics report at http://<unit>/diagnostics.txt - "
                  "there's no boot partition to pull out and read on a soldered eMMC, so it's served over "
                  "HTTP instead.")

        out_row = tk.Frame(f)
        out_row.pack(fill="x", pady=(6, 0))
        tk.Label(out_row, text="Save ISO to:", font=FONT_LABEL).pack(side="left")
        self.out_dir_var = tk.StringVar(value=PROJECT_DIR)
        tk.Entry(out_row, textvariable=self.out_dir_var, font=FONT_LABEL).pack(side="left", fill="x", expand=True,
                                                                                padx=6)
        self.out_dir_btn = tk.Button(out_row, text="Browse...", font=FONT_LABEL, command=self.on_browse_out_dir)
        self.out_dir_btn.pack(side="left")

        self.build_btn = tk.Button(footer, text="Build Klipper ISO", font=FONT_BTN, bg="#2c7a7b", fg="white",
                                   height=2, width=30, command=self.on_build)
        self.build_btn.pack()
        self.action_widgets += [self.print_pw_entry, self.ap_pw_entry, self.station_ssid_entry,
                                self.station_pw_entry, self.hostname_entry, self.country_entry,
                                self.out_dir_btn, self.build_btn]
        self._toggle_wifi_mode()

    def _toggle_wifi_mode(self):
        if self.wifi_mode_var.get() == "ap":
            self.station_frame.pack_forget()
            self.ap_frame.pack(fill="x", pady=(6, 0))
        else:
            self.ap_frame.pack_forget()
            self.station_frame.pack(fill="x", pady=(6, 0))

    def on_browse_out_dir(self):
        path = filedialog.askdirectory(parent=self, title="Where should the ISO be saved?",
                                       initialdir=self.out_dir_var.get() or PROJECT_DIR)
        if path:
            self.out_dir_var.set(path)

    def on_build(self):
        if self.busy:
            return
        opts = {
            "print_password": self.print_pw_entry.get().strip(),
            "hostname_prefix": self.hostname_entry.get().strip(),
            "country": self.country_entry.get().strip().upper(),
            "wifi_mode": self.wifi_mode_var.get(),
            "wifi_passphrase": self.ap_pw_entry.get().strip(),
            "station_ssid": self.station_ssid_entry.get().strip(),
            "station_password": self.station_pw_entry.get().strip(),
            "webui": self.webui_var.get(),
            "crowsnest": self.crowsnest_var.get(),
            "klipplug": self.klipplug_var.get(),
            "out_dir": os.path.expanduser(self.out_dir_var.get().strip() or PROJECT_DIR),
        }
        err = validate_build_inputs(opts, self.countries)
        if err:
            mb_error("Please check the settings", err)
            return
        if not os.path.isdir(opts["out_dir"]):
            mb_error("Folder not found", "%s doesn't exist - choose an existing folder." % opts["out_dir"])
            return

        wifi_summary = ("Hotspot: Klipper-<suffix>, password you set" if opts["wifi_mode"] == "ap"
                        else "Joins '%s'" % opts["station_ssid"])
        if not mb_yesno(
            "Build the Klipper ISO?",
            "This downloads the official Debian netinst ISO (~700 MB, checksum-verified) if not already "
            "cached, then builds:\n\n      klipper-wyse3040-installer.iso\n\ninto:\n      %s\n\n"
            "Web UI: %s%s\nWiFi: %s\nCountry: %s\n\nTakes several minutes depending on your connection. "
            "Continue?" % (opts["out_dir"], opts["webui"].title(),
                           (" + Crowsnest" if opts["crowsnest"] else "") + (" + KlipPlug" if opts["klipplug"] else ""),
                           wifi_summary, opts["country"]),
        ):
            return
        log.info("Building ISO: webui=%s crowsnest=%s klipplug=%s wifi_mode=%s country=%s hostname_prefix=%s",
                 opts["webui"], opts["crowsnest"], opts["klipplug"], opts["wifi_mode"], opts["country"],
                 opts["hostname_prefix"])

        def on_success(result):
            self._finish_ok("Built %s." % os.path.basename(result["iso_path"]))
            lines = [
                "Built:\n      %s\n" % result["iso_path"],
                "WRITE THESE DOWN (also saved to %s):" % result["creds_path"],
                "      SSH login:  ssh print@<unit-ip-or-hostname>.local",
                "      Password:   %s" % result["print_password"],
            ]
            if opts["wifi_mode"] == "ap":
                lines.append("      WiFi passphrase:  %s" % result["wifi_passphrase"])
            if result.get("klipplug_source"):
                lines.append("\nKlipPlug (experimental): units get it from %s. Its page: http://<unit>:%d. "
                             "Whether a unit really got it is shown at http://<unit>/diagnostics.txt."
                             % (result["klipplug_source"], KLIPPLUG_PORT))
            lines.append("\nNext: tab 2 (Write to USB) to flash it onto a USB stick, then boot each Wyse 3040 "
                        "from it (F12/Esc at power-on, ethernet plugged in with internet access). It's fully "
                        "unattended and wipes the eMMC without asking again.")
            if mb_yesno("ISO ready!", "\n".join(lines) + "\n\nGo to tab 2 now?"):
                self.image_var.set(result["iso_path"])
                self.notebook.select(TAB_WRITE)

        self._run_async(lambda: build_klipper_iso(opts, self.say, self.report_progress, self.cancel_event),
                        on_success, "Preparing to build the ISO...", kind="build", cancellable=True)

    def build_write_tab(self, f, footer):
        tk.Label(f, text="Write the Klipper installer ISO (or any disk image) onto a USB stick.",
                font=FONT_LABEL, anchor="w", justify="left").pack(anchor="w", fill="x")
        self._hint(f, "EVERYTHING on the chosen drive gets erased. For safety, only removable drives sized "
                  "%s (roughly) are offered. Internal disks are never offered." % WRITE_TARGET_LABEL)

        img = tk.LabelFrame(f, text=" Image file ", font=FONT_LABEL, padx=10, pady=8)
        img.pack(fill="x", pady=(10, 6))
        row = tk.Frame(img)
        row.pack(fill="x")
        self.image_var = tk.StringVar()
        tk.Entry(row, textvariable=self.image_var, font=FONT_LABEL).pack(side="left", fill="x", expand=True)
        self.browse_btn = tk.Button(row, text="Browse...", font=FONT_LABEL, command=self.on_browse_image)
        self.browse_btn.pack(side="left", padx=(6, 0))
        self._hint(img, "Filled in automatically after a build on tab 1 - or browse to any .iso/.img file.")

        drv = tk.LabelFrame(f, text=" Drive to ERASE and write to ", font=FONT_LABEL, padx=10, pady=8)
        drv.pack(fill="x", pady=6)
        row = tk.Frame(drv)
        row.pack(fill="x")
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(row, textvariable=self.target_var, state="readonly", font=FONT_LABEL)
        self.target_combo.pack(side="left", fill="x", expand=True)
        self.target_combo.bind("<<ComboboxSelected>>", lambda e: self._show_target_details())
        self.refresh_btn = tk.Button(row, text="Refresh", font=FONT_LABEL, command=self.refresh_targets)
        self.refresh_btn.pack(side="left", padx=(6, 0))
        self.target_details = tk.Label(drv, text="", font=FONT_SMALL, fg=COLOR_BAD, justify="left", anchor="w",
                                       wraplength=600)
        self.target_details.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Button(drv, text="Why isn't my drive listed?", font=FONT_SMALL, relief="flat", fg=COLOR_INFO,
                 cursor="hand2", command=self.on_why_not_listed).pack(anchor="w")

        self.verify_var = tk.BooleanVar(value=True)
        tk.Checkbutton(f, text="Verify after writing (recommended - catches bad or fake drives, roughly "
                      "doubles the time)", variable=self.verify_var, font=FONT_LABEL, wraplength=600,
                      justify="left").pack(anchor="w", pady=(6, 4))

        self.write_btn = tk.Button(footer, text="Write Image to Drive", font=FONT_BTN, bg=COLOR_BAD, fg="white",
                                   height=2, width=30, command=self.on_write)
        self.write_btn.pack()
        self.action_widgets += [self.browse_btn, self.refresh_btn, self.write_btn, self.target_combo]

    def build_extras_tab(self, f):
        box = tk.LabelFrame(f, text=" Start menu shortcut ", font=FONT_LABEL, padx=10, pady=8)
        box.pack(fill="x", pady=(0, 8))
        self._hint(box, "Adds 'Wyse 3040 Klipper Injector' to your applications menu.")
        self.launcher_btn = tk.Button(box, text="Add to applications menu", font=FONT_LABEL, width=22,
                                      command=self.on_add_launcher)
        self.launcher_btn.pack(anchor="w", pady=(6, 0))

        box = tk.LabelFrame(f, text=" About ", font=FONT_LABEL, padx=10, pady=8)
        box.pack(fill="x", pady=8)
        tk.Label(box, text="WORK IN PROGRESS: not yet verified to boot real Wyse 3040 hardware end-to-end.",
                font=("Sans", 9, "bold"), fg=COLOR_BAD, justify="left", anchor="w", wraplength=600).pack(
                anchor="w", fill="x", pady=(0, 4))
        tk.Label(box, text="%s v%s (Built %s)\nCopyright (c) 2026 %s\nFree for non-commercial use - PolyForm "
                "Noncommercial 1.0.0.\nProvided as is, with no warranty and no liability.\nLog file: %s"
                % (APP_TITLE, APP_VERSION, __build_date__, COPYRIGHT_HOLDER, LOG_FILE), font=FONT_SMALL,
                justify="left", anchor="w", wraplength=600).pack(anchor="w", fill="x")
        tk.Button(box, text="View licence", font=FONT_LABEL, command=lambda: show_licence(self)).pack(
            anchor="w", pady=(6, 0))
        self.action_widgets.append(self.launcher_btn)

    # ---- thread-safe status/progress/completion plumbing (identical
    # pattern to pi_injector.py - Tk widgets may only be touched from the
    # main thread, so worker threads just queue messages) ---------------- #
    def _poll_queue(self):
        try:
            self.after(100, self._poll_queue)
        except tk.TclError:
            return
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "status":
                    text, color = payload
                    self.status_lbl.config(text=text, fg=color)
                elif kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "done":
                    payload()
        except queue.Empty:
            pass
        except tk.TclError:
            pass

    def say(self, text, color=COLOR_INFO):
        if threading.current_thread() is threading.main_thread():
            self.status_lbl.config(text=text, fg=color)
        else:
            self._ui_queue.put(("status", (text, color)))

    def report_progress(self, fraction, text):
        self._ui_queue.put(("progress", (fraction, text)))

    def _apply_progress(self, fraction, text):
        if fraction is None:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.config(mode="indeterminate")
                self.progress.start(15)
        else:
            if str(self.progress.cget("mode")) != "determinate":
                self.progress.stop()
                self.progress.config(mode="determinate")
            self.progress["value"] = max(0.0, min(100.0, fraction * 100.0))
        if text:
            self.status_lbl.config(text=text, fg=COLOR_INFO)

    def _reset_progress(self):
        self.progress.stop()
        self.progress.config(mode="determinate")
        self.progress["value"] = 0

    def _set_buttons_enabled(self, enabled):
        for w in self.action_widgets:
            try:
                if isinstance(w, ttk.Combobox):
                    w.config(state="readonly" if enabled else "disabled")
                else:
                    w.config(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass

    def _end_busy(self):
        self.busy = False
        self.busy_kind = None
        self.cancel_btn.config(state="disabled")
        self._reset_progress()
        self._set_buttons_enabled(True)

    def _on_operation_error(self, exc):
        self._end_busy()
        if isinstance(exc, DownloadPaused):
            self.say("Download paused - press Build again to carry on.", COLOR_BAD)
            mb_warning("Download paused", str(exc))
            return
        if isinstance(exc, CancelledError):
            self.say("Cancelled.", COLOR_BAD)
            mb_warning("Cancelled", str(exc))
            return
        self.say("Failed - see the message for details.", COLOR_BAD)
        msg = str(exc) or type(exc).__name__
        if not isinstance(exc, InjectError):
            msg = "Something unexpected went wrong:\n\n%s: %s" % (type(exc).__name__, msg)
        mb_error("Error", "%s\n\n(Technical details are in the log file: %s)" % (msg, LOG_FILE))

    def _finish_ok(self, status_text):
        self._end_busy()
        self.say(status_text, COLOR_OK)

    def _run_async(self, work_fn, on_success, initial_status, kind="job", cancellable=False, on_error=None):
        self.busy = True
        self.busy_kind = kind
        self.cancel_event.clear()
        self._set_buttons_enabled(False)
        self.cancel_btn.config(state="normal" if cancellable else "disabled")
        self.report_progress(None, initial_status)

        def worker():
            try:
                result = work_fn()
            except Exception as exc:
                if not isinstance(exc, (InjectError,)):
                    log.exception("Unexpected error during %s", kind)
                else:
                    log.warning("%s failed: %s", kind, exc)
                handler = on_error or self._on_operation_error
                self._ui_queue.put(("done", lambda e=exc, h=handler: h(e)))
            else:
                self._ui_queue.put(("done", lambda r=result: on_success(r)))

        thread = threading.Thread(target=worker, daemon=True)
        CURRENT_WORKER[0] = thread
        thread.start()

    def on_cancel(self):
        if not self.busy or self.cancel_event.is_set():
            return
        if self.busy_kind == "build":
            text = ("Stop the build?\n\nAny downloaded/verified ISO is kept for next time. The ISO being "
                    "built is left unfinished - just press Build again to redo it.")
        else:
            text = "Stop now?\n\nIf the image is still being written, the drive will be left half-written "
            "and won't boot until you write it again."
        if mb_yesno("Cancel?", text, icon="warning", default="no"):
            self.cancel_event.set()
            self.cancel_btn.config(state="disabled")
            self.say("Cancelling - tidying up, please wait...", COLOR_BAD)
            threading.Thread(target=kill_active_children, daemon=True).start()

    def on_close(self):
        if self.busy:
            warnings_by_kind = {
                "build": "The ISO is still being built. Quitting now stops it safely, but you'll need to "
                        "press Build again next time.",
                "write": "An image is still being written. Quitting now leaves the drive half-written and "
                        "unbootable.",
            }
            text = warnings_by_kind.get(self.busy_kind, "Still working. Quitting now may leave things "
                                                          "incomplete.")
            if not mb_yesno("Still working!", text + "\n\nQuit anyway?", icon="warning", default="no"):
                return
            self.say("Stopping safely - please wait...", COLOR_BAD)
            self.config(cursor="watch")
            self.update_idletasks()
            stop_background_work(timeout=45)
        self.destroy()

    # ---- write tab ------------------------------------------------------ #
    def on_browse_image(self):
        start = self.out_dir_var.get().strip() or PROJECT_DIR
        if not os.path.isdir(start):
            start = REAL_HOME
        path = filedialog.askopenfilename(parent=self, title="Choose the image to write", initialdir=start,
                                          filetypes=IMAGE_FILE_TYPES)
        if path:
            self.image_var.set(path)

    def refresh_targets(self, quiet=False):
        try:
            eligible, rejected = classify_write_targets(quiet=quiet)
        except Exception as exc:
            if not quiet:
                mb_error("Error", "Couldn't list drives: %s" % exc)
            return
        signature = [(t["disk"], t["size"], t["serial"], tuple(t["contents"])) for t in eligible]
        if quiet and signature == self._targets_signature:
            self.rejected_targets = rejected
            return
        self._targets_signature = signature
        previous = self._selected_target()
        self.write_targets = eligible
        self.rejected_targets = rejected
        values = [t["desc"] for t in eligible]
        self.target_combo.config(values=values)
        keep = None
        if previous:
            for i, t in enumerate(eligible):
                if t["disk"] == previous["disk"] and t["serial"] == previous["serial"]:
                    keep = i
        if keep is not None:
            self.target_combo.current(keep)
        elif len(eligible) == 1:
            self.target_combo.current(0)
        else:
            self.target_var.set("")
        self._show_target_details()

    def _auto_refresh_targets(self):
        try:
            if not self.busy:
                self.refresh_targets(quiet=True)
        finally:
            self.after(3000, self._auto_refresh_targets)

    def _selected_target(self):
        idx = self.target_combo.current() if hasattr(self, "target_combo") else -1
        if idx is None or idx < 0 or idx >= len(self.write_targets):
            return None
        return self.write_targets[idx]

    def _show_target_details(self):
        t = self._selected_target()
        if not self.write_targets:
            self.target_details.config(
                text="No suitable drive found. Plug in a %s USB stick (the list updates by itself), or "
                "click the link below to see why a drive isn't offered." % WRITE_TARGET_LABEL, fg=COLOR_HINT)
        elif t is None:
            self.target_details.config(text="Choose the drive to erase from the list above.", fg=COLOR_HINT)
        else:
            contents = "; ".join(t["contents"]) or "no partitions (blank or unformatted)"
            self.target_details.config(text="Currently on it (will be ERASED): %s" % contents, fg=COLOR_BAD)

    def on_why_not_listed(self):
        lines = ["Only removable drives (USB sticks, SD cards) sized %s, give or take, are offered - so an "
                "internal disk can never be wiped by mistake.\n" % WRITE_TARGET_LABEL]
        if self.rejected_targets:
            lines.append("Drives found but not offered:\n")
            lines += ["• %s\n     → %s" % (d, r) for d, r in self.rejected_targets]
        else:
            lines.append("No other drives were found at all. If your stick is plugged in, try a different "
                        "USB port.")
        mb_info("Why isn't my drive listed?", "\n".join(lines))

    def _confirm_image_write(self, path, target):
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            looks_ok, size, what = peek_image(path)
        except InjectError as exc:
            mb_error("Can't use this image", str(exc))
            return False
        except Exception as exc:
            mb_error("Can't use this image", "Couldn't read the image file: %s" % exc)
            return False
        finally:
            self.config(cursor="")

        if size is not None and size > target["size"]:
            mb_error("Image too big", "This image needs %s, but the selected drive only holds %s.\n\nUse a "
                    "bigger drive." % (human_size(size), human_size(target["size"])))
            return False
        if not looks_ok and not mb_yesno(
            "Is this the right file?",
            "'%s' doesn't look like a bootable disk image (there's no partition table at its start).\n\n"
            "Write it anyway?" % os.path.basename(path), icon="warning", default="no",
        ):
            return False

        contents = "\n".join("      " + c for c in target["contents"]) or "      (no partitions)"
        mounted = target["mounted"]
        msg = ("ALL DATA on this drive will be PERMANENTLY ERASED:\n\n      %s\n\nIt currently contains:\n%s\n%s\n"
              "It will get this image:\n      %s\n      (%s, %s)\n\nIs this definitely the right drive?"
              % (target["desc"], contents, ("\nOpen right now at: %s\n" % ", ".join(mounted)) if mounted else "",
                 os.path.basename(path), what, human_size(size) if size else "final size shown while writing"))
        if not mb_yesno("Sanity check 1 of 2", msg, icon="warning", default="no"):
            self.say("Write cancelled - nothing was changed.")
            return False

        dlg = ReallySureDialog(self, target)
        self.wait_window(dlg)
        if not dlg.confirmed:
            self.say("Write cancelled - nothing was changed.")
            return False
        return True

    def on_write(self):
        if self.busy:
            return
        path = self.image_var.get().strip()
        if not path:
            mb_error("No image chosen", "Choose the image file to write first (Build one on tab 1, or "
                    "Browse... to an existing one).")
            return
        if not os.path.isfile(path):
            mb_error("Image not found", "Can't find this file:\n\n%s" % path)
            return
        target = self._selected_target()
        if target is None:
            mb_error("No drive chosen", "Choose the drive to write to from the list first.\n\nIf yours isn't "
                    "there, click 'Why isn't my drive listed?'.")
            return
        if not self._confirm_image_write(path, target):
            return

        verify = self.verify_var.get()
        log.info("User confirmed writing %s to %s (%s)", path, target["disk"], target["desc"])

        def on_success(result):
            self._finish_ok("Image written to %s." % target["desc"])
            minutes = result["seconds"] / 60.0
            checked = "and verified" if result["verified"] else "(not verified)"
            mb_info("Done!", "%s was written %s to:\n\n      %s\n\nin %s.\n\nYour desktop may pop up windows "
                    "for the new partitions - that's normal, close them.\n\nBoot each Wyse 3040 from this "
                    "stick (usually F12/Esc at power-on), with ethernet plugged into a network with internet "
                    "access. It's fully unattended - walk away and it wipes the eMMC and installs by itself."
                    % (os.path.basename(path), checked, target["desc"],
                       "under a minute" if minutes < 1 else "%d minute%s"
                       % (round(minutes), "" if round(minutes) == 1 else "s")))
            self.refresh_targets(quiet=True)

        self._run_async(lambda: write_image(path, target, verify, self.say, self.report_progress,
                                            self.cancel_event), on_success, "Preparing to write...",
                        kind="write", cancellable=True)

    def on_add_launcher(self):
        try:
            dest = install_menu_launcher()
        except Exception as exc:
            mb_error("Couldn't add the shortcut", str(exc))
            return
        mb_info("Added", "'Wyse 3040 Klipper Injector' is now in your applications menu.\n\n(%s)" % dest)


# --------------------------------------------------------------------------- #
# Start-up
# --------------------------------------------------------------------------- #
def setup_logging():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        if os.geteuid() == 0 and REAL_UID != 0:
            os.chown(LOG_FILE, REAL_UID, REAL_GID)
    except OSError:
        logging.basicConfig(level=logging.INFO)


def relaunch_with_privileges():
    script = os.path.abspath(__file__)
    python = sys.executable or "python3"

    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("xhost"):
        subprocess.run(["xhost", "+SI:localuser:root"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    keep = {}
    for var in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
                "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES"):
        if os.environ.get(var):
            keep[var] = os.environ[var]
    if "XAUTHORITY" not in keep:
        default_xauth = os.path.expanduser("~/.Xauthority")
        if os.path.isfile(default_xauth):
            keep["XAUTHORITY"] = default_xauth
    env_args = ["env"] + ["%s=%s" % kv for kv in sorted(keep.items())]

    for elevator in ("pkexec", "kdesudo", "gksudo", "gksu"):
        if shutil.which(elevator) is None:
            continue
        cmd = [python, script] + sys.argv[1:]
        if elevator == "pkexec":
            cmd = env_args + cmd
        try:
            os.execvp(elevator, [elevator] + cmd)
        except OSError:
            continue

    _desktop_error(
        APP_TITLE,
        "This tool needs administrator rights to write to disks and install build tools, but no graphical "
        "password prompt (pkexec) was found.\n\nEither install it (sudo apt install pkexec - older "
        "versions: policykit-1), or run this in a terminal:\n\n    sudo python3 \"%s\"" % script,
    )
    sys.exit(1)


def _terminate(signum, _frame):
    try:
        log.warning("Received signal %d - cleaning up and exiting.", signum)
        stop_background_work(timeout=20)
        global_cleanup_safety_net()
        logging.shutdown()
    finally:
        os._exit(128 + signum)


def main():
    if "--version" in sys.argv[1:]:
        print("%s v%s (Built %s) (WORK IN PROGRESS - not yet verified to boot real hardware end-to-end)"
              % (APP_TITLE, APP_VERSION, __build_date__))
        return
    setup_logging()
    log.info("=== %s v%s starting (uid %d) ===", APP_TITLE, APP_VERSION, os.geteuid())
    if os.geteuid() != 0:
        relaunch_with_privileges()
        return  # pragma: no cover - execvp replaces this process on success

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _terminate)
        except (ValueError, OSError):
            pass

    try:
        app = WyseInjectorApp()
    except tk.TclError as exc:
        _desktop_error(APP_TITLE, "Couldn't open the program window (%s).\n\nIf your desktop uses Wayland, "
                       "open a terminal, run:\n    xhost +SI:localuser:root\nand then start the tool again."
                       % exc)
        sys.exit(1)

    missing = missing_required_tools()
    if missing:
        mb_error(APP_TITLE, "This computer is missing some standard system tools this program needs:\n\n"
                "    %s\n\nOn Ubuntu/Mint/Debian: sudo apt install util-linux mount" % ", ".join(missing))
        app.destroy()
        sys.exit(1)

    dialog = DisclaimerDialog(app)
    app.wait_window(dialog)
    if not dialog.accepted:
        log.info("Disclaimer declined - exiting.")
        app.destroy()
        return

    app.start()
    app.mainloop()


if __name__ == "__main__":
    main()
