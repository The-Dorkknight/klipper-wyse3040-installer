# How it works

Plain-English account of what this project actually does to your PC and to
a Wyse 3040 — what it reads, what it changes, and what it never touches.

## The two build paths, and what they share

There are two ways to produce `klipper-wyse3040-installer.iso`: the GUI
(`wyse_injector.py`) and the script (`setup.sh`, also what the GitHub
Actions workflow runs). Both do the exact same thing underneath — render a
few templates, download and patch a Debian netinst ISO — they just differ
in how you tell them what you want.

### On your build PC (the machine you run the GUI or `setup.sh` on)

1. **Renders templates.** `preseed.cfg.tmpl`, `overlay/options.env.tmpl`,
   and either `overlay/hostapd/hostapd.conf.tmpl` (AP mode) or
   `overlay/wpa_supplicant/wlan0.conf.tmpl` (station mode) get their
   `__PLACEHOLDER__` tokens filled in with your password, WiFi settings,
   country code, etc. Nothing is sent anywhere — this all happens locally.
2. **Downloads the official Debian netinst ISO** from
   `cdimage.debian.org` (checksum-verified against the published
   `SHA256SUMS`, resumable if your connection drops), and caches it so a
   rebuild doesn't re-download.
3. **Extracts it, copies the preseed file and the whole `overlay/`
   directory onto it**, patches its boot menu (isolinux **and** GRUB, so
   it works whichever path the Wyse actually boots) to start the install
   automatically with no prompts, and rebuilds it as a new bootable ISO
   with `xorriso`.
4. **Installs its own build dependencies** (`xorriso`, `p7zip-full`,
   `wget`, `rsync`, isolinux) via `apt` if they're missing — the only
   thing on your build PC this project ever installs.

Your build PC's own disks are never touched by this step — building the
ISO only reads/writes inside this project's own folder (`.build/` and the
output ISO/`credentials.txt`).

### Writing to USB (GUI's "Write to USB" tab, or Balena Etcher/`dd`)

The GUI's write step is the one part of this project that touches your
build PC's hardware directly: it lists your disks (`lsblk`), filters to
**only** removable, correctly-sized (1–64 GB), unmounted, non-critical
drives, makes you pick one and type its exact device name to confirm, then
opens it exclusively (`O_EXCL` + `flock`, so nothing else can be using it
at the same time) and writes the image sector-by-sector, optionally
reading it back afterward to verify the checksum matches. It will not
write to your build PC's own system disk — that's excluded by
construction, not just by a warning.

### On the Wyse 3040, during the unattended install

This is the part that **erases the unit's internal eMMC** the moment it
boots from the USB stick, without asking again:

1. Debian's own installer partitions the whole eMMC as one ext4
   partition and installs a minimal Debian 12 base system, plus
   `openssh-server` (so SSH is available from first boot).
2. At the end of that install, `preseed/late_command` copies the `overlay/`
   directory onto the freshly-installed system and runs
   `overlay/late-command.sh` inside it (via `in-target`), which:
   - installs build/runtime packages (`apt-get install`, with retries)
   - clones Klipper and Moonraker from their official GitHub repos and
     sets each up in its own Python venv, running as a new `print` user
   - downloads and unpacks your chosen web UI (Mainsail or Fluidd) from
     its official GitHub release
   - installs the WiFi backend you chose (hostapd+dnsmasq for AP mode, or
     wpa_supplicant for station mode) and the ethernet config
   - installs `nginx` to serve the web UI and proxy Moonraker's API
   - installs the one-shot boot-diagnostics service
   - optionally clones and installs Crowsnest (webcam streaming) — a soft
     failure here (network hiccup, etc.) only skips Crowsnest, it doesn't
     abort the whole install
   - optionally adds the KlipPlug plugin manager by running
     `overlay/klipplug-step.sh`: it takes KlipPlug from a copy baked into
     the installer if there is one, otherwise clones it from GitHub, then
     calls KlipPlug's own installer for not-yet-running systems, which
     writes the service, links it to start at boot and runs KlipPlug once
     to prove it starts. Any problem there undoes the step and only skips
     KlipPlug
   - derives a unique hostname/SSID suffix from the unit's ethernet MAC
     address, writes it to `/etc/hostname` and (in AP mode) the hotspot
     SSID, and records a small summary to `/etc/wyse-klipper-options`
   - disables predictable network interface naming and updates GRUB
3. The unit reboots into the finished appliance.

## What it never does

- Never phones home, collects telemetry, or sends anything about your
  build or your units anywhere except the plain HTTP/HTTPS requests
  needed to fetch Debian, Klipper, Moonraker, the web UI, and (optionally)
  Crowsnest and KlipPlug from their own official sources.
- Never touches any disk on your build PC other than the one you
  explicitly selected and confirmed by typing its device name.
- Never installs anything on your build PC beyond the short, fixed list of
  ISO-build tools named above.
- Never asks for credentials other than your own `sudo`/`pkexec` password
  (to let it write disks and install packages) and whatever you type into
  the Build tab yourself.

## Architecture at a glance

```
wyse_injector.py (GUI)  ──┐
                          ├──> renders templates ──> overlay/ (this build's secrets + static files)
setup.sh (script/CI)    ──┘           │
                                       ▼
                          fetch + extract Debian netinst ISO
                                       │
                                       ▼
                    copy overlay/ + preseed.cfg onto it, patch boot menu
                                       │
                                       ▼
                       xorriso -as mkisofs  →  klipper-wyse3040-installer.iso
                                       │
                         (flash to USB: GUI's Write tab, Etcher, or dd)
                                       │
                                       ▼
                    boot a Wyse 3040 from the USB stick (ERASES its eMMC)
                                       │
                                       ▼
        Debian preseed install  →  preseed/late_command  →  late-command.sh
                                       │
                                       ▼
        Klipper + Moonraker + web UI + WiFi + boot-diagnostics, running
```
