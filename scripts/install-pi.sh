#!/usr/bin/env bash
# Run from the extracted source: sudo bash scripts/install-pi.sh
set -euo pipefail
umask 077
if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo bash scripts/install-pi.sh" >&2
  exit 1
fi
if [ "$(uname -s)" != Linux ] || ! command -v apt-get >/dev/null; then
  echo "This installer requires Raspberry Pi OS or another Debian-based Linux." >&2
  exit 1
fi
case "$(uname -m)" in
  aarch64|x86_64) ;;
  *) echo "Use 64-bit Raspberry Pi OS on your Pi 4. This installer supports ARM64 and x86-64." >&2; exit 1 ;;
esac
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR=/opt/universal-tv-debloater
STATE_BASE=/var/lib/tvcleaner
if [ ! -f "$SOURCE_DIR/UPSTREAM.json" ] || [ ! -f "$SOURCE_DIR/tvcleaner/service.py" ]; then
  echo "The source package is incomplete." >&2
  exit 1
fi
if [ -e "$INSTALL_DIR" ] && [ ! -f "$INSTALL_DIR/UPSTREAM.json" ]; then
  echo "$INSTALL_DIR already exists and is not this application. Nothing was overwritten." >&2
  exit 1
fi
apt-get update
apt-get install -y python3 python3-venv python3-pip adb avahi-daemon ca-certificates iproute2
if ! getent group tvcleaner >/dev/null; then
  groupadd --system tvcleaner
fi
if ! id tvcleaner >/dev/null 2>&1; then
  useradd --system --gid tvcleaner --create-home --home-dir "$STATE_BASE" --shell /usr/sbin/nologin tvcleaner
fi
install -d -m 0700 -o tvcleaner -g tvcleaner "$STATE_BASE" "$STATE_BASE/state" "$STATE_BASE/state/home"
if systemctl is-active --quiet tvcleaner.service; then
  systemctl stop tvcleaner.service
fi
python3 - "$SOURCE_DIR" "$INSTALL_DIR" <<'PY'
from pathlib import Path
import shutil,sys
source,target=map(Path,sys.argv[1:])
if source.resolve()!=target.resolve():
    shutil.copytree(source,target,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git','.venv','venv','__pycache__','*.pyc','.state','state','adbkey'))
PY
chmod -R a+rX "$INSTALL_DIR"
chmod 0755 "$INSTALL_DIR/scripts/adb-usb-wrapper.py"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$INSTALL_DIR/vendor/cleanyourtv/tools/requirements.txt"
# The application is root-owned, but its dedicated account must be able to read
# and execute the environment just created under the installer's private umask.
chmod -R a+rX "$INSTALL_DIR/.venv"
if [ ! -e "$INSTALL_DIR/vendor/cleanyourtv/tools/venv" ]; then
  ln -s ../../../.venv "$INSTALL_DIR/vendor/cleanyourtv/tools/venv"
fi
cat > /usr/local/bin/tvcleaner <<'SH'
#!/bin/sh
if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u tvcleaner -- /opt/universal-tv-debloater/.venv/bin/python /opt/universal-tv-debloater/scripts/cli.py --state /var/lib/tvcleaner/state "$@"
fi
exec sudo /usr/local/bin/tvcleaner "$@"
SH
chmod 0755 /usr/local/bin/tvcleaner
# Grant only USB devices exposing the standard ADB interface to this service.
# A USB socket alone does not imply a TV implements that interface.
install -d -m 0755 /etc/udev/rules.d
cat > /etc/udev/rules.d/70-tvcleaner-adb.rules <<'RULES'
ACTION!="add|change", GOTO="tvcleaner_adb_end"
SUBSYSTEM!="usb", GOTO="tvcleaner_adb_end"
ENV{DEVTYPE}!="usb_device", GOTO="tvcleaner_adb_end"
IMPORT{builtin}="usb_id"
ENV{ID_USB_INTERFACES}=="*:ff4201:*", GROUP="tvcleaner", MODE="0660"
LABEL="tvcleaner_adb_end"
RULES
chmod 0644 /etc/udev/rules.d/70-tvcleaner-adb.rules
udevadm control --reload-rules
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-update.service" /etc/systemd/system/pitv-update.service
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-update.path" /etc/systemd/system/pitv-update.path
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-auto-update.service" /etc/systemd/system/pitv-auto-update.service
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-auto-update.timer" /etc/systemd/system/pitv-auto-update.timer
# The PiTV appliance must remain reachable when idle. Keep explicit policy
# drop-ins so later package updates do not restore default sleep behavior.
install -d -m 0755 /etc/systemd/logind.conf.d /etc/systemd/sleep.conf.d
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-logind.conf" /etc/systemd/logind.conf.d/90-pitv-keep-awake.conf
install -o root -g root -m 0644 "$INSTALL_DIR/scripts/pitv-sleep.conf" /etc/systemd/sleep.conf.d/90-pitv-keep-awake.conf
# Masking the common sleep targets also blocks service-initiated suspend.
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
if systemctl is-active --quiet systemd-logind.service; then
  systemctl kill --signal=HUP --kill-whom=main systemd-logind.service
fi
cat > /etc/systemd/system/tvcleaner.service <<'UNIT'
[Unit]
Description=Universal TV Debloater local control page
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tvcleaner
Group=tvcleaner
WorkingDirectory=/opt/universal-tv-debloater
Environment=HOME=/var/lib/tvcleaner/state/home
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/universal-tv-debloater/.venv/bin/python -m tvcleaner --state /var/lib/tvcleaner/state serve --host 0.0.0.0 --port 80
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
UMask=0077
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/tvcleaner

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now avahi-daemon
systemctl enable --now pitv-update.path
systemctl enable --now tvcleaner.service
systemctl enable --now pitv-auto-update.timer
echo "Installed. Open http://$(hostname -s).local/ on your phone on the same reachable local network."
# The old local updater still checks access.json for compatibility with v0.2.0.
# The web pages never require or display this token.
/usr/local/bin/tvcleaner token >/dev/null
echo "The service starts on boot. Unattended cleanup is off until you arm it for approved recipes or enrolled TVs."
echo "For USB debugging devices already attached, reconnect the cable so the new USB permission rule applies."
