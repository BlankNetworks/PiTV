# Install PiTV on a Raspberry Pi

PiTV is an application for Raspberry Pi OS, not a replacement Pi firmware image.
This is the one-time setup. Later app and profile updates arrive from the public
PiTV GitHub releases without SSH, Git credentials, file transfers or button presses.

## 1. Prepare the Pi

1. Use Raspberry Pi Imager to install 64-bit Raspberry Pi OS Lite on the SD card.
   Set hostname to `pitv`, set username to `pi` if you want the standard
   `pi@pitv.local` SSH address, and enable SSH. Choose a unique password or an SSH
   key for this Pi; do not use a shared public password. If you
   will use Wi-Fi, enter that network's name and password in Imager. A wired router
   connection is simplest. Do not erase an existing Pi that has TV records or keys.
2. Boot the Pi with a reliable power supply and connect it to the same reachable
   local network as your phone and TV. The router should assign its address by DHCP.
3. Connect once from a computer with `ssh pi@pitv.local` if you chose user `pi`.
   If that name is not resolved, find the Pi's address in your router and use
   `ssh pi@IP`. Substitute your chosen user if it is not `pi`.
   Verify the SSH host key before accepting it.

For an existing Raspberry Pi OS installation whose hostname is different, set it
once with `sudo hostnamectl set-hostname pitv`, reboot, and reconnect. Do this only
if `pitv` is not already in use on that local network.

## 2. Install with one command

After SSH connects, paste this single command into the Pi's terminal:

```bash
f=$(mktemp) && curl -fsSL https://github.com/BlankNetworks/PiTV/releases/latest/download/pitv-install.py -o "$f" && sudo python3 "$f"
```

Or run it directly from your computer without first opening an interactive Pi shell:

```bash
ssh pi@pitv.local 'f=$(mktemp) && curl -fsSL https://github.com/BlankNetworks/PiTV/releases/latest/download/pitv-install.py -o "$f" && sudo python3 "$f"'
```

Use your actual SSH name or IP in that command if different. The Pi needs
`curl`, Python 3, `sudo`, and outbound HTTPS to GitHub. The command downloads
the installer asset from the **latest stable PiTV release**. That asset is built
with the release ZIP's exact version and SHA-256. It downloads the matching ZIP,
checks its hash and archive paths, then runs the bundled installer. It stops if
PiTV is already present; use the web interface for later updates. You can
inspect the downloaded `pitv-install.py` before running it if desired.

The installer adds the native ADB and Python dependencies, the PiTV service,
Avahi local-name discovery and hourly release checks. It disables idle sleep and
hibernation. The PiTV service starts on boot. The installer does not change SSH
accounts, passwords, keys or SSH service settings, so your original SSH login
continues to work. Keep the Pi powered and connected
to the internet during this first installation.

For a manual checkout instead, clone the current stable tag from the
[PiTV releases page](https://github.com/BlankNetworks/PiTV/releases) and run
`sudo bash scripts/install-pi.sh` from that checkout. The one-command path does
not require Git to be installed on the Pi.

## 3. Open the interface

On a phone or computer on the same reachable network, open `http://pitv.local/`.
There is no PiTV token. The interface has **Find TVs**, **Review packages**,
**Clean your TV**, **Restore**, and **Update PiTV**. Automatic TV cleanup
stays off until a supervised real-TV cleanup and restore have been verified.

If `.local` does not resolve, use `http://IP/` with the Pi's current address from
the router. The app follows changes among private IPv4 LAN ranges; it does not
change the router or create a Wi-Fi access point. mDNS names normally stay within
one local link. Guest-network isolation, VLAN/firewall rules, blocked multicast,
IPv6-only networks and captive portals can limit discovery or updates. No internet
prevents release checks and installation of new app versions, while local TV
operations can still work. A TV still must enable and authorize ADB debugging.
On a network larger than /24, automatic discovery scans the Pi's containing /24; use manual address
or a selected /24 for another range.

## 4. Future changes to PiTV

Change the source in the public repo, bump `tvcleaner/__init__.py` to a new patch
version (for example `0.2.5`), and put the matching heading first in
`RELEASE_NOTES.md`. Push to `main`. GitHub Actions tests and publishes the release
ZIP. The Pi checks for it after boot and about hourly, verifies the GitHub SHA-256
and release archive, then installs it with application rollback. No later `git
pull` is needed on the Pi. The **Update PiTV** card reports checks and failures.
The latest stable release is the trust source; protect write access to that repo.

No software can guarantee recovery from power loss or a damaged SD card during
an update. Retain a working SD-card backup and access to the Pi for exceptional
recovery. PiTV updates do not update Raspberry Pi OS or TV manufacturer firmware.
