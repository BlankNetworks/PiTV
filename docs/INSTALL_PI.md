# Install PiTV on a Raspberry Pi

PiTV is an application for Raspberry Pi OS, not a replacement Pi firmware image.
This is the one-time setup. Later app and profile updates arrive from the public
PiTV GitHub releases without SSH, Git credentials, file transfers or button presses.

## 1. Prepare the Pi

1. Use Raspberry Pi Imager to install 64-bit Raspberry Pi OS Lite on the SD card.
   Set hostname to `pitv`, create your own user/password, and enable SSH. If you
   will use Wi-Fi, enter that network's name and password in Imager. A wired router
   connection is simplest. Do not erase an existing Pi that has TV records or keys.
2. Boot the Pi with a reliable power supply and connect it to the same reachable
   local network as your phone and TV. The router should assign its address by DHCP.
3. Connect once from a computer with `ssh YOUR_USER@pitv.local`. If that name is
   not resolved, find the Pi's address in your router and use `ssh YOUR_USER@IP`.
   Verify the SSH host key before accepting it.

For an existing Raspberry Pi OS installation whose hostname is different, set it
once with `sudo hostnamectl set-hostname pitv`, reboot, and reconnect. Do this only
if `pitv` is not already in use on that local network.

## 2. Install from the public repo

Run these commands in the Pi's terminal, replacing `v0.2.3` with the current
stable tag shown on the [PiTV releases page](https://github.com/BlankNetworks/PiTV/releases):

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --depth 1 --branch v0.2.3 https://github.com/BlankNetworks/PiTV.git
cd PiTV
sudo bash scripts/install-pi.sh
```

The installer adds the native ADB and Python dependencies, the PiTV service,
Avahi local-name discovery and hourly release checks. It disables idle sleep and
hibernation. The PiTV service starts on boot. Keep the Pi powered and connected
to the internet during this first installation.

## 3. Open the interface

On a phone or computer on the same reachable network, open `http://pitv.local/`.
There is no PiTV token. The interface has **Find TVs**, **Review packages**,
**Clean selected packages**, **Restore**, and **Update PiTV**. Automatic TV cleanup
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
version (for example `0.2.4`), and put the matching heading first in
`RELEASE_NOTES.md`. Push to `main`. GitHub Actions tests and publishes the release
ZIP. The Pi checks for it after boot and about hourly, verifies the GitHub SHA-256
and release archive, then installs it with application rollback. No later `git
pull` is needed on the Pi. The **Update PiTV** card reports checks and failures.
The latest stable release is the trust source; protect write access to that repo.

No software can guarantee recovery from power loss or a damaged SD card during
an update. Retain a working SD-card backup and access to the Pi for exceptional
recovery. PiTV updates do not update Raspberry Pi OS or TV manufacturer firmware.
