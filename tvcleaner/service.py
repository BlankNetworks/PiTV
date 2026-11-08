"""Device-bound planning, durable backups and reversible upstream execution."""
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time

from . import __version__
from .catalog import Catalog
from .demo import DemoTransport
from .policy import package_rows
from .storage import atomic_bytes, atomic_json, private_dir, read_json


class ServiceError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def device_key(snapshot):
    identity = snapshot["identity"]
    serial = str(identity.get("serial", "")).strip()
    android_id = str(identity.get("android_id", "")).strip().lower()
    if serial.lower() in ("", "unknown", "null", "0123456789abcdef", "1234567890", "0"):
        serial = ""
    if not re.fullmatch(r"[0-9a-f]{16}", android_id) or android_id in ("0" * 16, "f" * 16):
        android_id = ""
    if not serial and not android_id:
        raise ServiceError("The TV did not provide a stable device ID. Inspection is available; automatic or remote cleanup is blocked.")
    return digest({"serial": serial, "android_id": android_id, "model": identity.get("model"),
                   "manufacturer": identity.get("manufacturer")})


def binding(snapshot):
    identity = snapshot["identity"]
    fingerprint = identity.get("fingerprint", "")
    if not fingerprint or fingerprint.lower() in ("unknown", "null"):
        raise ServiceError("The firmware fingerprint is missing. This TV needs manual investigation.")
    return {"device": device_key(snapshot), "firmware": fingerprint, "build": identity.get("build", ""),
            "sdk": identity.get("sdk", ""), "family": identity.get("family", "")}


def state_signature(snapshot):
    return digest({"binding": binding(snapshot), "packages": sorted(snapshot["packages"]),
                   "disabled": sorted(snapshot["disabled"]), "home": snapshot["home"],
                   "protected": sorted(snapshot.get("protected", [])),
                   "third_party": sorted(snapshot.get("third_party", []))})


def public_identity(snapshot):
    return {k: snapshot["identity"].get(k, "") for k in
            ("brand", "manufacturer", "model", "android", "sdk", "build", "family")}


class CleanerService:
    def __init__(self, root, state, demo=False, transport=None):
        self.root = Path(root).resolve()
        self.upstream = self.root / "vendor/cleanyourtv"
        self.state = private_dir(state)
        self.home = private_dir(self.state / "home")
        self.runs_dir = private_dir(self.state / "runs")
        self.plans_dir = private_dir(self.state / "plans")
        self.demo = demo
        self.manifest = read_json(self.root / "UPSTREAM.json")
        if not self.manifest:
            raise ServiceError("The pinned upstream manifest is missing.")
        self.verify_upstream()
        self.catalog = Catalog(self.upstream)
        spec = importlib.util.spec_from_file_location("cleanyourtv_recovery", self.upstream / "tools/recovery.py")
        self.recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recovery)
        if transport is None:
            if demo:
                transport = DemoTransport()
            else:
                from .transport import AdbTransport
                transport = AdbTransport(home=self.home)
        self.transport = transport
        self.mutation_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.automatic_lock = threading.Lock()
        self.config = read_json(self.state / "config.json", {"automatic": {"enabled": False, "cidr": ""}, "enrollments": {}})

    def _acquire_mutation(self):
        if not self.mutation_lock.acquire(blocking=False):
            raise ServiceError("Another cleanup or restore is running.")
        lock = None
        try:
            lock = (self.state / "mutation.lock").open("a+")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Holding the process lock proves these are abandoned, not another CLI's active run.
            for path in self.runs_dir.glob("*/run.json"):
                run = read_json(path)
                if run.get("status") in ("applying", "restoring"):
                    run.update(status="interrupted", summary="Interrupted operation. Inspect this TV and restore before further cleanup.")
                    atomic_json(path, run)
            return lock
        except Exception:
            if lock:
                lock.close()
            self.mutation_lock.release()
            raise ServiceError("Another process is using this appliance, or its state cannot be locked.") from None

    def _release_mutation(self, lock):
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        self.mutation_lock.release()

    def _refresh_config(self):
        self.config = read_json(self.state / "config.json", {"automatic": {"enabled": False, "cidr": ""}, "enrollments": {}})

    def _config_update(self, update):
        with self.config_lock, (self.state / "configuration.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            configuration = read_json(self.state / "config.json", {"automatic": {"enabled": False, "cidr": ""}, "enrollments": {}})
            update(configuration)
            atomic_json(self.state / "config.json", configuration)
            self.config = configuration
            fcntl.flock(lock, fcntl.LOCK_UN)

    def verify_upstream(self):
        for relative, expected in self.manifest["files"].items():
            path = self.upstream / relative
            if not path.is_file() or path.is_symlink() or file_digest(path) != expected:
                raise ServiceError(f"Pinned source changed or is missing: {relative}. Reinstall a reviewed release before cleanup.")

    def status(self):
        from .discovery import local_networks
        self._refresh_config()
        networks = [{"interface": "demo", "cidr": "192.168.1.0/24"}] if self.demo else local_networks()
        return {"version": __version__, "demo": self.demo, "catalog": self.catalog.summary(),
                "networks": networks, "automatic": dict(self.config["automatic"]),
                "upstream": {"commit": self.manifest["commit"]},
                "launcher": {"available": self.launcher_available()},
                "appliance": read_json(self.state / "appliance.json", {"phase": "idle", "message": "Waiting for automatic mode."}),
                "recipes": self.list_recipes(),
                "automatic_blocks": list(self.config.get("automatic_blocks", {}).values()),
                "limitations": ["Android TV and compatible Fire OS only; other platforms need their own methods.",
                                "A new TV must authorize debugging. USB requires a real ADB device interface.",
                                "Unattended cleanup requires an enrollment or an owner-verified exact firmware recipe.",
                                "Cleanup does not remove every streaming advertisement."]}

    def scan(self, cidr="", include_usb=True):
        from .discovery import discover_networks, validate_network
        if not isinstance(cidr, str) or not isinstance(include_usb, bool):
            raise ServiceError("Choose a local network and whether to include USB devices.")
        if cidr:
            validate_network(cidr)
        if self.demo:
            return {"devices": [{"endpoint": "192.168.1.50:5555", "address": "192.168.1.50", "port": 5555,
                                 "transport": "demo", "status": "Demo TV ready"}]}
        devices, warnings = [], []
        if include_usb and hasattr(self.transport, "usb_devices"):
            try:
                devices.extend(self.transport.usb_devices())
            except Exception as error:
                warnings.append("USB discovery: " + str(error))
        found = discover_networks(cidr or "auto", adb_binary=self.transport.binary, home=self.home)
        devices.extend(found["devices"])
        warnings.extend(found.get("warnings", []))
        networks = found.get("networks", [])
        unique, seen = [], set()
        for item in devices:
            key = (item.get("endpoint"), item.get("transport"), item.get("state"))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return {"devices": unique[:64], "networks": networks, "warnings": warnings,
                "limited": len(unique) > 64}

    def connect(self, endpoint):
        return {"message": self.transport.connect(endpoint)}

    def pair(self, endpoint, code):
        return {"message": self.transport.pair(endpoint, code)}

    def inspect(self, endpoint):
        snapshot = self.transport.snapshot(endpoint)
        warnings = []
        try:
            eligible = self.enrollment_for(snapshot) is not None
        except ServiceError as error:
            warnings.append(str(error))
            eligible = False
        return {"endpoint": endpoint, "identity": public_identity(snapshot),
                "matches": self.catalog.match(snapshot["identity"], set(snapshot["packages"]))[:12],
                "package_count": len(snapshot["packages"]), "disabled_count": len(snapshot["disabled"]),
                "automatic_eligible": eligible, "warnings": warnings}

    def plan(self, endpoint, profile_id):
        self.verify_upstream()
        snapshot = self.transport.snapshot(endpoint)
        target = binding(snapshot)
        profile = self.catalog.get(profile_id)
        if profile.get("valid") is False or profile.get("usable") is False or not profile["packages"]:
            raise ServiceError("This profile is not a usable package list. Read its notes before proceeding.")
        if snapshot["identity"].get("family") not in ("android_tv", "fire_os"):
            raise ServiceError("This device is not a supported Android/Google TV or Fire OS television.")
        rows = package_rows(profile, snapshot, self.recovery.core_packages)
        plan_id = secrets.token_hex(16)
        data = {"id": plan_id, "endpoint": endpoint, "profile": profile,
                "identity": public_identity(snapshot), "packages": rows, "binding": target,
                "signature": state_signature(snapshot), "expires_at": time.time() + 900,
                "upstream_commit": self.manifest["commit"], "warnings": list(profile.get("warnings", [])),
                "automatic_eligible": self.enrollment_for(snapshot) is not None}
        data["warnings"].extend(["A model match is a suggestion. Check this profile's firmware and household notes.",
                                 "Home, updates, voice, casting, input services, user-installed apps and known streaming apps are preserved."])
        atomic_json(self.plans_dir / f"{plan_id}.json", data)
        return self.public_plan(data)

    @staticmethod
    def public_plan(data):
        result = {k: data[k] for k in ("id", "endpoint", "identity", "packages", "expires_at", "warnings", "automatic_eligible")}
        result["profile"] = {k: data["profile"].get(k) for k in ("id", "tv", "os", "notes", "warnings")}
        return result

    def _get_plan(self, plan_id):
        if not re.fullmatch(r"[0-9a-f]{32}", str(plan_id)):
            raise ServiceError("Invalid cleanup plan.")
        data = read_json(self.plans_dir / f"{plan_id}.json")
        if not data or data["expires_at"] < time.time():
            raise ServiceError("This plan expired. Inspect the TV again.")
        if data.get("used"):
            raise ServiceError("This plan has already been used. Inspect the TV again.")
        return data

    def _environment(self, endpoint):
        from .transport import adb_environment, upstream_environment
        env = adb_environment(self.home)
        for key in list(env):
            if key.startswith("FORCE_") or key in ("TVINPUT_ALLOW", "HANDLERS_ALLOW", "STOCK_LAUNCHER", "HOME_KEY_SERVICE", "INPUT_KEY_SERVICE", "TV_PORT"):
                env.pop(key, None)
        env.update(HOME=str(self.home), PYTHONUNBUFFERED="1")
        env.update(upstream_environment(endpoint, self.transport.binary, self.root))
        return env

    def _tool(self, relative, arguments, endpoint, timeout=240):
        if self.demo:
            raise ServiceError("Real tools are unavailable in demo mode.")
        script = self.upstream / relative
        command = ([str(self.upstream / "tools/venv/bin/python"), "-I", str(script)] if script.suffix == ".py"
                   else ["bash", str(script)]) + [str(a) for a in arguments]
        child = subprocess.Popen(command, cwd=self.upstream, env=self._environment(endpoint), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
        try:
            output, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                output, _ = child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                output, _ = child.communicate()
            raise ServiceError("TV operation timed out. Remaining changes were stopped; inspect the recovery result.")
        return subprocess.CompletedProcess(command, child.returncode, output, "")

    def _record_path(self, list_path, endpoint):
        from .transport import upstream_record_tag
        address = upstream_record_tag(endpoint)
        key = hashlib.sha1(str(Path(list_path).resolve()).encode()).hexdigest()[:8]
        return self.home / ".cleanyourtv" / f"recovery-{address}-{key}.json"

    def _checkpoint(self, run):
        atomic_json(self.runs_dir / run["id"] / "run.json", run)

    def _validate_record(self, record_path, list_path, endpoint, snapshot, names):
        from .transport import upstream_endpoint
        try:
            record = self.recovery.load_record(record_path)
        except (SystemExit, AttributeError, TypeError) as error:
            raise ServiceError("The restoration record failed validation. No batch was disabled.") from None
        if record.get("host") != upstream_endpoint(endpoint) or record.get("list") != str(list_path.resolve()) or record.get("home") != snapshot["home"]:
            raise ServiceError("The restoration record does not match this target, list and Home.")
        if not set(names) <= set(record["packages"]) or any(record["packages"].get(name) != "enabled" for name in names):
            raise ServiceError("The restoration record does not preserve the selected packages' enabled state.")
        for name, value in record["packages"].items():
            expected = "absent" if name not in snapshot["packages"] else ("disabled" if name in snapshot["disabled"] else "enabled")
            if value != expected:
                raise ServiceError("A package state in the restoration record differs from the reviewed TV.")
        settings = dict(line.split("=", 1) for line in snapshot["raw"].get("settings_global", "").splitlines() if "=" in line)
        for name in self.recovery.SETTINGS:
            value = str(record.get("settings", {}).get(name, ""))
            if value != settings.get(name, "null") or not (value == "null" or self.recovery.SCALE_RE.fullmatch(value)):
                raise ServiceError("The restoration record did not preserve the TV's animation settings.")
        return record

    def _assert_automatic(self, expected):
        if expected is None:
            return
        self._refresh_config()
        if self.config["automatic"] != expected["configuration"] or not self.config["automatic"]["enabled"]:
            raise ServiceError("Automatic cleanup was turned off or its network changed.")
        if expected.get("recipe"):
            current = self._all_recipes().get(expected["recipe"]["id"])
            if current != expected["recipe"] or not current.get("enabled"):
                raise ServiceError("This firmware recipe was revoked or changed. Automatic cleanup stopped.")
            if expected.get("device") in self.config.get("automatic_blocks", {}):
                raise ServiceError("This TV is paused after an automatic failure. Review it before retrying.")
        elif self.config["enrollments"].get(expected["enrollment"]["binding"]["device"]) != expected["enrollment"]:
            raise ServiceError("This TV's automatic approval changed. No automatic cleanup will continue.")

    def _new_run(self, plan, snapshot, kind="cleanup"):
        run_id = secrets.token_hex(16)
        folder = private_dir(self.runs_dir / run_id)
        atomic_json(folder / "before.json", snapshot)
        atomic_json(folder / "plan.json", plan)
        run = {"id": run_id, "created_at": time.time(), "kind": kind, "demo": self.demo,
               "status": "applying", "endpoint": plan["endpoint"], "binding": binding(snapshot),
               "tv": public_identity(snapshot), "profile_id": plan["profile"]["id"],
               "batches": [], "changed": [], "skipped": [], "errors": [], "warnings": [],
               "before_digest": file_digest(folder / "before.json"),
               "summary": "Backup complete. Applying reviewed cleanup.", "needs_manual_check": True}
        self._checkpoint(run)
        return run, folder

    def apply(self, plan_id, packages, enroll=False, *, _automatic=None):
        lock = self._acquire_mutation()
        try:
            return self._apply(plan_id, packages, enroll, _automatic)
        finally:
            self._release_mutation(lock)

    def _apply(self, plan_id, packages, enroll, automatic=None):
        self._assert_automatic(automatic)
        if not isinstance(enroll, bool):
            raise ServiceError("Automatic enrollment must be on or off.")
        self.verify_upstream()
        plan = self._get_plan(plan_id)
        current_profile = self.catalog.get(plan["profile"]["id"])
        if current_profile["digest"] != plan["profile"]["digest"]:
            raise ServiceError("The profile changed. Create a new plan.")
        if not isinstance(packages, list) or any(not isinstance(p, str) for p in packages):
            raise ServiceError("Select packages from the reviewed plan.")
        requested = set(packages)
        allowed = {r["name"] for r in plan["packages"] if r["installed"] and not r["disabled"] and not r["protected"]}
        if not requested or not requested <= allowed:
            raise ServiceError("The selection is empty or includes a preserved/unavailable package.")
        ordered = [row["name"] for row in plan["packages"] if row["name"] in requested]
        snapshot = self.transport.snapshot(plan["endpoint"])
        if state_signature(snapshot) != plan["signature"]:
            raise ServiceError("The TV, firmware, Home or package state changed. Inspect it again before cleanup.")
        for path in self.runs_dir.glob("*/run.json"):
            previous = read_json(path)
            if previous.get("binding", {}).get("device") == binding(snapshot)["device"] and previous["status"] in ("interrupted", "needs_restore", "applying", "restoring"):
                raise ServiceError("This TV has an unfinished operation. Restore it before starting another cleanup.")
        plan["used"] = True
        atomic_json(self.plans_dir / f"{plan_id}.json", plan)
        run, folder = self._new_run(plan, snapshot)
        try:
            for offset in range(0, len(ordered), 5):
                self._assert_automatic(automatic)
                now = self.transport.snapshot(plan["endpoint"])
                if binding(now) != run["binding"] or now["home"] != snapshot["home"] or set(now["packages"]) != set(snapshot["packages"]) or set(now.get("protected", [])) != set(snapshot.get("protected", [])):
                    raise ServiceError("The target changed during cleanup. Stopping this operation.")
                expected_disabled = set(snapshot["disabled"]) | set(run["changed"])
                if set(now["disabled"]) != expected_disabled:
                    raise ServiceError("Package state changed outside this operation. Stopping cleanup.")
                names = ordered[offset:offset + 5]
                list_path = folder / f"batch-{offset // 5:03d}.txt"
                atomic_bytes(list_path, ("\n".join(names) + "\n").encode("utf-8"))
                batch = {"file": list_path.name, "packages": names, "list_digest": file_digest(list_path), "attempted": True}
                run["batches"].append(batch)
                self._checkpoint(run)
                if self.demo:
                    self.transport.disabled.update(names)
                else:
                    # Capture and validate an upstream record BEFORE permitting its disable script.
                    saved = self._tool("tools/recovery.py", ["--save", list_path], plan["endpoint"])
                    if saved.returncode:
                        raise ServiceError("The TV's restoration record could not be saved. No new batch was disabled.")
                    record_path = self._record_path(list_path, plan["endpoint"])
                    self._validate_record(record_path, list_path, plan["endpoint"], now, names)
                    batch["record_file"] = record_path.name
                    batch["record_digest"] = file_digest(record_path)
                    batch["record_backup"] = f"recovery-{offset // 5:03d}.json"
                    atomic_bytes(folder / batch["record_backup"], record_path.read_bytes())
                    self._checkpoint(run)
                    if binding(self.transport.snapshot(plan["endpoint"])) != run["binding"]:
                        raise ServiceError("The target changed while its recovery record was being saved.")
                    result = self._tool("tools/disable.sh", [list_path], plan["endpoint"])
                    (folder / f"{list_path.stem}.log").write_text(result.stdout, encoding="utf-8")
                    if result.returncode:
                        raise ServiceError("The TV refused part of the cleanup. Restore was requested.")
                after = self.transport.snapshot(plan["endpoint"])
                if binding(after) != run["binding"]:
                    raise ServiceError("A different TV answered after cleanup. No further commands will be sent.")
                run["changed"] = sorted((set(after["disabled"]) - set(snapshot["disabled"])) & requested)
                if (set(after["disabled"]) != expected_disabled | set(names) or after["home"] != snapshot["home"]
                        or set(after["packages"]) != set(snapshot["packages"])
                        or set(after.get("protected", [])) != set(snapshot.get("protected", []))
                        or set(after.get("third_party", [])) != set(snapshot.get("third_party", []))):
                    raise ServiceError("Verification did not confirm every change and the original Home. Restore was requested.")
                self._checkpoint(run)
                try:
                    self.transport.screenshot(plan["endpoint"], folder / f"{list_path.stem}.png")
                except Exception:
                    run["warnings"].append("A screenshot was unavailable; verify the picture using the TV remote.")
            final = self.transport.snapshot(plan["endpoint"])
            if (binding(final) != run["binding"] or final["home"] != snapshot["home"]
                    or set(final["packages"]) != set(snapshot["packages"])
                    or set(final.get("protected", [])) != set(snapshot.get("protected", []))
                    or set(final["disabled"]) != set(snapshot["disabled"]) | requested):
                raise ServiceError("The final TV verification changed. Completion and enrollment were blocked.")
            atomic_json(folder / "after.json", final)
            run["after_digest"] = file_digest(folder / "after.json")
            run["completed_at"] = time.time()
            run.update(status="completed", summary=f"Disabled {len(run['changed'])} selected packages. Check apps, Home and inputs with the remote.")
            self._checkpoint(run)
        except Exception as error:
            run["errors"].append(str(error))
            self._checkpoint(run)
            try:
                self._restore(run, plan["endpoint"], automatic_rollback=True)
            except Exception as recovery_error:
                run.update(status="needs_restore", summary="Cleanup stopped. Restore is still needed when this TV is reachable.")
                run["errors"].append(str(recovery_error))
                self._checkpoint(run)
        if enroll and run["status"] == "completed":
            try:
                self.enroll(final, plan["profile"], ordered)
            except Exception:
                run["warnings"].append("Cleanup completed, but automatic enrollment could not be saved.")
                self._checkpoint(run)
        return self.public_run(run)

    def public_run(self, run):
        return {k: run.get(k) for k in ("id", "created_at", "status", "kind", "tv", "summary", "changed", "skipped", "errors", "warnings", "needs_manual_check", "demo", "endpoint")}

    def list_runs(self):
        runs = [read_json(path) for path in self.runs_dir.glob("*/run.json")]
        return {"runs": [self.public_run(run) for run in sorted(runs, key=lambda r: r["created_at"], reverse=True)[:100]]}

    def restore(self, run_id, endpoint):
        if not re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
            raise ServiceError("Invalid restoration record.")
        lock = self._acquire_mutation()
        run = None
        try:
            self.verify_upstream()
            run = read_json(self.runs_dir / run_id / "run.json")
            if not run or run.get("demo") != self.demo:
                raise ServiceError("This restoration record is unavailable in the current mode.")
            return self._restore(run, endpoint)
        except Exception as error:
            if run and run.get("status") == "restoring":
                run.update(status="needs_restore", summary="Restoration stopped before verification completed.")
                run["errors"].append(str(error))
                self._checkpoint(run)
            raise
        finally:
            self._release_mutation(lock)

    def _restore(self, run, endpoint, automatic_rollback=False):
        snapshot = self.transport.identity_snapshot(endpoint)
        if binding(snapshot) != run["binding"]:
            raise ServiceError("This is a different TV or firmware. The restoration was blocked.")
        folder = self.runs_dir / run["id"]
        if not (folder / "before.json").is_file() or file_digest(folder / "before.json") != run.get("before_digest"):
            raise ServiceError("The original TV backup is missing or changed. Restoration was blocked.")
        original = read_json(folder / "before.json")
        if binding(original) != run["binding"] or not self.recovery.HOME_RE.fullmatch(original.get("home", "")):
            raise ServiceError("The original TV backup does not match this restoration record.")
        run["status"] = "restoring"
        self._checkpoint(run)
        for batch in reversed(run["batches"]):
            if binding(self.transport.identity_snapshot(endpoint)) != run["binding"]:
                raise ServiceError("A different TV or firmware answered during restoration. No further commands were sent.")
            list_path = folder / batch["file"]
            if file_digest(list_path) != batch["list_digest"]:
                raise ServiceError("A saved cleanup list changed. Restoration was blocked.")
            if self.demo:
                for package in batch["packages"]:
                    if package not in original["disabled"]:
                        self.transport.disabled.discard(package)
                continue
            if not batch.get("record_file"):
                # No disable call was allowed before saving the record.
                continue
            source = folder / batch["record_backup"]
            if not source.is_file() or file_digest(source) != batch["record_digest"]:
                raise ServiceError("A restoration record is missing or changed. The fallback that enables everything is blocked.")
            # Upstream keys recovery by IP; remap only after verifying stable device AND firmware.
            destination = self._record_path(list_path, endpoint)
            if destination != source:
                if destination.exists() and file_digest(destination) != batch["record_digest"]:
                    raise ServiceError("This address has a conflicting restoration record.")
                private_dir(destination.parent)
                atomic_bytes(destination, source.read_bytes())
            result = self._tool("tools/restore.sh", [list_path, original["home"].split("/")[0]], endpoint)
            (folder / f"restore-{list_path.stem}.log").write_text(result.stdout, encoding="utf-8")
            if result.returncode:
                raise ServiceError("The TV did not complete restoration. Keep its backup and check the TV.")
        after = self.transport.snapshot(endpoint)
        if binding(after) != run["binding"]:
            raise ServiceError("The device changed during restoration.")
        affected = {name for batch in run["batches"] for name in batch["packages"]}
        if set(after["disabled"]) & affected != set(original["disabled"]) & affected or after["home"] != original["home"]:
            raise ServiceError("Restoration could not verify the previous package states and Home.")
        atomic_json(folder / "restored.json", after)
        run["restored_digest"] = file_digest(folder / "restored.json")
        run["restored_at"] = time.time()
        if set(after["disabled"]) != set(original["disabled"]):
            run["warnings"].append("Other package states differ from the backup. Only this operation's recorded changes were restored.")
        run.update(status="rolled_back" if automatic_rollback else "restored",
                   summary="The recorded package states and Home were restored. Check the TV with its remote.")
        self._checkpoint(run)
        self._config_update(lambda config: config["enrollments"].pop(run["binding"]["device"], None))
        return self.public_run(run)

    def enroll(self, snapshot, profile, packages):
        value = {"binding": binding(snapshot), "profile_id": profile["id"], "profile_digest": profile["digest"],
                 "packages": sorted(packages), "installed": sorted(snapshot["packages"]),
                 "home": snapshot["home"], "protected": sorted(snapshot.get("protected", [])),
                 "third_party": sorted(snapshot.get("third_party", [])),
                 "baseline_disabled": sorted(set(snapshot["disabled"]) - set(packages)),
                 "upstream_commit": self.manifest["commit"], "created_at": time.time()}
        self._config_update(lambda config: config["enrollments"].__setitem__(value["binding"]["device"], value))

    def enrollment_for(self, snapshot):
        self._refresh_config()
        target = binding(snapshot)
        item = self.config["enrollments"].get(target["device"])
        if not item or item["binding"] != target or item["installed"] != sorted(snapshot["packages"]) or item["upstream_commit"] != self.manifest["commit"]:
            return None
        if item.get("home") != snapshot["home"] or item.get("protected") != sorted(snapshot.get("protected", [])) or item.get("third_party") != sorted(snapshot.get("third_party", [])):
            return None
        if item.get("baseline_disabled") != sorted(set(snapshot["disabled"]) - set(item["packages"])):
            return None
        profile = self.catalog.get(item["profile_id"])
        if profile["digest"] != item["profile_digest"]:
            return None
        for path in self.runs_dir.glob("*/run.json"):
            run = read_json(path)
            if run.get("binding", {}).get("device") == target["device"] and run["status"] in ("needs_restore", "interrupted", "applying", "restoring"):
                return None
        return item

    def _all_recipes(self):
        """Load local and reviewed release recipes. Invalid data never authorizes a TV."""
        from .recipes import validate_recipe, RecipeError
        items = {}
        for path in sorted((self.root / "recipes").glob("*.json")):
            if path.is_symlink() or path.stat().st_size > 262144:
                raise ServiceError("A bundled recipe has an invalid file type or size.")
            try:
                item = validate_recipe(read_json(path))
            except (RecipeError, ValueError, TypeError) as error:
                raise ServiceError("A bundled recipe failed validation: " + str(error)) from None
            if item["id"] in items:
                raise ServiceError("Duplicate recipe IDs are present. No unattended cleanup is allowed.")
            items[item["id"]] = item
        for key, raw in self.config.get("recipes", {}).items():
            try:
                item = validate_recipe(raw)
            except (RecipeError, ValueError, TypeError) as error:
                raise ServiceError("A local recipe failed validation: " + str(error)) from None
            if key != item["id"] or key in items:
                raise ServiceError("A local recipe ID is inconsistent or duplicated.")
            items[key] = item
        for key, item in items.items():
            if key in self.config.get("recipe_switches", {}):
                enabled = self.config["recipe_switches"][key]
                if not isinstance(enabled, bool):
                    raise ServiceError("A recipe enablement value is invalid.")
                item["enabled"] = enabled
        return items

    def list_recipes(self):
        from .recipes import public_recipe
        self._refresh_config()
        try:
            return {"recipes": [public_recipe(item) for item in self._all_recipes().values()], "errors": []}
        except ServiceError as error:
            return {"recipes": [], "errors": [str(error)]}

    def create_recipe(self, run_id, checks, notes):
        """Owner attestation after a completed cleanup AND recorded restore test."""
        from .recipes import build_recipe, public_recipe
        if not re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
            raise ServiceError("Choose a completed cleanup and restore test.")
        lock = self._acquire_mutation()
        try:
            self.verify_upstream()
            folder = self.runs_dir / run_id
            run = read_json(folder / "run.json", {})
            if run.get("status") != "restored" or run.get("demo") != self.demo or run.get("kind") != "cleanup" or not run.get("changed"):
                raise ServiceError("A recipe requires a successful cleanup followed by a successful manual restore test.")
            snapshots = {}
            for name in ("before", "after", "restored"):
                path = folder / f"{name}.json"
                if not path.is_file() or file_digest(path) != run.get(name + "_digest"):
                    raise ServiceError("The cleanup and restore evidence is missing or changed. Run a new supervised test.")
                snapshots[name] = read_json(path)
                if binding(snapshots[name]) != run["binding"]:
                    raise ServiceError("The test evidence belongs to a different TV or firmware.")
            before, after, restored = (snapshots[name] for name in ("before", "after", "restored"))
            if state_signature(before) != state_signature(restored):
                raise ServiceError("The restore test did not return the exact original state.")
            selected = sorted(run["changed"])
            if set(after["disabled"]) != set(before["disabled"]) | set(selected) or after["home"] != before["home"]:
                raise ServiceError("The cleanup evidence does not match the selected packages and Home.")
            plan = read_json(folder / "plan.json")
            if not plan or plan.get("upstream_commit") != self.manifest["commit"]:
                raise ServiceError("The test used another upstream release. Revalidate it before approving a recipe.")
            profile = self.catalog.get(run["profile_id"])
            if profile["digest"] != plan.get("profile", {}).get("digest"):
                raise ServiceError("The tested package list has changed.")
            allowed = {row["name"] for row in package_rows(profile, before, self.recovery.core_packages)
                       if row["installed"] and not row["protected"] and not row["disabled"]}
            if not set(selected) <= allowed:
                raise ServiceError("The test selection includes a protected or unavailable package.")
            item = build_recipe(profile, before, selected, self.manifest["commit"], checks, notes, demo=self.demo)
            evidence = {"run_id": run_id, "before_digest": run["before_digest"],
                        "after_digest": run["after_digest"], "restored_digest": run["restored_digest"]}
            def save(config):
                config.setdefault("recipes", {})[item["id"]] = item
                config.setdefault("recipe_evidence", {})[item["id"]] = evidence
            self._config_update(save)
            return public_recipe(item)
        finally:
            self._release_mutation(lock)

    def import_recipe(self, value):
        """Explicit owner import of data, with no scripts or automatic downloads."""
        from .recipes import validate_recipe, public_recipe
        self.verify_upstream()
        item = validate_recipe(value)
        if item["demo"] != self.demo or item["upstream_commit"] != self.manifest["commit"]:
            raise ServiceError("This recipe is for another release or simulation mode.")
        profile = self.catalog.get(item["profile_id"])
        if profile["digest"] != item["profile_digest"]:
            raise ServiceError("This recipe's package list differs from the installed release.")
        self._refresh_config()
        if item["id"] in self._all_recipes():
            raise ServiceError("This recipe ID already exists. Disable the old recipe before preparing a distinct revision.")
        self._config_update(lambda config: config.setdefault("recipes", {}).__setitem__(item["id"], item))
        return public_recipe(item)

    def export_recipe(self, recipe_id):
        self._refresh_config()
        item = self._all_recipes().get(recipe_id)
        if not item:
            raise ServiceError("This recipe does not exist.")
        return item

    def set_recipe_enabled(self, recipe_id, enabled):
        from .recipes import public_recipe
        if not isinstance(enabled, bool):
            raise ServiceError("Recipe enablement must be on or off.")
        self._refresh_config()
        item = self._all_recipes().get(recipe_id)
        if not item:
            raise ServiceError("This recipe does not exist.")
        self._config_update(lambda config: config.setdefault("recipe_switches", {}).__setitem__(recipe_id, enabled))
        item["enabled"] = enabled
        return public_recipe(item)

    def support_report(self, endpoint):
        """Deliberately exclude serials, Android IDs, addresses, settings and user apps."""
        snapshot = self.transport.snapshot(endpoint)
        identity = {key: snapshot["identity"].get(key, "") for key in
                    ("manufacturer", "brand", "model", "device", "product", "android", "sdk", "build", "fingerprint", "family")}
        system_packages = sorted(set(snapshot["packages"]) - set(snapshot.get("third_party", [])))
        return {"schema_version": 1, "controller_version": __version__, "demo": self.demo,
                "upstream_commit": self.manifest["commit"], "identity": identity,
                "system_packages": system_packages, "home": snapshot["home"],
                "protected": sorted(set(snapshot.get("protected", [])) & set(system_packages)),
                "disabled_system_packages": sorted(set(snapshot["disabled"]) & set(system_packages)),
                "matches": [{"id": item["id"], "score": item["score"], "exact_model": item.get("exact_model", False)}
                            for item in self.catalog.match(snapshot["identity"], set(snapshot["packages"]))[:12]],
                "notice": "Read-only support evidence. This report does not approve an unattended cleanup recipe."}

    def clear_automatic_block(self, endpoint):
        snapshot = self.transport.snapshot(endpoint)
        key = device_key(snapshot)
        for path in self.runs_dir.glob("*/run.json"):
            run = read_json(path)
            if run.get("binding", {}).get("device") == key and run.get("status") in ("needs_restore", "interrupted", "applying", "restoring"):
                raise ServiceError("Restore this TV's unfinished run before resuming automatic cleanup.")
        self._config_update(lambda config: config.setdefault("automatic_blocks", {}).pop(key, None))
        return {"status": "cleared", "tv": public_identity(snapshot)}

    def set_automatic(self, enabled, cidr="", network_mode="manual", usb=True, policy="enrolled"):
        from .discovery import validate_network
        if not isinstance(enabled, bool) or not isinstance(usb, bool):
            raise ServiceError("Automatic mode and USB discovery must be on or off.")
        if network_mode not in ("manual", "auto", "usb") or policy not in ("enrolled", "recipes") or not isinstance(cidr, str):
            raise ServiceError("Choose a valid network mode and automatic cleanup policy.")
        if enabled and network_mode == "manual":
            validate_network(cidr)
        if enabled and network_mode == "usb" and not usb:
            raise ServiceError("USB-only operation requires USB discovery.")
        value = {"enabled": enabled, "cidr": cidr if network_mode == "manual" else "",
                 "network_mode": network_mode, "usb": usb, "policy": policy}
        self._config_update(lambda config: config.__setitem__("automatic", value))
        self._appliance_state("armed" if enabled else "paused", "Automatic cleanup is armed." if enabled else "Automatic cleanup is off.")
        return self.config["automatic"]

    def _appliance_state(self, phase, message, results=None):
        value = {"phase": phase, "message": message, "updated_at": time.time(), "demo": self.demo}
        if results is not None:
            value["results"] = results
        atomic_json(self.state / "appliance.json", value)

    def automatic_pass(self):
        if not self.automatic_lock.acquire(blocking=False):
            return {"results": [], "status": "busy"}
        lock = None
        try:
            lock = (self.state / "automatic.lock").open("a+")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"results": [], "status": "busy"}
            return self._automatic_pass()
        except Exception as error:
            self._appliance_state("attention", str(error))
            raise
        finally:
            if lock:
                lock.close()
            self.automatic_lock.release()

    def _automatic_pass(self):
        from .recipes import recipe_matches
        self._refresh_config()
        configuration = dict(self.config["automatic"])
        if not configuration["enabled"]:
            return {"results": []}
        self.verify_upstream()
        self._appliance_state("scanning", "Looking for an authorized TV on the connected network or USB.")
        if configuration.get("network_mode") == "usb":
            devices = self.transport.usb_devices() if not self.demo else self.scan("")["devices"]
        else:
            cidr = "" if configuration.get("network_mode") == "auto" else configuration.get("cidr", "")
            devices = self.scan(cidr)["devices"]
        if not configuration.get("usb", True):
            devices = [item for item in devices if item.get("transport") != "usb"]
        results, eligible, seen = [], [], set()
        recipes = self._all_recipes() if configuration.get("policy") == "recipes" else {}
        for candidate in devices[:32]:
            self._refresh_config()
            if self.config["automatic"] != configuration:
                break
            if candidate.get("transport") in ("pairing", "adb_tls_pairing"):
                results.append({"endpoint": candidate.get("endpoint"), "status": "pairing_required"})
                continue
            endpoint = candidate.get("endpoint")
            if not endpoint:
                results.append({"status": "connection_unavailable", "message": candidate.get("status", "USB access is unavailable.")})
                continue
            if candidate.get("state") in ("unauthorized", "offline", "no permissions"):
                results.append({"endpoint": endpoint, "status": "waiting_for_authorization" if candidate["state"] == "unauthorized" else "connection_unavailable"})
                continue
            try:
                self.transport.connect(endpoint)
                snapshot = self.transport.snapshot(endpoint)
                key = device_key(snapshot)
                if key in seen:
                    continue
                seen.add(key)
                if key in self.config.get("automatic_blocks", {}):
                    results.append({"endpoint": endpoint, "status": "paused_after_failure"})
                    continue
                item = self.enrollment_for(snapshot)
                recipe = None
                if not item and recipes:
                    matches = []
                    for value in recipes.values():
                        try:
                            profile = self.catalog.get(value["profile_id"])
                        except (KeyError, ValueError):
                            continue
                        if recipe_matches(value, snapshot, profile, self.manifest["commit"], demo=self.demo):
                            matches.append(value)
                    if len(matches) > 1:
                        results.append({"endpoint": endpoint, "status": "ambiguous_recipes", "message": "More than one enabled recipe matches. Disable the obsolete recipe."})
                        continue
                    if matches:
                        recipe = matches[0]
                if not item and not recipe:
                    results.append({"endpoint": endpoint, "status": "review_required"})
                    continue
                approval = item or recipe
                wanted = sorted(set(approval["packages"]) - set(snapshot["disabled"]))
                if not wanted:
                    results.append({"endpoint": endpoint, "status": "already_clean"})
                    continue
                eligible.append((endpoint, snapshot, item, recipe, wanted))
            except Exception as error:
                status = "waiting_for_authorization" if any(word in str(error).lower() for word in ("authoriz", "approve", "pair")) else "stopped"
                results.append({"endpoint": endpoint, "status": status, "error": str(error)})
        if len(devices) > 32:
            results.append({"status": "discovery_limit", "message": "Too many debugging services. Select a smaller network or use USB-only mode."})
            eligible = []
        if len(eligible) > 1:
            results.extend({"endpoint": item[0], "status": "multiple_tvs", "message": "More than one TV is eligible. Connect one TV or use a reviewed manual cleanup."} for item in eligible)
            eligible = []
        for endpoint, snapshot, item, recipe, wanted in eligible:
            attempted = False
            try:
                expected = {"configuration": configuration, "device": device_key(snapshot)}
                expected["enrollment" if item else "recipe"] = item or recipe
                self._assert_automatic(expected)
                plan = self.plan(endpoint, (item or recipe)["profile_id"])
                self._appliance_state("cleaning", "Backing up and applying the approved cleanup.", results)
                attempted = True
                result = self.apply(plan["id"], wanted, _automatic=expected)
                result["endpoint"] = endpoint
                results.append(result)
                if result["status"] != "completed":
                    self._block_automatic(snapshot, result["summary"], result.get("id"))
            except Exception as error:
                if item:
                    self._config_update(lambda config: config["enrollments"].pop(item["binding"]["device"], None))
                if attempted:
                    self._block_automatic(snapshot, str(error))
                results.append({"endpoint": endpoint, "status": "stopped", "error": str(error)})
        good = results and all(item.get("status") in ("completed", "already_clean") for item in results)
        phase = "complete" if good else ("attention" if results else "waiting_for_connection")
        message = "Approved cleanup complete; the result is saved." if good else ("A TV needs attention. See the connection results." if results else "No TV debugging connection is available yet.")
        self._appliance_state(phase, message, results)
        return {"results": results}

    def _block_automatic(self, snapshot, reason, run_id=None):
        key = device_key(snapshot)
        value = {"device": key, "tv": public_identity(snapshot), "reason": reason,
                 "run_id": run_id, "created_at": time.time()}
        self._config_update(lambda config: config.setdefault("automatic_blocks", {}).__setitem__(key, value))

    def launcher_available(self):
        artifact = read_json(self.root / "launcher-artifact.json")
        apk = self.upstream / "launcher/app/build/outputs/apk/release/app-release.apk"
        return bool(artifact and apk.is_file() and artifact.get("upstream_commit") == self.manifest["commit"]
                    and artifact.get("sha256") == file_digest(apk))
