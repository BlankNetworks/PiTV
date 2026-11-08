# 0.2.8 PiTV release

Prepared November 8, 2026.

- The **Wi-Fi relay** and **PITV network** sections can each be collapsed from their buttons under the version cards.
- USB Wi-Fi scan retries when NetworkManager first returns no networks and shows a local result message. A typed SSID can now connect even when the scan list is empty or the network is hidden. The page shows connection success or failure beside **Connect USB Wi-Fi**.
- The installer now starts the default `PITV` / `pitv12345` access point on both fresh installs and updates when built-in Wi-Fi is idle. An active built-in Wi-Fi connection is preserved. The access point autoconnects on later boots.
- A physical adapter may still need a working Linux driver and support for the upstream Wi-Fi band. This change does not claim physical relay validation or enable automatic TV cleanup.

---

# 0.2.7 PiTV release

Prepared November 8, 2026.

- A new **Wi-Fi relay** tab under the version cards scans nearby networks through a separate USB Wi-Fi adapter and connects it to the TV's Wi-Fi. A **PITV network** tab beside it starts the built-in access point and lets the owner change its SSID and password. The default is `PITV` / `pitv12345`.
- NetworkManager's shared IPv4 mode provides DHCP, DNS and NAT to phones on the Pi access point. The Pi scans the USB adapter's local subnet for TVs, then uses the existing supervised cleanup and restore flow. This is routed sharing, not a transparent bridge.
- Fresh installation starts the access point only when built-in Wi-Fi is idle; release upgrades preserve existing network connections until the owner starts the access point in the page. Ethernet is never reconfigured by this feature. Wi-Fi credentials are handled by a separate root worker and stored in root-only NetworkManager profiles.
- The relay has automated and demo tests, but has not yet been validated on a physical USB adapter, TV and phone together. Automatic TV cleanup stays off.

---

# 0.2.6 PiTV release

Prepared November 8, 2026.

- The single-command SSH installation example allocates a terminal so `sudo` can ask for the Pi's own password on a fresh setup.

---

# 0.2.5 PiTV release

Prepared November 8, 2026.

- The public GitHub About section now describes PiTV, links to the installation guide and tags the Raspberry Pi, Android TV and CleanYourTV project topics.
- Every stable PiTV release now includes a small `pitv-install.py` asset. One pasted command downloads that asset on a fresh 64-bit Pi, verifies the matching release ZIP by SHA-256 and safe archive paths, and runs the existing installer. Later updates still arrive automatically through the PiTV release checker.
- The first-install guide and README show the single command and a direct SSH variant. The installer preserves the owner's SSH and network setup and stops if PiTV is already present.

---

# 0.2.4 PiTV release

Prepared November 8, 2026.

- The guided and advanced pages now show separate PiTV app and pinned CleanYourTV profile versions, with responsive cards that stack on phones.
- The top of each page credits and links to the CleanYourTV project. The pinned profile snapshot is v3.4.0 at commit `fc148d5a9707b30e8f49f489625947421740dc55`.
- The scan scope defaults to ALL connected local private networks and USB, with bounded /24 scans. The final reviewed action is labeled **Clean your TV** and explains the restore record, selected changes and checks in plain English. No token is needed.
- No real TV cleanup has been validated yet. The user must still enable and authorize debugging, select a TV, and review its suggested packages before pressing the final button.
- A daily GitHub Actions check opens a review pull request when CleanYourTV publishes a new stable version. It does not merge or deploy upstream changes automatically. The installation guide uses `pi@pitv.local` when the owner selected user `pi` and preserves that SSH setup.

---

# 0.2.3 PiTV release

Prepared November 8, 2026.

- The Pi checks stable `BlankNetworks/PiTV` releases after boot and about hourly. It requests a newer release through the existing checksum-checked, rollback-capable updater. Three failed attempts for one version pause further automatic attempts until a newer release or manual review.
- A GitHub Actions workflow tests and packages a versioned main-branch commit, then publishes the ZIP as a stable release. Bump the version and first release-notes heading with each publishable source update.
- Fresh installation serves the browser page on port 80. Set the Pi hostname to `pitv` during initial OS setup for `http://pitv.local/`.
- Automatic PiTV app updates are separate from automatic TV cleanup, which stays off after an update. The TV catalog and cleanup rules are unchanged.

---

# 0.2.2 PiTV release

Prepared November 8, 2026.

- The Pi installer now keeps the appliance awake: systemd idle action is `ignore`, all four sleep modes are disabled, and sleep targets are persistently masked. The next PiTV update applies this policy. Deliberate reboot and shutdown remain available.
- No TV catalog or cleanup policy changes. Automatic cleanup remains off after updating.
- This policy cannot prevent a power loss, thermal protection shutdown, or another privileged program from deliberately powering off the Pi.

---

# 0.2.1 PiTV release

Prepared November 7, 2026.

- Guided `pitv.local` interface and full advanced controls, both usable without a token on a trusted LAN.
- Manual cleanup remains reviewed and reversible; automatic cleanup is off by default.
- **Check for PiTV updates** button checks only stable `BlankNetworks/PiTV` releases. A confirmed update downloads the exact versioned ZIP, checks GitHub's published SHA-256, and uses the existing rollback-capable local updater. This updates the app and pinned debloat profiles, not TV firmware.
- Raspberry Pi ADB daemon startup fix uses the private loopback port 5038.
- Pins the CleanYourTV v3.4.0 source and debloat catalog: 442 models, 450 usable profiles and 117 brands. Existing owner-approved exact firmware recipes tied to the older source commit will need new supervised validation before automatic use.
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
