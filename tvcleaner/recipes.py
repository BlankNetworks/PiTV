"""Strict, data-only cleanup recipes attested by the appliance's owner.

A recipe is reusable across distinct devices only when the exact model, firmware,
system inventory and protected state agree.  It is not a manufacturer certificate
or a signature from a trusted publisher.  The service must establish the linked
successful cleanup/restore evidence and obtain the owner's functional checks
before calling :func:`build_recipe`.

This module performs no I/O, launches no commands and loads no executable recipe
content.  A content digest detects accidental or unapproved edits to stored data;
it is not a substitute for authenticating an imported recipe.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import secrets
import time
from typing import Any

from .policy import PACKAGE, package_rows


class RecipeError(ValueError):
    """A recipe or the evidence used to create it is incomplete or inconsistent."""


SCHEMA_VERSION = 1
SELECTOR_FIELDS = (
    "manufacturer", "brand", "model", "device", "product", "android", "sdk",
    "build", "fingerprint", "family",
)
CHECK_FIELDS = ("home", "inputs", "remote", "streaming", "network", "reboot", "restore")
_RECIPE_FIELDS = frozenset({
    "schema_version", "id", "created_at", "enabled", "demo", "profile_id",
    "profile_digest", "upstream_commit", "selector", "packages", "system_packages",
    "home", "protected", "baseline_disabled", "attestation", "digest",
})
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/debloat[A-Za-z0-9_.-]*\.txt\Z")
_ACTIVITY = re.compile(r"\.?[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*\Z")
_VERSION = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)*\Z")
_SDK = re.compile(r"[1-9][0-9]{0,2}\Z")
_PATTERN_CHARACTERS = frozenset("*?[]{}()^$|\\+")
_UNKNOWN = frozenset({"unknown", "null", "none", "undefined", "default", ".", "-"})
_MAX_PACKAGES = 10_000
_GENERIC_CORE = frozenset({
    "android", "com.android.settings", "com.android.tv.settings", "com.android.shell",
    "com.android.systemui", "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
})


def _object(value: Any, fields: set[str] | frozenset[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise RecipeError(f"{label} must contain exactly the supported fields")
    return value


def _text(value: Any, label: str, *, maximum: int = 1024) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > maximum or not value.isprintable()):
        raise RecipeError(f"{label} must be a nonempty, bounded, literal string")
    return value


def _selector(value: Any) -> dict[str, str]:
    identity = _object(value, set(SELECTOR_FIELDS), "Recipe selector")
    for key in SELECTOR_FIELDS:
        text = _text(identity[key], f"Selector {key}")
        if text.casefold() in _UNKNOWN or any(char in _PATTERN_CHARACTERS for char in text):
            raise RecipeError(f"Selector {key} must identify one exact value, without patterns")
    if not _VERSION.fullmatch(identity["android"]) or not _SDK.fullmatch(identity["sdk"]):
        raise RecipeError("The Android release and SDK must contain explicit version numbers")
    if identity["family"] not in ("android_tv", "fire_os"):
        raise RecipeError("Recipes are supported only for Android TV or compatible Fire OS")
    return dict(identity)


def _packages(value: Any, label: str, *, allow_empty: bool = True,
              allow_android: bool = True, canonical: bool = False) -> list[str]:
    if type(value) is not list or len(value) > _MAX_PACKAGES or (not value and not allow_empty):
        raise RecipeError(f"{label} must be a bounded list of package names")
    seen: set[str] = set()
    for name in value:
        if (type(name) is not str or len(name) > 255
                or not ((allow_android and name == "android") or PACKAGE.fullmatch(name))):
            raise RecipeError(f"{label} contains an invalid package name")
        if name in seen:
            raise RecipeError(f"{label} contains duplicate package names")
        seen.add(name)
    ordered = sorted(seen)
    if canonical and value != ordered:
        raise RecipeError(f"{label} must be sorted in canonical order")
    return ordered


def _home(value: Any) -> tuple[str, str]:
    text = _text(value, "Home component", maximum=512)
    if text.count("/") != 1:
        raise RecipeError("Home must be one fully identified package/activity component")
    package, activity = text.split("/", 1)
    if not PACKAGE.fullmatch(package) or not _ACTIVITY.fullmatch(activity):
        raise RecipeError("Home contains an invalid package/activity component")
    if activity.endswith(("ResolverActivity", "ChooserActivity")):
        raise RecipeError("A chooser is not a verified default Home activity")
    return text, package


def _checks(value: Any) -> dict[str, bool]:
    checks = _object(value, set(CHECK_FIELDS), "Owner checks")
    if any(type(checks[key]) is not bool or checks[key] is not True for key in CHECK_FIELDS):
        raise RecipeError("The owner must explicitly confirm every functional and restore check")
    return dict(checks)


def _notes(value: Any) -> str:
    if (type(value) is not str or len(value) > 4096
            or any(not char.isprintable() and char not in "\n\t" for char in value)):
        raise RecipeError("Owner notes must be plain text of at most 4096 characters")
    return value


def _commit(value: Any) -> str:
    if type(value) is not str or not _HEX_40.fullmatch(value):
        raise RecipeError("The pinned upstream commit must be a full lowercase Git SHA-1")
    return value


def _profile(profile: Any) -> tuple[str, str, set[str]]:
    if type(profile) is not dict or profile.get("usable") is not True:
        raise RecipeError("The catalog profile must be usable")
    profile_id = profile.get("id")
    if type(profile_id) is not str or not _PROFILE_ID.fullmatch(profile_id):
        raise RecipeError("The profile ID must name one catalog cleanup list")
    folder, _ = profile_id.split("/", 1)
    if folder in (".", ".."):
        raise RecipeError("The profile ID cannot contain a path traversal")
    digest = profile.get("digest")
    if type(digest) is not str or not _HEX_64.fullmatch(digest):
        raise RecipeError("The profile must have a complete SHA-256 content digest")
    entries = profile.get("packages")
    if type(entries) is not list or any(type(entry) is not dict or "name" not in entry for entry in entries):
        raise RecipeError("The profile package list is malformed")
    names = _packages([entry["name"] for entry in entries], "Profile packages", allow_android=False,
                      allow_empty=False)
    return profile_id, digest, set(names)


def _snapshot(snapshot: Any) -> dict[str, Any]:
    required = {"identity", "packages", "disabled", "third_party", "home", "protected"}
    if type(snapshot) is not dict or not required <= set(snapshot):
        raise RecipeError("The TV snapshot is incomplete")
    identity = snapshot["identity"]
    if type(identity) is not dict or not set(SELECTOR_FIELDS) <= set(identity):
        raise RecipeError("The TV identity is incomplete")
    selector = _selector({key: identity[key] for key in SELECTOR_FIELDS})
    installed = _packages(snapshot["packages"], "Installed packages", allow_empty=False)
    disabled = _packages(snapshot["disabled"], "Disabled packages")
    third_party = _packages(snapshot["third_party"], "Third-party packages")
    protected = _packages(snapshot["protected"], "Protected packages", allow_empty=False)
    home, home_package = _home(snapshot["home"])
    present = set(installed)
    if not set(disabled) <= present or not set(third_party) <= present or not set(protected) <= present:
        raise RecipeError("The snapshot package inventories disagree")
    if (home_package not in present or home_package not in protected
            or home_package in disabled):
        raise RecipeError("The current Home app must be installed, enabled and protected")
    if "android" in third_party:
        raise RecipeError("The Android framework cannot be a third-party package")
    system = sorted(present - set(third_party))
    if not system:
        raise RecipeError("The TV must provide a nonempty system package inventory")
    return {"selector": selector, "packages": installed, "disabled": disabled,
            "third_party": third_party, "system_packages": system, "home": home,
            "protected": protected}


def _selection_allowed(names: list[str], profile: dict, snapshot: dict) -> None:
    selected = set(names)
    if not selected <= set(snapshot["system_packages"]):
        raise RecipeError("Selected packages must be installed system packages")
    if selected & (set(snapshot["protected"]) | set(snapshot["third_party"]) | _GENERIC_CORE):
        raise RecipeError("A protected or user-installed package cannot enter a recipe")
    # Apply the controller's static protections here as a second line of defense.
    # Its pinned upstream core list is checked by the service before calling us.
    rows = package_rows(profile, snapshot, lambda _names: False)
    if any(row["name"] in selected and row["protected"] for row in rows):
        raise RecipeError("A preserved TV function or streaming app cannot enter a recipe")


def _content_digest(recipe: dict) -> str:
    content = {key: value for key, value in recipe.items() if key not in ("digest", "enabled")}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_recipe(profile: dict, snapshot: dict, packages: list[str], upstream_commit: str,
                 checks: dict, notes: str, *, demo: bool = False) -> dict:
    """Build a recipe after the service verifies cleanup/restore evidence.

    ``checks`` is an owner's attestation, never an independent certificate.  This
    function deliberately does not invent validation evidence or ship a starter
    allowlist.  Serial numbers, Android IDs and raw device data are not copied.
    """
    if type(demo) is not bool:
        raise RecipeError("Demo mode must be a boolean")
    profile_id, profile_digest, profile_names = _profile(profile)
    observed = _snapshot(snapshot)
    selected = _packages(packages, "Selected packages", allow_empty=False, allow_android=False)
    if not set(selected) <= profile_names:
        raise RecipeError("Every selected package must belong to the pinned catalog profile")
    _selection_allowed(selected, profile, observed)
    recipe = {
        "schema_version": SCHEMA_VERSION,
        "id": secrets.token_hex(16),
        "created_at": int(time.time()),
        "enabled": True,
        "demo": demo,
        "profile_id": profile_id,
        "profile_digest": profile_digest,
        "upstream_commit": _commit(upstream_commit),
        "selector": observed["selector"],
        "packages": selected,
        "system_packages": observed["system_packages"],
        "home": observed["home"],
        "protected": observed["protected"],
        "baseline_disabled": sorted(set(observed["disabled"]) - set(selected)),
        "attestation": {"kind": "owner_attested", "checks": _checks(checks), "notes": _notes(notes)},
    }
    recipe["digest"] = _content_digest(recipe)
    return validate_recipe(recipe)


def validate_recipe(recipe: dict) -> dict:
    """Return a private copy of a valid recipe; reject unknown or altered fields."""
    value = _object(recipe, _RECIPE_FIELDS, "Recipe")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise RecipeError("Unsupported recipe schema version")
    if type(value["id"]) is not str or not _HEX_32.fullmatch(value["id"]):
        raise RecipeError("Recipe ID must be 32 lowercase hexadecimal characters")
    if type(value["created_at"]) is not int or not 0 < value["created_at"] <= 253402300799:
        raise RecipeError("Recipe creation time must be an integer UTC timestamp")
    if type(value["enabled"]) is not bool or type(value["demo"]) is not bool:
        raise RecipeError("Recipe enabled and demo flags must be booleans")
    _selector(value["selector"])
    _commit(value["upstream_commit"])
    selected = _packages(value["packages"], "Selected packages", allow_empty=False,
                         allow_android=False, canonical=True)
    system = _packages(value["system_packages"], "System packages", allow_empty=False, canonical=True)
    protected = _packages(value["protected"], "Protected packages", allow_empty=False, canonical=True)
    baseline = _packages(value["baseline_disabled"], "Baseline disabled packages", canonical=True)
    home, home_package = _home(value["home"])
    if set(selected) & set(baseline) or home_package in baseline or home_package not in protected:
        raise RecipeError("The recipe Home or baseline disabled state is inconsistent")
    profile = {"id": value["profile_id"], "digest": value["profile_digest"], "usable": True,
               "packages": [{"name": name} for name in selected]}
    _profile(profile)
    # User-installed Home/input services may legitimately be protected without
    # appearing in the system inventory. Their presence is checked on matching.
    observed = {"packages": system, "system_packages": system, "disabled": baseline,
                "third_party": [], "protected": protected, "home": home}
    _selection_allowed(selected, profile, observed)
    attestation = _object(value["attestation"], {"kind", "checks", "notes"}, "Owner attestation")
    if attestation["kind"] != "owner_attested":
        raise RecipeError("Only an explicit owner attestation is supported")
    _checks(attestation["checks"])
    _notes(attestation["notes"])
    if (type(value["digest"]) is not str or not _HEX_64.fullmatch(value["digest"])
            or value["digest"] != _content_digest(value)):
        raise RecipeError("The recipe content digest does not match")
    return deepcopy(value)


def recipe_matches(recipe: dict, snapshot: dict, profile: dict, upstream_commit: str,
                   *, demo: bool = False) -> bool:
    """Fail closed unless an enabled recipe exactly fits the observed TV state."""
    try:
        if type(demo) is not bool:
            return False
        value = validate_recipe(recipe)
        if not value["enabled"] or value["demo"] != demo:
            return False
        profile_id, profile_digest, profile_names = _profile(profile)
        if (value["profile_id"] != profile_id or value["profile_digest"] != profile_digest
                or value["upstream_commit"] != _commit(upstream_commit)):
            return False
        observed = _snapshot(snapshot)
        selected = value["packages"]
        if not set(selected) <= profile_names:
            return False
        _selection_allowed(selected, profile, observed)
        return (
            value["selector"] == observed["selector"]
            and value["system_packages"] == observed["system_packages"]
            and value["home"] == observed["home"]
            and value["protected"] == observed["protected"]
            and value["baseline_disabled"] == sorted(set(observed["disabled"]) - set(selected))
        )
    except (RecipeError, KeyError, TypeError, ValueError):
        return False


def public_recipe(recipe: dict) -> dict:
    """Expose useful owner controls without publishing the full app inventory."""
    value = validate_recipe(recipe)
    keys = ("schema_version", "id", "created_at", "enabled", "demo", "profile_id",
            "profile_digest", "upstream_commit", "selector", "packages", "home",
            "attestation", "digest")
    result = {key: value[key] for key in keys}
    result.update(package_count=len(value["packages"]), system_package_count=len(value["system_packages"]),
                  protected_count=len(value["protected"]), baseline_disabled_count=len(value["baseline_disabled"]))
    return result
