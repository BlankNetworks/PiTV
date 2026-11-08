# Next validation and development work

Version 0.2 implements roaming LAN/conditional USB discovery, owner-attested firmware recipes, unattended orchestration, explicit direct-Ethernet setup and reviewed local release updates.

1. Install on a physical Pi 4 and verify USB udev access, dedicated ADB keys, service reboot startup and a real mobile browser.
2. Perform a supervised one-package cleanup, reboot/household feature checks and exact restore. Only then approve the first hardware recipe.
3. Test direct Ethernet with an isolated port and each TV's actual DHCP/ADB behavior.
4. Test application update failure and rollback on a spare card before relying on unattended field use.
5. Add reviewed model/firmware recipes from hardware evidence. Firmware updates require fresh evidence.
6. Establish a maintained repository and signed release distribution before unattended online updates.
7. Add platform-specific adapters for non-Android TV operating systems only where supported management interfaces exist.
8. Validate launcher handover and physical remote behavior separately; keep automatic launcher replacement out of current unattended recipes.

No physical Pi/TV or direct-cable validation has been performed in the cloud development environment.
