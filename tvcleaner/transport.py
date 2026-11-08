"""Bounded, authenticated ADB access to an explicitly selected local TV.

This module never discovers, roots, flashes, or authorizes a device. Discovery is
separate; the TV owner must enable debugging and accept the normal ADB prompt.
Raw snapshots contain private device information and belong in private state.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


class TransportError(RuntimeError):
    """A validation, connection, authorization, or device-read failure."""


PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
))
_PACKAGE_PATTERN = r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|android)"
PACKAGE_RE = re.compile(rf"^{_PACKAGE_PATTERN}$")
_COMPONENT_RE = re.compile(rf"(?<![A-Za-z0-9_.])({_PACKAGE_PATTERN})/([A-Za-z0-9_.$]+)")
_PROPERTY_RE = re.compile(r"^\[([^\]\r\n]+)\]: \[(.*)\]$")
_ERROR_RE = re.compile(
    r"(?im)^(?:error:|adb: error:|failure(?:\s|\[|:)|securityexception|"
    r"java\.[\w.]*exception|exception occurred|permission denied|"
    r"/system/bin/sh:|sh:|unknown command:|can't find service:|cmd: can't find service:)"
)
_MAX_TEXT = 4 * 1024 * 1024
_MAX_SCREENSHOT = 24 * 1024 * 1024
_TV_FEATURES = {
    "android.hardware.type.television", "android.software.leanback",
    "android.software.leanback_only", "amazon.hardware.fire_tv",
}
_USB_SERIAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_USB_PATH_RE = re.compile(r"[0-9]+(?:-[0-9]+(?:\.[0-9]+)*)?\Z")
_USB_STATES = {
    "device": "authorized", "unauthorized": "approval_required", "offline": "offline",
    "authorizing": "approval_required", "connecting": "connecting",
    "no_permissions": "permission_required",
}
_USB_ENV = "TVCLEANER_USB_TARGET"
_SYSTEM_ADB = "/usr/bin/adb"
ADB_SERVER_PORT = "5038"


def private_ipv4(value: str) -> ipaddress.IPv4Address:
    """Accept exactly an RFC1918/link-local literal, never a hostname."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise TransportError("Enter a private IPv4 address on the TV's local network.")
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        raise TransportError("Use a numeric IPv4 address, not a hostname or URL.") from None
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise TransportError("Only RFC1918 or link-local IPv4 addresses are allowed.")
    return address


def split_endpoint(endpoint: str) -> tuple[str, int]:
    if not isinstance(endpoint, str) or len(endpoint) > 32 or endpoint != endpoint.strip():
        raise TransportError("Enter the TV's private IPv4 address and optional port.")
    parts = endpoint.split(":")
    if len(parts) not in (1, 2):
        raise TransportError("Use an IPv4 endpoint such as 192.168.1.50:5555.")
    address = private_ipv4(parts[0])
    port_text = parts[1] if len(parts) == 2 else "5555"
    if not re.fullmatch(r"[0-9]{1,5}", port_text):
        raise TransportError("The ADB port must be a number from 1 to 65535.")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise TransportError("The ADB port must be a number from 1 to 65535.")
    return str(address), port


def validate_endpoint(endpoint: str) -> str:
    """Return a private IPv4:port endpoint or an explicit ``usb:<serial>``.

    A syntactically valid USB serial is not authority to use that device. Every
    USB operation also checks current ``adb devices -l`` USB-path evidence and
    authorization, and cleanup still requires a complete television snapshot.
    """
    if isinstance(endpoint, str) and endpoint.startswith("usb:"):
        serial = endpoint[4:]
        if not _USB_SERIAL_RE.fullmatch(serial) or serial.lower().startswith("emulator-"):
            raise TransportError("Use usb:<serial> for an explicitly detected USB ADB device.")
        return "usb:" + serial
    address, port = split_endpoint(endpoint)
    return f"{address}:{port}"


def is_usb_endpoint(endpoint: str) -> bool:
    return validate_endpoint(endpoint).startswith("usb:")


def parse_usb_devices(output: str) -> list[dict]:
    """Parse only ADB entries with an explicit USB bus path, never network peers.

    Permission-denied and duplicate-serial records are visible but have no
    actionable endpoint. A USB path is transport evidence, not TV evidence.
    """
    if not isinstance(output, str) or len(output) > 1024 * 1024:
        raise TransportError("ADB returned an invalid USB device list.")
    records = []
    serials: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or line.startswith("List of devices"):
            continue
        paths = [part[4:] for part in fields[2:] if part.startswith("usb:")]
        if len(paths) != 1 or len(paths[0]) > 64 or not _USB_PATH_RE.fullmatch(paths[0]):
            continue
        state = "no_permissions" if fields[1:3] == ["no", "permissions"] else fields[1]
        endpoint = None
        try:
            endpoint = validate_endpoint("usb:" + fields[0])
        except TransportError:
            # adb cannot read a USB serial before udev grants access on Linux.
            if state != "no_permissions":
                continue
        if endpoint:
            serials[endpoint] = serials.get(endpoint, 0) + 1
        records.append({
            "endpoint": endpoint, "address": "USB " + paths[0], "port": None,
            "transport": "usb", "state": state,
            "status": _USB_STATES.get(state, "unavailable"), "usb_path": paths[0],
        })
        if len(records) > 64:
            raise TransportError("Too many USB ADB entries were returned; automatic selection is blocked.")
    for record in records:
        if record["endpoint"] and serials[record["endpoint"]] > 1:
            record.update(endpoint=None, state="ambiguous", status="ambiguous_serial")
        elif record["state"] == "no_permissions":
            record["endpoint"] = None
    return sorted(records, key=lambda item: (item["usb_path"], item["endpoint"] or ""))


def upstream_endpoint(endpoint: str) -> str:
    """Stable transport label for the unmodified upstream host:port interface.

    The .invalid name is never resolved or connected to. The USB wrapper accepts
    only this exact label and substitutes a currently enumerated USB selector.
    """
    endpoint = validate_endpoint(endpoint)
    if endpoint.startswith("usb:"):
        serial_hash = hashlib.sha256(endpoint[4:].encode("ascii")).hexdigest()[:32]
        return f"usb-{serial_hash}.invalid:5555"
    return endpoint


def upstream_record_tag(endpoint: str) -> str:
    """The exact tag used by upstream recovery.record_path for this transport."""
    endpoint = validate_endpoint(endpoint)
    label = upstream_endpoint(endpoint) if endpoint.startswith("usb:") else split_endpoint(endpoint)[0]
    return re.sub(r"[^A-Za-z0-9.-]", "_", label)


def upstream_environment(endpoint: str, binary: str, root: Path) -> dict[str, str]:
    """Explicit upstream transport settings, with no inherited USB overrides.

    USB's compatibility wrapper intentionally uses only Debian's system adb.
    Network transport retains the controller's explicitly selected executable.
    """
    endpoint = validate_endpoint(endpoint)
    if not endpoint.startswith("usb:"):
        return {"TV_HOST": endpoint, "TV_ADB_BINARY": str(binary)}
    resolved = shutil.which(os.fspath(binary)) if not Path(binary).is_absolute() else os.fspath(binary)
    if not resolved or Path(resolved).resolve() != Path(_SYSTEM_ADB).resolve():
        raise TransportError("USB cleanup requires Debian's native /usr/bin/adb. Run the Pi installer first.")
    wrapper = Path(root).resolve() / "scripts/adb-usb-wrapper.py"
    if not wrapper.is_file() or wrapper.is_symlink() or not os.access(wrapper, os.X_OK):
        raise TransportError("The USB compatibility wrapper is missing or not executable. Reinstall this release.")
    return {"TV_HOST": upstream_endpoint(endpoint), "TV_ADB_BINARY": str(wrapper), _USB_ENV: endpoint}


def adb_environment(home: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Use the appliance's loopback daemon/key home; disable mDNS auto-connect.

    The appliance uses port 5038 instead of Android tools' default port 5037.
    Never kill or reconfigure another app's daemon to take control of its keys.
    """
    environment = os.environ.copy()
    environment["ADB_MDNS_AUTO_CONNECT"] = "0"
    # Never inherit a remote ADB server target from the launching shell.
    environment.pop("ADB_SERVER_SOCKET", None)
    environment.pop("ANDROID_ADB_SERVER_ADDRESS", None)
    environment.pop("ANDROID_ADB_SERVER_PORT", None)
    environment.pop("ANDROID_SERIAL", None)
    # A LAN operation must not inherit settings from a previous USB operation.
    for key in list(environment):
        if key.startswith("TVCLEANER_USB_"):
            environment.pop(key, None)
    # ADB_SERVER_SOCKET treats this as a remote daemon and refuses to start it.
    # The native client starts a missing local daemon with this port setting.
    environment["ANDROID_ADB_SERVER_PORT"] = ADB_SERVER_PORT
    if home is not None:
        path = Path(home).expanduser().resolve()
        environment["HOME"] = str(path)
        environment["ANDROID_USER_HOME"] = str(path / ".android")
        environment.pop("ADB_VENDOR_KEYS", None)
    return environment


def resolve_adb(adb_binary: str | os.PathLike[str] | None = None) -> str:
    if adb_binary is None:
        resolved = shutil.which("adb")
        if not resolved:
            raise TransportError("ADB is not installed. Install Raspberry Pi OS's native adb package.")
        return resolved
    try:
        value = os.fspath(adb_binary)
    except TypeError:
        raise TransportError("The ADB executable path is invalid.") from None
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("-"):
        raise TransportError("The ADB executable path is invalid.")
    return value


def _components(output: str) -> set[str]:
    return {match.group(0) for match in _COMPONENT_RE.finditer(output)}


def _package_set(output: str, name: str, *, allow_empty: bool = False) -> set[str]:
    result: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("package:") or not PACKAGE_RE.fullmatch(line[8:]):
            raise TransportError(f"The TV returned an incomplete or malformed {name} list.")
        result.add(line[8:])
    if not result and not allow_empty:
        raise TransportError(f"The TV returned no {name}; no cleanup can proceed.")
    return result


def _settings(output: str, namespace: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            continue
        if "=" not in line:
            raise TransportError(f"The TV did not return a complete {namespace} settings backup.")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", key):
            raise TransportError(f"The TV did not return a complete {namespace} settings backup.")
        result[key] = value
    if not result:
        raise TransportError(f"The TV returned no {namespace} settings; cleanup is blocked.")
    return result


def _identity(raw: dict[str, str]) -> dict[str, str]:
    """The same identity parser for cleanup and a TV that needs recovery."""
    properties: dict[str, str] = {}
    for line in raw["getprop"].splitlines():
        if not line.strip():
            continue
        match = _PROPERTY_RE.fullmatch(line.rstrip("\r"))
        if not match:
            raise TransportError("The TV returned incomplete device properties; cleanup is blocked.")
        properties[match[1]] = match[2]

    def prop(name: str, *fallback: str) -> str:
        for key in (name, *fallback):
            value = properties.get(key, "").strip()
            if value and value.lower() not in ("unknown", "null"):
                return value
        return ""

    features: set[str] = set()
    for line in raw["features"].splitlines():
        if not line.strip():
            continue
        if not line.startswith("feature:"):
            raise TransportError("The TV returned an incomplete feature list; cleanup is blocked.")
        features.add(line[8:].split("=", 1)[0].strip())
    if not features.intersection(_TV_FEATURES):
        raise TransportError("This device did not identify as Android TV or Fire TV. Cleanup is blocked.")

    identity = {
        "manufacturer": prop("ro.product.manufacturer", "ro.product.vendor.manufacturer"),
        "brand": prop("ro.product.brand", "ro.product.vendor.brand"),
        "model": prop("ro.product.model", "ro.product.vendor.model"),
        "device": prop("ro.product.device", "ro.product.vendor.device"),
        "product": prop("ro.product.name", "ro.product.vendor.name"),
        "android": prop("ro.build.version.release"),
        "sdk": prop("ro.build.version.sdk"),
        "build": prop("ro.build.display.id", "ro.build.version.incremental"),
        "fingerprint": prop("ro.build.fingerprint", "ro.system.build.fingerprint"),
        "serial": prop("ro.serialno", "ro.boot.serialno"),
        "android_id": _settings(raw["settings_secure"], "secure").get("android_id", ""),
        "family": "android_tv",
    }
    if identity["android_id"].lower() in ("null", "unknown"):
        identity["android_id"] = ""
    essential = ("manufacturer", "brand", "model", "device", "product", "android", "sdk", "build", "fingerprint")
    if any(not identity[key] for key in essential) or not re.fullmatch(r"[0-9]{1,3}", identity["sdk"]) or int(identity["sdk"]) < 1:
        raise TransportError("The TV's identity or firmware is incomplete; cleanup is blocked.")
    if "amazon.hardware.fire_tv" in features or identity["manufacturer"].lower() == "amazon":
        identity["family"] = "fire_os"

    return identity


class AdbTransport:
    def __init__(self, adb_binary=None, home=None, timeout=20):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise TransportError("The ADB timeout must be greater than zero and at most 120 seconds.")
        self.adb_binary = resolve_adb(adb_binary)
        self.binary = self.adb_binary
        self.timeout = float(timeout)
        try:
            self.env = adb_environment(home)
            if home is not None:
                Path(self.env["ANDROID_USER_HOME"]).mkdir(mode=0o700, parents=True, exist_ok=True)
        except (OSError, ValueError, TypeError):
            raise TransportError("The private ADB key directory could not be prepared.") from None

    def _run(self, arguments: list[str], *, input_text: str | None = None, binary=False):
        try:
            result = subprocess.run(
                [self.adb_binary, *arguments], input=input_text,
                capture_output=True, text=not binary, timeout=self.timeout,
                env=self.env, check=False,
            )
        except subprocess.TimeoutExpired:
            raise TransportError("ADB timed out. Check the TV's network and debugging approval.") from None
        except (OSError, ValueError, UnicodeError):
            raise TransportError("ADB could not run. Check the native adb installation and TV connection.") from None
        # Do not surface arbitrary subprocess output or pairing secrets in an API error.
        stdout = result.stdout or (b"" if binary else "")
        stderr = result.stderr or (b"" if binary else "")
        if result.returncode:
            diagnostic = (stdout + stderr).decode("utf-8", "replace") if binary else stdout + stderr
            if "unauthorized" in diagnostic.lower() or "authentication" in diagnostic.lower():
                raise TransportError("The TV has not authorized this Pi. Accept its debugging prompt or pair on the TV.")
            if "offline" in diagnostic.lower():
                raise TransportError("The TV's ADB connection is offline. Reconnect after checking the TV.")
            raise TransportError("ADB refused the operation. Check the TV's approval, network, and debugging settings.")
        if len(stdout) > (_MAX_SCREENSHOT if binary else _MAX_TEXT):
            raise TransportError("The TV response exceeded the allowed size; cleanup is blocked.")
        if not binary and (_ERROR_RE.search(stdout) or _ERROR_RE.search(stderr)):
            raise TransportError("The TV refused a required command; cleanup is blocked.")
        return stdout

    def connect(self, endpoint) -> str:
        endpoint = validate_endpoint(endpoint)
        if endpoint.startswith("usb:"):
            serial = self._usb_selector(endpoint)
            if self._run(["-s", serial, "get-state"]).strip() != "device":
                raise TransportError("The USB device is not ready. Check its debugging approval and cable.")
            return "USB debugging is authorized. Inspect the device to verify that it is a supported TV."
        output = self._run(["connect", endpoint])
        lowered = output.lower()
        if "connected to " not in lowered or any(value in lowered for value in ("failed", "cannot", "unable")):
            raise TransportError("The TV did not accept the connection. Enable debugging and approve this Pi on the TV.")
        if self._run(["-s", endpoint, "get-state"]).strip() != "device":
            raise TransportError("The TV has not authorized this Pi. Accept its debugging prompt, then connect again.")
        return f"Connected to {endpoint}. The TV authorized this Pi."

    def pair(self, endpoint, code) -> str:
        # Wireless pairing has a network endpoint. USB uses the TV's RSA prompt.
        address, port = split_endpoint(endpoint)
        endpoint = f"{address}:{port}"
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise TransportError("Enter the six-digit pairing code shown on the TV.")
        # ADB accepts the code interactively. Keep it out of argv, logs and results.
        output = self._run(["pair", endpoint], input_text=code + "\n")
        if "successfully paired" not in output.lower():
            raise TransportError("Pairing was not accepted. Check the TV's current pairing address and code.")
        return "Pairing accepted. Connect using the separate debugging address and port shown on the TV."

    def usb_devices(self) -> list[dict]:
        """List USB transport state without authorizing or sending device commands."""
        return parse_usb_devices(self._run(["devices", "-l"]))

    def _usb_selector(self, endpoint: str) -> str:
        endpoint = validate_endpoint(endpoint)
        matches = [device for device in self.usb_devices() if device["endpoint"] == endpoint]
        if len(matches) != 1:
            raise TransportError("This USB device is missing, has no permissions, or has an ambiguous serial.")
        state = matches[0]["state"]
        if state in ("unauthorized", "authorizing"):
            raise TransportError("The USB device has not authorized this Pi. Accept its debugging prompt on the TV.")
        if state == "offline":
            raise TransportError("The USB ADB connection is offline. Check the TV and reconnect its data cable.")
        if state != "device":
            raise TransportError("This USB device is not in an authorized, running Android state.")
        return endpoint[4:]

    def _selector(self, endpoint: str) -> str:
        endpoint = validate_endpoint(endpoint)
        return self._usb_selector(endpoint) if endpoint.startswith("usb:") else endpoint

    def shell(self, endpoint, command) -> str:
        endpoint = validate_endpoint(endpoint)
        if (not isinstance(command, str) or not command or len(command) > 8192
                or any(ord(character) < 32 for character in command)):
            raise TransportError("The requested TV command is invalid.")
        return self._run(["-s", self._selector(endpoint), "shell", command])

    def _optional_shell(self, endpoint: str, command: str) -> str | None:
        try:
            return self.shell(endpoint, command)
        except TransportError:
            return None

    def identity_snapshot(self, endpoint) -> dict[str, Any]:
        """Confirm a recovery target without requiring healthy Home/input services.

        This is only sufficient for device/firmware binding before restoration.
        A complete ``snapshot`` is still required before cleanup and after restore.
        """
        endpoint = validate_endpoint(endpoint)
        raw = {
            "getprop": self.shell(endpoint, "getprop"),
            "settings_secure": self.shell(endpoint, "settings list secure"),
            "features": self.shell(endpoint, "pm list features"),
        }
        identity = _identity(raw)
        serial = identity["serial"].strip().lower()
        android_id = identity["android_id"].strip().lower()
        serial_valid = serial not in ("", "unknown", "null", "0123456789abcdef", "1234567890", "0")
        android_id_valid = (re.fullmatch(r"[0-9a-f]{16}", android_id) is not None
                            and android_id not in ("0" * 16, "f" * 16))
        if not serial_valid and not android_id_valid:
            raise TransportError("The TV did not provide a stable device ID; restoration is blocked.")
        return {"identity": identity, "raw": raw}

    def snapshot(self, endpoint) -> dict[str, Any]:
        """Read identity, backups and live component protections; never write TV state."""
        endpoint = validate_endpoint(endpoint)
        raw: dict[str, str] = {}
        probes = {
            "getprop": "getprop",
            "packages": "pm list packages --user 0",
            "disabled": "pm list packages -d --user 0",
            "third_party": "pm list packages -3 --user 0",
            "settings_global": "settings list global",
            "settings_secure": "settings list secure",
            "settings_system": "settings list system",
            "features": "pm list features",
        }
        for key, command in probes.items():
            raw[key] = self.shell(endpoint, command)

        packages = _package_set(raw["packages"], "installed packages")
        disabled = _package_set(raw["disabled"], "disabled packages", allow_empty=True)
        third_party = _package_set(raw["third_party"], "third-party packages", allow_empty=True)
        if not disabled <= packages or not third_party <= packages:
            raise TransportError("The package inventories disagree. Wait for TV updates to finish, then inspect again.")
        settings = {namespace: _settings(raw[f"settings_{namespace}"], namespace)
                    for namespace in ("global", "secure", "system")}

        identity = _identity(raw)

        home_query = "resolve-activity --brief --user 0 -a android.intent.action.MAIN -c android.intent.category.HOME"
        home_output = self._optional_shell(endpoint, "cmd package " + home_query)
        if home_output is None or not _components(home_output):
            home_output = self.shell(endpoint, "pm " + home_query)
        raw["home"] = home_output
        homes = _components(home_output)
        if len(homes) != 1:
            raise TransportError("A single active Home app could not be identified. Choose a default launcher on the TV first.")
        home = next(iter(homes))
        home_package = home.split("/", 1)[0]
        if home_package not in packages or home_package in disabled or home.endswith(("ResolverActivity", "ChooserActivity")):
            raise TransportError("The active Home app could not be verified; cleanup is blocked.")
        protected = {home_package}

        # Keep all currently registered Home apps; not only the resolved default.
        raw["home_candidates"] = self._optional_shell(
            endpoint, "cmd package query-activities --brief --user 0 -a android.intent.action.MAIN -c android.intent.category.HOME"
        ) or ""
        protected.update(component.split("/", 1)[0] for component in _components(raw["home_candidates"]))

        raw["ime"] = self._optional_shell(endpoint, "ime list -a -s") or ""
        ime_components = _components(raw["ime"])
        for name in ("default_input_method", "enabled_input_methods"):
            ime_components.update(_components(settings["secure"].get(name, "")))
        current_imes = {component.split("/", 1)[0] for component in ime_components} & packages
        if not current_imes:
            raise TransportError("The TV's keyboard services could not be identified; cleanup is blocked.")
        protected.update(current_imes)

        # Query the TV input service registry, falling back to its system dump.
        input_command = "cmd package query-services --brief --user 0 -a android.media.tv.TvInputService"
        input_output = self._optional_shell(endpoint, input_command)
        input_components = _components(input_output or "")
        raw["tv_input_services"] = input_output or ""
        if not input_components:
            raw["tv_input"] = self.shell(endpoint, "dumpsys tv_input")
            if not raw["tv_input"].strip() or "Can't find service" in raw["tv_input"]:
                raise TransportError("The TV input registry could not be backed up; cleanup is blocked.")
            # A streaming stick can have no physical tuner or HDMI input services.
            if not any(marker in raw["tv_input"].lower() for marker in ("tvinput", "tv input", "input map", "inputmap")):
                raise TransportError("The TV input registry response was not recognized; cleanup is blocked.")
            input_components.update(_components(raw["tv_input"]))
        protected.update(component.split("/", 1)[0] for component in input_components)
        protected &= packages

        return {
            "identity": identity, "packages": sorted(packages),
            "disabled": sorted(disabled), "third_party": sorted(third_party),
            "home": home, "protected": sorted(protected), "raw": raw,
        }

    def screenshot(self, endpoint, destination: Path):
        endpoint = validate_endpoint(endpoint)
        image = self._run(["-s", self._selector(endpoint), "exec-out", "screencap", "-p"], binary=True)
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) < 33:
            raise TransportError("The TV did not return a PNG screenshot.")
        temporary: str | None = None
        try:
            destination = Path(destination)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=".tv-shot-", dir=destination.parent, delete=False) as file:
                temporary = file.name
                file.write(image)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            temporary = None
        except (OSError, ValueError, TypeError):
            raise TransportError("The private TV screenshot could not be saved.") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
