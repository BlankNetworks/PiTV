# Direct Ethernet: pending implementation

**Status: design and handoff only.** This release does not configure an isolated Ethernet network or provide a direct-LAN setup command. No DHCP service, NetworkManager profile or routing change has been installed or tested on a Raspberry Pi for this feature.

The normal supported connection is the Pi and TV on the same reachable home network. Connecting the Pi's Ethernet socket directly to a TV is a different mode: both devices need compatible addresses, and the TV still needs an enabled, authorized network debugging service. A cable alone does not provide those requirements.

## Intended operation

After one deliberate setup on the Pi, one designated Ethernet interface would become an isolated TV-facing port. NetworkManager would assign the Pi `10.77.0.1/24` on that port and provide DHCP to the connected television. The profile would reconnect automatically when the cable is attached, so the configured appliance could run headlessly afterward.

The operator must retain another way to manage the Pi during setup: Wi-Fi, a separate USB Ethernet adapter, or a physically attached console. The same physical port must not simultaneously serve as the home's ordinary LAN uplink and the TV's DHCP server.

The existing application would then discover eligible network ADB endpoints on the isolated subnet, wait for the TV's normal debugging authorization if needed, and apply only a matching enabled cleanup recipe through the existing guarded service.

## Required local Codex implementation

Create a separate, explicitly invoked setup helper. Do not call it automatically from the application installer, initial boot or network discovery. A prospective interface is:

```text
sudo bash scripts/configure-direct-lan.sh --interface eth0 --isolated-tv-port
```

**That script does not exist yet. This is the proposed interface for implementation, not a command to run in this release.**

The helper must first offer a read-only preflight/dry-run and then enforce all of these conditions before writing network configuration:

1. Validate the interface name and confirm that it is a physical Ethernet interface managed by NetworkManager. Refuse Wi-Fi, bridges, bonds, VLANs, virtual interfaces and an unidentifiable device.
2. Require the operator's explicit declaration that this is an isolated TV-facing port. Do not try every interface, guess based on a cable connection, or turn on DHCP on the normal home LAN.
3. For first-time configuration, require an unused, disconnected port with no existing IPv4 address or active connection. An administratively down interface is not proof that its cable is disconnected; collect reliable link/carrier evidence or refuse.
4. Refuse an interface carrying a default route or the current SSH connection, considering both IPv4 and IPv6. Preserve the existing management route and require another usable management interface or explicit physical-console use.
5. Check the proposed subnet against all current interface addresses and non-default routes. Refuse overlapping networks rather than replacing them. If a different private subnet is supported, validate it and keep discovery bounded.
6. Preserve existing NetworkManager profiles, hostname, SSH settings, unrelated services and TV recovery records. Never delete or rewrite an unrelated profile to make setup succeed.
7. Create only an identifiable application-owned profile, such as `tvcleaner-direct`, bound to the selected interface and physical adapter. Give it an ownership marker. A same-name profile without that marker must cause refusal.
8. Configure shared IPv4 on that interface, `ipv4.never-default=yes`, disabled IPv6, and controlled autoconnect behavior. Keep the DHCP/DNS service confined to the isolated interface. Do not run a second global DHCP server.
9. Stage the profile with autoconnect disabled, verify every intended property, and only then enable autoconnect. Recheck the preflight immediately before changes. Handle failed or interrupted setup without leaving an unchecked server enabled.
10. Make repeated setup idempotent for an exactly matching owned profile. Treat drift or ambiguous ownership as an error. Provide removal by that owned profile's verified UUID, preserving unrelated profiles and the management path.

NetworkManager's shared mode also enables forwarding/NAT through an available uplink. The helper and its documentation must make that behavior explicit. It must not modify the home's router or assume that the TV requires internet access for the debloating operation.

## Required validation before release

Use mocked `ip` and `nmcli` responses to verify rejection of occupied interfaces, default/SSH routes, subnet overlap, malformed inputs, foreign profiles and unexpected configuration drift. Verify that dry-run executes no mutation commands and that every real command uses argument arrays rather than shell interpolation.

Physical validation must then cover a Pi 4, an isolated TV-facing adapter, a separate management connection, DHCP lease assignment, restart/link reconnection, profile removal and preservation of the existing home LAN. Test a representative TV that actually supports network ADB over its Ethernet interface. Do not count mocked tests as hardware verification.

## Remaining device limits

- A TV with a static address may need its network settings adjusted before it can use this subnet.
- Some firmware may expose wireless debugging only on Wi-Fi, or may stop a previous debugging connection when switching to Ethernet.
- Developer mode and the first debugging authorization remain TV-controlled actions.
- Neither DHCP nor a successful network connection proves that a cleanup recipe matches the TV's firmware.
- USB ADB is a separate transport and is available only where the TV exposes a genuine USB debugging interface.

## References

- [Raspberry Pi network configuration](https://www.raspberrypi.com/documentation/computers/configuration.html): DHCP and optional link-local fallback.
- [NetworkManager IPv4 settings](https://www.networkmanager.dev/docs/api/latest/settings-ipv4.html): shared-mode addressing and `never-default`.
- [NetworkManager settings for nmcli](https://www.networkmanager.dev/docs/api/latest/nm-settings-nmcli.html): shared-mode DHCP, DNS and NAT behavior.
- [Android Debug Bridge](https://developer.android.com/tools/adb): debugging authorization and network pairing.
