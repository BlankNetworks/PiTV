# First hardware validation

Automated tests establish controller behavior with simulated responses. They cannot establish that a TV's firmware, remote, input switching or streaming applications work after cleanup.

Use one owned/authorized Android or Google TV for the first supervised run. Record model, Android version and firmware build without publishing serial numbers, account names, Wi-Fi details or Android IDs.

1. Install on a 64-bit Pi 4 and verify that the service starts again after a Pi reboot. Open the page from an iPhone/iPad. Verify the Pi still performs any existing jobs.
2. Enable debugging on the TV and verify its own approval/pairing flow. Confirm inspection reports the correct model/build and identifies the actual Home/input/keyboard services.
3. Inspect the proposed profile and its notes. Start with one known optional telemetry package. Keep the private backup and verify the disabled state independently on the TV.
4. Use Restore. Confirm that the package returns to its previous state and packages already disabled before the test stay disabled.
5. Repeat the reviewed selection. Check real Home, remote, HDMI, Live TV where present, network, casting/voice where used, streaming playback and a TV reboot.
6. Test automatic enrollment only after the supervised test. Verify that changed firmware or package inventory requires review. Turn automatic mode off and verify it does not start a new cleanup.
7. Close the TV's debugging interfaces after the one-time job. To restore later, enable and approve debugging again.

Only after this can this Pi/TV combination be called hardware-validated. Samsung/LG/Roku and other non-Android systems require separate research and implementation; successful Android tests say nothing about them.

## Additional v0.2 checks

- Replug a supported USB ADB device after the udev rule is installed; verify service-account access, TV authorization and the dedicated localhost ADB daemon. Test an unauthorized device without applying changes.
- Create a recipe only from a successful cleanup and successful exact restore, with real owner observations. A second matching unit may use it; changed firmware must wait for review. Simulated recipe tests do not provide these observations.
- Present the same TV over both USB and LAN, and then two distinct eligible TVs; verify deduplication and ambiguity handling before unattended use.
- Validate an upgrade and application rollback on a spare card or recoverable installation. Confirm saved keys, backups and access token survive and automatic mode remains off.
- Direct Ethernet/DHCP setup is not implemented in this package. Follow docs/DIRECT_LAN.md when completing it locally, preserving the management connection.
