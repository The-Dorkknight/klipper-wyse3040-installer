# Security policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security concern.

Instead, use GitHub's private vulnerability reporting for this repo:
**Security tab → Report a vulnerability**. This lets us discuss and fix the
issue privately before it's public.

If that's not available for some reason, open a normal issue asking to be
contacted privately, without including exploit details.

## Scope and expectations

This is a hobby project, provided AS IS with no warranty (see
[LICENSE.md](LICENSE.md)) and maintained on a best-effort basis by one
person. There's no SLA on response time, but genuine security reports
(credential handling, unsafe defaults, a unit shipping with something more
exposed than intended) will be taken seriously and fixed when possible.

Worth knowing already, not a vulnerability to report:

- Every unit built from the same ISO shares the same login password and
  (in AP mode) the same WiFi passphrase — this is by design, documented in
  the README, and is exactly why `credentials.txt` and the build repo
  itself should be kept private.
- The default `moonraker.conf` trusts a broad range of private IP space
  (see "Known limitations" in the README) so it works out of the box on
  whatever network you put a unit on — this is a deliberate usability
  trade-off, documented, not an oversight.
- This tool runs as root and writes directly to disks — that's inherent to
  what it does (imaging installer media), not itself a bug.
