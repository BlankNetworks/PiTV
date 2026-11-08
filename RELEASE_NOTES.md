# 0.2.1 PiTV release

Prepared November 7, 2026.

- Guided `pitv.local` interface and full advanced controls, both usable without a token on a trusted LAN.
- Manual cleanup remains reviewed and reversible; automatic cleanup is off by default.
- **Check for PiTV updates** button checks only stable `BlankNetworks/PiTV` releases. A confirmed update downloads the exact versioned ZIP, checks GitHub's published SHA-256, and uses the existing rollback-capable local updater. This updates the app and pinned debloat profiles, not TV firmware.
- Raspberry Pi ADB daemon startup fix uses the private loopback port 5038.
- No physical TV cleanup or restore has been validated yet.

---

# 0.2.0 development release

Prepared November 7, 2026.

## Delivered

- Headless service checks about every 15 seconds, with connected-network discovery and optional USB ADB discovery.
- Exact firmware recipes created from a successful cleanup and exact restore, with explicit owner hardware checks; recipes can apply to another matching unit after TV authorization. No preapproved hardware recipes are bundled.
- Stable target deduplication, multiple-target review, failed-attempt blocking and durable appliance status.
- Strict USB endpoints, a dedicated loopback ADB server, a narrow service-account USB permission rule, and an adapter for the unchanged upstream tools.
- Phone controls for automatic mode, recipe creation/toggling and redacted support reports.
- Reviewed release ZIP/SHA-256 updater with state/key preservation, automation pause, application rollback and interrupted-update recovery.
- A complete Mac-local Codex handoff in docs/CODEX_HANDOFF.md.

## Verification completed

`python3 -m unittest discover -s tests -q`

**263 tests passed** in the Linux development workspace. The suite covers catalog/policy behavior, network and USB transport, discovery, recipes, headless selection, recovery, concurrency, HTTP behavior and release/update transactions.

The USB lifecycle fixture executes the actual pinned upstream recovery, disable and restore code plus the actual USB bridge against a subprocess fake ADB device. This is integration coverage with simulated device state, not a physical TV test.

Python compilation, shell syntax, browser JavaScript syntax and static UI element-reference checks passed. The release archive is also inspected for required files, pinned upstream integrity and unsafe paths before handoff.

## Remaining work and limits

- Direct Pi-to-TV Ethernet addressing/DHCP is **not implemented**. docs/DIRECT_LAN.md describes the guarded local implementation and required physical-network checks. Normal same-LAN discovery is implemented.
- Actual Pi installation, reboot, USB permissions, network/TV authorization, mobile browser interaction and supervised cleanup/restore remain to be tested locally.
- A real owner-verified firmware recipe is required before new-unit unattended cleanup. Catalog entries alone are not automatic approval.
- TVs must support and authorize debugging. USB sockets alone do not imply USB ADB support; integrated Fire TV Smart TVs document network ADB only.
- Updates are operator-requested and checksum-verified, not signed OTA. Application rollback does not remove newly installed system packages.
- Current Home is preserved. No universal TV support, non-Android mutation adapters, automatic launcher takeover or guarantee of removing all streaming-app advertisements is included.

The chat workspace did not SSH into or modify the user's Mac, Pi or TV. Importing chat text does not transfer these source files. Download/extract the source bundle and open it in local Codex.

---

# 0.1.0 development release

Prepared November 4, 2026.

## Delivered

- Complete pinned CleanYourTV source and catalog: 433 metadata entries, 441 list variants, 114 brands, 1,764 distinct active package names.
- Conservative model/profile suggestions with source notes, explicit variant selection and strict malformed-list rejection.
- Native ARM64 ADB installation path for Raspberry Pi OS, bounded private IPv4 discovery and wireless-debugging pairing.
- Phone control page with access token, discovery, inspection, package review, cleanup history, restoration and enrolled-device automatic mode.
- Durable per-run snapshots, package lists and archived upstream recovery records; per-batch verification and rollback.
- Device/firmware binding across changes and restoration, including a new private IP or TLS debugging port.
- Cross-process mutation locks, same-origin/authentication checks, protected package policy, source integrity checks and power-loss recovery state.
- Separate demo, command-line interface, systemd installer and installation/development guides.

## Verification completed

`python3 -m unittest discover -s tests -q`

**135 tests passed** in the Linux development workspace. Suites cover catalog parsing and matching, discovery, ADB transport, core/feature preservation, lifecycle/recovery, concurrency/revocation, authenticated HTTP behavior and the full simulated API workflow.

The non-demo lifecycle fixture runs the actual pinned upstream `recovery.save`, `load_record` and `restore_commands` logic against a simulated TV. TV process execution is replaced for these tests; no real device was modified.

Python compilation, Bash syntax and browser JavaScript syntax checks passed. UI element IDs and event/label references were checked.

## Verification still required

- Physical Raspberry Pi 4 installation, boot and permissions checks.
- A supervised real TV cleanup, restoration and remote/input/app tests.
- Interactive mobile browser check. The attempted Playwright Chromium download was invalid/truncated, so a browser executable was unavailable. HTTP integration and JavaScript syntax checks are not a replacement for this check.

## Deliberate scope

- No unattended cleanup of an unseen TV based only on a guessed model score.
- No automatic launcher replacement or prebuilt launcher APK in this release. Current Home is preserved.
- No Tizen, webOS, Roku, VIDAA, SmartCast or Vega OS mutation adapters.
- No DNS filtering or promise to remove every in-app advertisement.
- No public deployment or remote Pi installation performed.

The source remains an independent companion to CleanYourTV. Its original tools and protected-package/Home rules were not modified.
