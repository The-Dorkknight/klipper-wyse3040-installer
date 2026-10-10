# Third-party software

This project's own code (the templates, `wyse_injector.py`, `setup.sh`,
`overlay/late-command.sh`, `overlay/wyse-diag.sh`, and the rest of
`overlay/`) is licensed under the PolyForm Noncommercial License 1.0.0 —
see [LICENSE.md](LICENSE.md).

That licence covers **only this project's own code**. Every unit built from
this project downloads and runs a number of other people's software,
unmodified except where noted, and each of those keeps its own licence.
This project claims no affiliation with, and is not endorsed by, any of the
projects below.

| Component | What it's used for | Project | Licence |
|---|---|---|---|
| Debian | Base operating system | [debian.org](https://www.debian.org/) | Mostly DFSG-free (GPL, etc.); see Debian's own licensing pages |
| Klipper | 3D-printer motion control (`klippy`) | [Klipper3d/klipper](https://github.com/Klipper3d/klipper) | GPLv3 |
| Moonraker | Klipper's API/web-server companion | [Arksine/moonraker](https://github.com/Arksine/moonraker) | GPLv3 |
| Mainsail | Web UI (one of two choices) | [mainsail-crew/mainsail](https://github.com/mainsail-crew/mainsail) | AGPLv3 |
| Fluidd | Web UI (the other choice) | [fluidd-core/fluidd](https://github.com/fluidd-core/fluidd) | AGPLv3 |
| Crowsnest | Optional webcam streaming | [mainsail-crew/crowsnest](https://github.com/mainsail-crew/crowsnest) | AGPLv3 |
| KlipPlug | Optional plugin manager (same author as this tool, but a separate project) | [The-Dorkknight/klipplug](https://github.com/The-Dorkknight/klipplug) | AGPLv3 |
| hostapd / dnsmasq / wpa_supplicant | WiFi hotspot / station-mode backend | Debian packages | Various (mostly BSD/GPL) |
| nginx | Serves the web UI, proxies Moonraker | Debian package (`nginx-light`) | BSD-2-Clause |
| systemd, avahi-daemon, zram-generator, etc. | Base OS services | Debian packages | Various |

Links go to each project's own repo rather than quoting their licence text
here, since that text can change — always check the source for the current
terms.

## Hardware and reverse-engineering notes

The Wyse 3040's boot quirk (`grub-installer/force-efi-extra-removable`) and
its known WiFi driver limitations in AP mode are documented, publicly
known issues in the Wyse-3040 hobbyist/homelab community — not something
reverse-engineered specifically for this project. No vendor tools,
firmware, or proprietary documentation were used in building this.
