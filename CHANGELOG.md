# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) once the project leaves WIP
status; until then, expect breaking changes between `0.x` releases.

## 0.2.0.dev0 - 2026-10-10

First published version.

## [0.2.0-wip] — unreleased

**Work in progress — still not verified on real Wyse 3040 hardware.**

### Added
- Optional **KlipPlug** plugin manager (experimental; a separate project
  under AGPL-3.0): a tick box on the Build tab, `KLIPPLUG=1` for
  `setup.sh`, and a `KLIPPLUG` secret for the GitHub Actions build. Units
  get it from a copy baked into the ISO (a `klipplug` folder or zip placed
  next to the tools at build time) or clone it from GitHub during install.
  Runs as its own service on port 7130. A unit that can't get or install it
  still completes its install.
- `overlay/klipplug-step.sh`, the install step for it, kept separate so it
  can be run and tested on its own.
- Boot diagnostics and `/etc/wyse-klipper-options` report whether KlipPlug
  is installed.
- First automated tests (`tests/`), covering this option.
- Click-and-launch launchers: `WyseInjector-Linux.sh` (finds Python 3 +
  Tkinter itself and says what to install if either is missing),
  `WyseInjector-Windows.bat` and `WyseInjector-Mac.command` (this tool
  needs real Linux disk/build tooling, so these explain that plainly and
  point to WSL2 / a Linux VM instead of pretending to work). Tests that all
  three exist and are the right kind of file.
- Version and build-timestamp display in the GUI's window title, a small
  persistent label in its status bar, the About box, and `--version`.
- `setup.py` and `requirements.txt` for anyone who'd rather `pip install .`
  it than run it in place (the tool itself has zero third-party
  dependencies — standard library only).
- A just-for-fun "Wyse-Pi" photo in the README — pastry, not hardware,
  clearly labelled as such.

### Not tested
- The KlipPlug option was run on a PC against a stand-in for the installed
  system (a chroot), not inside a real Debian installer and not on a Wyse
  3040.
- `WyseInjector-Linux.sh` was run for real on Linux. `WyseInjector-Windows.bat`
  and `WyseInjector-Mac.command` were written and read carefully but not run
  on actual Windows or macOS — there was neither here to test on.

## [0.1.0-wip] — unreleased

**Work in progress — not yet verified to boot real Wyse 3040 hardware
end-to-end.**

### Added
- Point-and-click GUI (`wyse_injector.py`), built on the same disk-safety
  and download machinery as the sibling Pi Hotspot Injector project: Build
  ISO / Write to USB / Extras tabs, resumable checksum-verified downloads,
  two-step destructive-write confirmation, post-write verification.
- WiFi station/client mode as an alternative to the default AP/hotspot
  mode, selectable per build.
- Choice of Mainsail or Fluidd as the web UI.
- Optional Crowsnest webcam streaming (soft-fails without aborting the
  whole install if it can't be installed).
- Boot-diagnostics report served at `http://<unit>/diagnostics.txt`
  (there's no boot partition to pull out and read on a soldered eMMC).
- WiFi regulatory country code is now a build-time setting, validated
  against ISO 3166, instead of being hard-coded.
- Retry/exponential-backoff around network-dependent steps in
  `late-command.sh` (package installs, git clones, web UI download) so a
  transient network blip doesn't abort an otherwise-good unattended
  install.
- `setup.sh` and the GitHub Actions workflow updated to expose the same
  new options (WiFi mode, web UI, Crowsnest, country code, hostname
  prefix) via environment variables / repo secrets.

### Fixed
- `.gitignore` now also excludes the two newly-rendered files that can
  carry secrets (`overlay/options.env`, `overlay/wpa_supplicant/wlan0.conf`).
- A stray junk directory and a missing systemd device-wait override on
  `dnsmasq.service` (mirroring one already present on `hostapd.service`)
  from an early draft.

## [0.0.1] — initial draft

- First working version: preseed-based unattended Debian 12 installer for
  the Wyse 3040, AP-mode WiFi hotspot + ethernet, Klipper + Moonraker +
  Mainsail, built via `setup.sh` locally or through GitHub Actions.
