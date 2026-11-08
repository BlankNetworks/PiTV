# Compatibility

The controller reads the pinned CleanYourTV Android package catalog. It does not install operating systems or vendor firmware onto the Pi or TV.

| Platform | Current behavior |
| --- | --- |
| Android TV / Google TV | Network or genuine USB ADB inspection and selected package disabling, if every required state/identity probe succeeds and the firmware permits the change. |
| Amazon Fire OS | Same restricted ADB route where available; no Home replacement. Some models deny package disabling altogether. |
| Samsung Tizen | No cleanup adapter. Use native privacy settings; loading an Android list cannot modify Tizen. |
| LG webOS | No cleanup adapter. A separately designed authenticated webOS adapter would be a new implementation. |
| Roku OS | No cleanup adapter. A remote-control API is not a package-removal API. |
| Hisense VIDAA / Vizio SmartCast | No cleanup adapter. |
| Amazon Vega OS | No Android/ADB cleanup adapter. |
| Android phones/tablets | Rejected unless firmware reports a TV feature. |

The source project's non-Android guide is https://cleanyour.tv/not-android. The primary project documentation is https://github.com/Gathin23/cleanyourtv.

The scanner finds port 5555 and ADB TLS service advertisements. A port being open is only a connection hint, not proof of a TV or a supported model. TVs with debugging disabled, inaccessible guest networks, nonstandard ports without mDNS, IPv6-only configurations or firmware lacking required probes may need manual setup or remain inspection-only.

Profiles keep their own notes and alternative files. Similar retail names, shared board codenames and identical package inventories do not prove firmware compatibility. Automatic mode requires either an individually enrolled device or an owner-verified recipe with exact firmware and system-state matching. A fuzzy catalog score never authorizes unattended changes. No preapproved hardware recipes are bundled.

USB support requires a TV debugging-device interface and normal TV authorization. An ordinary TV USB socket is not sufficient. Integrated Fire TV Smart TVs document network ADB only. Direct Pi-to-TV Ethernet addressing remains pending; the default installer does not change networking. See [direct LAN handoff](DIRECT_LAN.md).
