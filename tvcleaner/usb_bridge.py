"""Narrow USB selector bridge for unmodified CleanYourTV cleanup tools.

The vendored native-ADB client requires a host:port argument. Its synthetic
``.invalid`` label is never used as a network address: this bridge checks the
label, checks the explicitly selected USB device, and forwards only the
get-state/shell calls used by the guarded cleanup and recovery scripts.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .transport import (
    TransportError, _SYSTEM_ADB, _USB_ENV, adb_environment, parse_usb_devices,
    upstream_endpoint, validate_endpoint,
)


def main(argv=None, *, _native_adb=None) -> int:
    """CLI entry point; native binary injection is available only to Python tests.

    Production always uses /usr/bin/adb. No argument or environment variable can
    replace that executable or select a different serial from the mapped label.
    ``TVCLEANER_USB_TARGET`` is supplied by the controller after planning; its
    value must match the wrapper's explicit -s label and current USB enumeration.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        endpoint = validate_endpoint(os.environ.get(_USB_ENV, ""))
        if not endpoint.startswith("usb:"):
            raise TransportError("The USB bridge has no explicitly selected USB target.")
        label = upstream_endpoint(endpoint)
        if os.environ.get("TV_HOST") != label or len(arguments) < 3 or arguments[:2] != ["-s", label]:
            raise TransportError("The USB bridge target does not match the controller's selected device.")
        command = arguments[2:]
        if command != ["get-state"]:
            if len(command) == 2 and command[0] == "shell":
                shell_command = command[1]
            elif len(command) == 4 and command[:3] == ["shell", "-T", "-x"]:
                shell_command = command[3]
            else:
                raise TransportError("The USB bridge accepts only cleanup get-state and shell calls.")
            if (not isinstance(shell_command, str) or not shell_command or len(shell_command) > 8192
                    or shell_command.startswith("-") or any(ord(c) < 32 for c in shell_command)):
                raise TransportError("The USB bridge received an invalid shell command.")
        home = os.environ.get("HOME", "")
        if not home or not Path(home).is_absolute():
            raise TransportError("The USB bridge requires the controller's private ADB home.")
        environment = adb_environment(home)
        # Do not read TV_ADB_BINARY, PATH, or any other executable override here.
        # _native_adb exists for an isolated subprocess fixture, never the CLI.
        binary = _SYSTEM_ADB if _native_adb is None else os.fspath(_native_adb)
        devices = subprocess.run([binary, "devices", "-l"], capture_output=True,
                                 text=True, timeout=20, check=False, env=environment)
        if devices.returncode:
            raise TransportError("USB ADB enumeration failed. Check the Pi's USB permissions.")
        matches = [item for item in parse_usb_devices(devices.stdout) if item["endpoint"] == endpoint]
        if len(matches) != 1 or matches[0]["state"] != "device":
            raise TransportError("The selected USB device is absent, ambiguous, offline, or awaiting TV authorization.")
        # An explicit serial is always selected. Never choose the first transport,
        # switch adbd mode, issue adb connect, or fall back to a network endpoint.
        result = subprocess.run([binary, "-s", endpoint[4:], *command], timeout=120,
                                check=False, env=environment)
        return result.returncode
    except (TransportError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        message = str(error) if isinstance(error, TransportError) else "USB ADB could not complete the selected operation."
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
