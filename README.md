# Universal TV Debloater for Raspberry Pi

A portable, local controller for the [CleanYourTV](https://github.com/Gathin23/cleanyourtv) catalog. Prepare a Raspberry Pi once, take it to a home, connect it to that home's network, and control cleanup from your phone or tablet.

**PiTV is in supervised rollout.** The controller, discovery, matching, web API and recovery flows have automated simulation tests. The Pi service has been installed and checked on a Raspberry Pi, but no physical television has been cleaned by this appliance yet. Start with the demo, then a supervised test on one TV. Catalog coverage is community source coverage, not a claim that every listed television has been tested with this controller.

## Included

| Item | This release |
| --- | --- |
| TV/model entries | 433 |
| Cleanup lists, including alternatives | 441 |
| Brands in the upstream catalog | 114 |
| Unique package names in active lists | 1,764 |
| Upstream revision | `04aec4b7c763ed642d3994d25e5b89dd57014a6d` |
| Control interface | Local phone/tablet browser, plus a terminal CLI |
| Discovery | Connected private IPv4 networks, wireless-debugging advertisements and USB ADB enumeration |
| Backups | Device properties, package states, settings and exact upstream restore records |
| Changes | Disable selected packages for Android user 0, in batches of at most five |
| Automatic visits | Enrolled devices or owner-verified exact firmware recipes |
| Local operation | No AI subscription, cloud service, telemetry or runtime catalog download |

All upstream lists, their alternate variants, notes and original tools are included under `vendor/cleanyourtv/`. The controller does not combine a manufacturer's different lists or try them successively against a television. It gathers evidence, ranks possible matches and uses the one the owner reviews.

## What "universal" means here

The Pi is the portable controller and catalog. It is not a universal firmware exploit or a device that can modify every television through an HDMI cable.

Android TV and Google TV are the primary targets. Compatible Fire OS devices can be inspected and cleaned where their firmware permits normal ADB package disabling. Samsung Tizen, LG webOS, Roku OS, VIDAA, SmartCast and Amazon Vega OS do not become compatible by loading more Android package lists. Those systems currently receive no changes from this application. See [compatibility](docs/COMPATIBILITY.md).

The TV must offer debugging access and the owner must approve the Pi on the TV. A phone can control the Pi; the TV remote is still needed for its approval screen and for checking normal operation afterward. Network client isolation or guest Wi-Fi can prevent connection even when both devices have internet access.

## Prepare the Pi

Use Raspberry Pi OS **64-bit**, preferably Lite, on a Pi 4. An existing suitable installation can be used; this installer does not reflash the SD card, change the Pi's hostname, reconfigure Wi-Fi or replace the router. Bring a proper Pi power supply. Ethernet to the home's router is the easiest way to get a headless Pi online.

Download and extract the source package on the Pi. You can transfer it using SFTP from a phone/tablet. From a terminal on the Pi, inside the extracted project folder:

```bash
sudo bash scripts/install-pi.sh
```

This installs Debian's native `adb`, Python and upstream Python dependencies; copies the project to `/opt/universal-tv-debloater`; creates the dedicated `tvcleaner` service account; and starts a systemd service on port 8080. Internet access is needed for this installation. The profiles and Python dependencies then remain local.

The generic installer prints an address on port 8080 and a legacy access token. This PiTV build does not ask for or require that token in either control page or API. On the configured PiTV appliance, open `http://pitv.local/` for the guided page or `http://pitv.local/advanced` for all controls. A fresh generic install opens:

```text
http://<your-pi-hostname>.local:8080
```

Use the Pi's numeric LAN address instead if `.local` discovery is unavailable. This is a local HTTP service for a trusted home network. Anyone who can reach the Pi on that network can operate the manual and advanced controls, including automatic mode. Do not forward its HTTP or ADB ports through the router.

## First visit to a television

1. Connect the Pi to power and the home's router. Keep the TV on the same reachable LAN.
2. Open the control page on your phone. No token is needed.
3. On the TV, enable developer options and the debugging option its firmware provides. Usually Build is pressed seven times in Settings → About. Menu names vary.
4. Scan the local subnet or enter the TV's private IPv4 address. Select the device and accept its debugging prompt. For wireless debugging, use **Pair** with the pairing port and six-digit code, then **Connect** with the separate connection port shown on the TV.
5. Inspect the TV. Review the suggested profile, its Android version and its model/firmware notes. A match score is a suggestion, not validation that the entire list is appropriate.
6. Review the packages. A small set of known telemetry/content-recognition packages is selected by default only when present in both the chosen list and the TV. Optional packages are not automatically selected. Core packages, active input and keyboard services, Home, setup, known updates/casting/voice services, user-installed apps and known streaming applications are protected.
7. Select **Clean selected packages**. The controller re-reads the TV, records its original state, applies bounded batches through upstream tools and verifies package states and Home. A failure stops further cleanup and attempts restoration.
8. Check Home, real remote buttons, HDMI/Live TV inputs, Wi-Fi, voice/casting and the apps the household uses. Turn debugging off after a one-time cleanup. You can take the Pi home; disabled packages remain on the TV until re-enabled or changed by an update/reset.

The preserved-package policy is intentionally conservative. This release does not provide a switch that bypasses upstream core/Home guards. Some vendor-specific services remain optional and still need their notes reviewed.

## Headless operation

Once configured, the Pi boots into its service and checks for an authorized TV about every 15 seconds. It can follow the active Ethernet/Wi-Fi network as you move between homes, discover USB ADB devices, or use USB-only mode. The phone page remains available for setup, status and recovery; a browser does not have to stay open for automatic operation.

```bash
sudo tvcleaner automatic on --network auto --policy recipes
sudo tvcleaner status
sudo tvcleaner automatic off
```

A model name in the catalog does not authorize every firmware version. Automatic execution accepts either an individually enrolled TV or an **owner-verified firmware recipe**. A recipe records the exact manufacturer, model, device, product, Android version, firmware fingerprint/build, system package inventory, Home and protections. Selected packages still pass the current package policy. A recipe can then cover another TV with matching firmware and state after that TV authorizes this Pi.

**This release contains no preapproved hardware recipes.** Create one from a supervised successful cleanup followed by a successful restore test. In Cleanup history, choose **Create firmware recipe**, then confirm the real Home, input, remote, streaming/casting, network, reboot and restore checks. These are owner attestations; the software cannot press the real remote or verify the picture for you. See [recipe requirements](docs/RECIPES.md).

An unknown firmware waits for review. Two eligible TVs pause automatic selection; the same TV exposed over USB and LAN is deduplicated. A failed attempt is blocked from automatic retries until its state is inspected and any required restore finishes. Use `sudo tvcleaner automatic unblock --endpoint <tv-endpoint>` to clear a reviewed failure. Automatic runs still save exact restore records before any changes.

Direct Pi-to-TV Ethernet addressing is pending implementation; see the [isolated Ethernet handoff](docs/DIRECT_LAN.md). A normal home-LAN connection needs no DHCP server on the Pi. USB works only when the TV exposes a genuine debugging-device interface; ordinary TV USB sockets do not promise this capability. Use `usb:<serial>` from discovery for an actual USB ADB target. ADB uses a dedicated localhost daemon on port 5038 and keeps its private authorization keys across application updates.

[Update instructions](docs/UPDATING.md) describe checksum-verified local release upgrades, preserved state and application rollback. Updates pause automatic mode until it is explicitly rearmed. A changed source list or upstream revision invalidates previous recipe applicability until reviewed again. To gather evidence for new model support, download **Support report** after inspection, or run:

```bash
sudo tvcleaner report <tv-endpoint>
```

The support report omits serial numbers, Android IDs, network addresses, settings and the user-installed app list. It does not approve a new cleanup recipe.

## Restore

Use **Cleanup history → Restore this run**, enter the TV's current debugging address, and reconnect with the TV's approval if required. The stable device and firmware identity must match the saved run, even if its IP address or wireless-debugging port changed.

Restoration uses the exact upstream record saved before the change. Missing or corrupted records stop the operation; the upstream fallback that enables every package is not allowed. The controller has a separate identity-only recovery probe so an unhealthy Home screen does not prevent a restore attempt while ADB remains reachable. Full verification follows restoration.

Private runtime records live under `/var/lib/tvcleaner/state/`. Keep that directory and its `home/.cleanyourtv` records with the appliance. Do not post it publicly: it includes device identifiers, properties, settings, screenshots and ADB credentials. The source package does not include runtime data.

If the TV cannot be reached over ADB, this tool cannot repair it remotely. Use the TV's own settings to re-enable packages when accessible, or its model-specific recovery procedure. The program does not flash firmware.

## Ads and the replacement home screen

This controller disables selected packages. It does not guarantee removal of every tracker or advertisements embedded in YouTube, Netflix or other streaming services. It does not configure DNS filtering in this release.

## Update PiTV

Use **Check for PiTV updates** on the main page to see the latest stable release
from the public `BlankNetworks/PiTV` repo. Review the release notes, then choose
**Install PiTV update**. The button updates the PiTV app and its pinned
CleanYourTV profile snapshot together. It does not install a TV manufacturer's
firmware. Automatic TV cleanup stays off after an app update. See
[updating](docs/UPDATING.md) for the release checks, recovery and manual ZIP path.

**This release preserves the current home screen.** The reference launcher source is included, but there is no prebuilt APK and no automatic launcher handover. The upstream Linux Android resource compiler has an x86-64 requirement, and some TV models need different Home/remote procedures. Those must be prepared and tested separately before adding them to unattended operation. See [launcher preparation](docs/LAUNCHER.md).

## Try the interface without a TV

Python 3.10 or newer is sufficient for the demo. No ADB or upstream dependencies are needed:

```bash
python3 -m tvcleaner --demo serve --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080` and try discovery, cleanup and restore. No token is needed. Every screen is labeled **Demo** and uses a simulated Xiaomi TV. Demo state is kept in a separate `demo` subdirectory. The simulated television resets when the demo process restarts; real televisions do not behave that way. Start fresh demo state for a clean new demo.

## Terminal commands

After installation, the CLI runs as the same dedicated account as the service:

```bash
sudo tvcleaner status
sudo tvcleaner scan 192.168.1.0/24
sudo tvcleaner connect 192.168.1.50:5555
sudo tvcleaner inspect 192.168.1.50:5555
sudo tvcleaner profiles
sudo tvcleaner runs
sudo tvcleaner automatic off
```

Example plan command, only when the inspected TV matches this model:

```bash
sudo tvcleaner plan 192.168.1.50:5555 xiaomi-mi-tv-4a/debloat.txt
```

Apply is explicitly scoped to a saved plan and selected packages:

```text
sudo tvcleaner apply <plan-id> --package <selected-package> --enroll
sudo tvcleaner restore <run-id> <current-tv-ip>:<debugging-port>
```

For a pairing-code TV, `sudo tvcleaner pair <ip>:<pairing-port>` prompts for the code without putting it into shell history. Then use `connect` with the connection port.

Service diagnostics:

```bash
sudo systemctl status tvcleaner --no-pager
sudo journalctl -u tvcleaner -n 50 --no-pager
```

## Development

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/install-pi.sh scripts/prepare-launcher.sh
```

See [architecture](ARCHITECTURE.md), [hardware validation](docs/HARDWARE_VALIDATION.md) and [next development work](docs/ROADMAP.md). The controller keeps an upstream file manifest and refuses changed pinned tools/lists before mutations. Update the upstream snapshot as a reviewed release; do not `git pull` new lists into a travelling appliance and retain old automatic approvals.

This is an independent companion project. Original CleanYourTV code and catalog are by Gathin and its contributors under MIT; the upstream license and typeface notice are preserved. New controller code is MIT. No upstream pull request or public repository was created by this package.
