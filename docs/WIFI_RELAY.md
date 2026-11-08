# Wi-Fi relay on PiTV

PiTV can use two Wi-Fi radios: the built-in `wlan0` broadcasts the Pi's access
point, while a separate USB Wi-Fi adapter joins the network used by the TV.
NetworkManager shares the Pi's current internet route to phones on its access
point using DHCP, DNS and NAT. This is routed internet sharing, not a transparent
Wi-Fi bridge. The Pi itself scans the TV network and performs ADB cleanup; a phone
on the PITV network does not need direct discovery of the TV.

## Before you start

- Use 64-bit Raspberry Pi OS with NetworkManager, a Pi with built-in Wi-Fi, and
  a Linux-compatible USB Wi-Fi adapter. The adapter must appear as a separate
  wireless interface; it does not need access-point mode.
- Keep the TV on the Wi-Fi network that the USB adapter will join. Enable the
  TV's debugging option and approve the Pi on the TV screen when asked.
- The default Pi access point is **PITV** with password **pitv12345**. This is a
  public default, so change it in **PITV network** before using it around people
  you do not trust. The web interface has no separate token.

## Set up from the phone page

1. Open `http://pitv.local/` through the Pi's existing network connection. On a
   fresh install, PiTV starts the PITV access point automatically only when the
   built-in Wi-Fi radio is idle. An app update preserves the existing Wi-Fi
   settings; use this page to start the access point for the first time.
2. Under the two version cards, open **PITV network**. Change the Pi Wi-Fi name
   and password if desired, then press **Save and start Pi Wi-Fi**. If your phone
   was using the built-in Wi-Fi's former connection, it may disconnect. Join the
   new Pi network and reopen `http://pitv.local/`. The page shows the Pi's access
   point address as an alternative if `.local` discovery is blocked. The original
   `ssh pi@pitv.local` login also remains available if you chose user `pi`.
3. Plug in the USB Wi-Fi adapter. Open **Wi-Fi relay**, select the USB adapter,
   and press **Scan nearby Wi-Fi**. Choose the TV's Wi-Fi name, enter its password,
   and press **Connect USB Wi-Fi**. The password is transferred to the Pi's local
   web service and stored in a root-only NetworkManager profile. It is not shown
   again on the page or returned by the API.
4. Connect your phone or tablet to the Pi's access point. Press **Find TVs** in
   the relay panel, or use the normal TV-finding step. PiTV scans up to four
   connected private local /24 ranges, including the USB adapter's network. If
   the TV is not found, enter its debugging address manually or use pairing.
5. Review the matching profile and packages, then press **Clean your TV** once.
   Check the TV afterward and keep its restore record. Automatic TV cleanup
   remains off until a supervised cleanup and restore have been validated.

## Network boundaries

- The Pi does not join or change the TV's Wi-Fi settings. It routes traffic
  from the PITV network to its current default internet connection. The USB
  connection receives a low route metric so it is normally that connection.
  The page reports when another route is selected instead. Ethernet remains
  usable as a management connection.
- A guest network that isolates Wi-Fi clients may hide the TV from the Pi even
  if internet works. A hotel or other captive portal may require separate login.
  Enterprise 802.1X and WEP Wi-Fi are not supported by the relay form.
- `pitv.local` uses local multicast discovery. If a device blocks `.local`, use
  the access-point address shown in the page or the Pi's current address from
  the router. The interface does not expose PiTV through the public internet.
- Starting or renaming the access point can disconnect a phone currently using
  it. Rejoin with the new name and password. When the built-in Wi-Fi is the Pi's
  only active network, PiTV refuses to take it over until Ethernet or a USB
  Wi-Fi uplink is connected.

The relay has automated validation and demo tests. It has not yet been tested
with a physical USB dongle, TV, and phone together. Driver support, radio
capabilities, NetworkManager behavior, and Wi-Fi isolation vary by location.
