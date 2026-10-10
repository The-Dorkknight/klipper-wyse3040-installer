# Contributing

Issues and pull requests are welcome, especially:

- **Real-hardware test reports** — this project is work in progress and
  hasn't been verified to boot a real Wyse 3040 end-to-end yet. If you try
  it, a report (what worked, what didn't, unit variant, WiFi
  hardware used) is genuinely the most valuable contribution right now.
- Bug fixes, especially around the boot-menu patching (Debian occasionally
  reshuffles `isolinux`/GRUB file layouts between releases) and WiFi driver
  quirks on different hardware.
- Documentation fixes/clarifications.

## Before you open a PR

- Run the automated tests (no USB stick, root or network needed):
  `python3 -m unittest discover -s tests`.
- Keep changes focused — one fix or feature per PR is easier to review
  than a bundle of unrelated ones.
- If you change `overlay/late-command.sh`, `setup.sh`, or `wyse_injector.py`,
  run `bash -n <file>` / `python3 -m py_compile wyse_injector.py` first —
  a syntax error there breaks every unit's unattended install silently.
- Update `CHANGELOG.md` under an `[Unreleased]` heading.
- If your change touches what the built system does (packages installed,
  services enabled, network behaviour), update `HOW_IT_WORKS.md` too.

## Licensing of contributions

By submitting a contribution, you agree it's licensed under the same terms
as the rest of the project — the PolyForm Noncommercial License 1.0.0 (see
[LICENSE.md](LICENSE.md)). Don't submit code you don't have the right to
license this way.

## Reporting bugs

Use the bug report issue template — it asks for the information that's
actually needed to reproduce a build or boot problem (which build path,
Wyse 3040 variant, WiFi hardware, and the relevant log output or
`diagnostics.txt`).

## Security issues

Please don't open a public issue for a security concern — see
[SECURITY.md](SECURITY.md) instead.
