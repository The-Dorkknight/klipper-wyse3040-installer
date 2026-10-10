# Publishing this repo

This folder is already a real, ready-to-publish git repo: commit identity
is set (`Matthew Armstrong <The-Dorkknight@users.noreply.github.com>`), the
working tree is clean, and the `v0.2.0-wip` tag points at the latest
commit. Nothing to configure by hand.

## Publish it

1. Open **GitHub Desktop → Add Local Repository**, point it at this folder.
2. Click **Publish repository** (keep it Public to match the licence/README
   wording, unless you want it private for now).

That's it — README, screenshots, licence, issue templates and the
`v0.2.0-wip` tag all go up together as one push.

## A few things worth doing afterwards (all in the GitHub web UI, no terminal)

- **Mark it a pre-release.** Repo page → Releases → the `v0.2.0-wip` tag →
  Edit → check **"Set as a pre-release"**. While this stays WIP, that's the
  honest label.
- **Turn on the project page.** Settings → Pages → Source: Deploy from a
  branch → `main` / `/docs`. It'll be live at
  `https://The-Dorkknight.github.io/klipper-wyse3040-installer/` a few
  minutes later.
- **GitHub Actions secrets (optional).** Only needed if you want the CI
  build to use fixed credentials/options instead of fresh random ones each
  run: Settings → Secrets and variables → Actions → add any of
  `PRINT_PASSWORD`, `WIFI_PASSPHRASE`, `COUNTRY`, `WIFI_MODE`,
  `STATION_SSID`, `STATION_PASSWORD`, `WEBUI`, `CROWSNEST`, `KLIPPLUG`,
  `HOSTNAME_PREFIX`. All optional; unset ones use `setup.sh`'s defaults.

## Adding real hardware photos later

The README and the project page both have a spot for photos of an actual
running unit, hidden until you add them:

| File | What it should show |
|---|---|
| `docs/images/unit.jpg` | A Wyse 3040 unit running this project |
| `docs/images/unit-2.jpg` | The same unit, another angle |
| `docs/images/real-feed.jpg` | Mainsail/Fluidd actually running on a real unit |

Resize to ~1600px wide and strip EXIF/GPS first (`exiftool -all= file.jpg`),
drop them into `docs/images/`, uncomment the matching block in `README.md`
(search for "Real hardware photos"), commit, push. The project page
(`docs/index.html`) picks them up automatically — it probes for those three
files and only shows the section if at least one exists.

## Never commit

`credentials.txt`, `preseed.cfg`, `overlay/hostapd/hostapd.conf`,
`overlay/wpa_supplicant/wlan0.conf`, `overlay/options.env`,
`overlay/klipplug/` — all gitignored already. A `git status` before any
manual `git add .` is good hygiene if you ever bypass the normal build
scripts.
