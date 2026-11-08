"""Read-only import of pinned CleanYourTV examples and advisory TV matching.

A community example records choices made for a particular television and household.
Neither an exact model label nor a package overlap is permission to execute it.  The
controller must independently protect essential packages and approve a concrete plan.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

# Match the complete active portion of a line, never the first whitespace token.
PACKAGE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+\Z")
_MAX_FILE_BYTES = 2 * 1024 * 1024
_ADVISORY = (
    "Community example: firmware, region and household choices may differ. "
    "A matching profile is a suggestion, not authorization to modify the TV."
)
_GENERIC_MODEL_TOKENS = frozenset({
    "smart", "smarttv", "smarttelevision", "tv", "television", "android", "androidtv",
    "google", "googletv", "pro", "plus", "max", "ultra", "box", "tvbox", "aosp",
    "4k", "8k", "2k", "1080p", "720p", "hd", "full", "fhd", "uhd", "led", "lcd",
    "oled", "qled", "ffm", "ffi", "atv", "unknown", "default", "null", "none",
})
_STOP_VALUES = frozenset({"and", "the", "is", "a", "an", "on", "with", "same", "unknown", "null", "none"})
_WARNING_RE = re.compile(
    r"\b(?:warn(?:ing)?|caution|risk|break(?:s|ing)?|broken|danger|must|"
    r"do not|don't|never|recovery|kept|keep|optional|only|choice|untested)\b", re.I
)
_FIELD_RE = re.compile(
    r"\b(device(?:\s+codename)?|codename|product)(?:\s*(?::|=)\s*|\s+(?:is\s+)?)"
    r"(?:\"([^\"\n]+)\"|'([^'\n]+)'|`([^`\n]+)`|([A-Za-z0-9][A-Za-z0-9_.-]*))", re.I
)
_OS_RE = re.compile(
    r"\b(?:Android(?:\s+TV)?|Google\s+TV)\s*(?:version\s*)?"
    r"(\d+(?:\.\d+)*)(?:\s*((?:(?:and|,|/)\s*\d+(?:\.\d+)*\s*)+))?", re.I
)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _phrase_in(needle: str, haystack: str) -> bool:
    """Case/punctuation-normalized whole-token match, without partial SKU matches."""
    n, h = _tokens(needle), _tokens(haystack)
    return bool(n) and any(h[i:i + len(n)] == n for i in range(len(h) - len(n) + 1))


def _specific_model(model: str) -> bool:
    tokens = _tokens(model)
    if not tokens or len("".join(tokens)) < 3 or not any(c.isalpha() for c in "".join(tokens)):
        return False
    distinctive = [t for t in tokens if t not in _GENERIC_MODEL_TOKENS and t not in {"0", "1"}]
    if not distinctive:
        return False
    # Generic smart-TV model strings are shared across many unrelated retail SKUs.
    if "smarttv" in tokens or ("smart" in tokens and "tv" in tokens):
        return any(any(c.isalpha() for c in t) and any(c.isdigit() for c in t) for t in distinctive)
    return True


def _android_versions(text: str) -> set[int]:
    versions: set[int] = set()
    for match in _OS_RE.finditer(text):
        versions.add(int(match.group(1).split(".", 1)[0]))
        if match.group(2):
            versions.update(int(n) for n in re.findall(r"\b(\d+)(?:\.\d+)*", match.group(2)))
    return versions


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


class Catalog:
    """An immutable in-memory catalog. Construction does not execute upstream code."""

    def __init__(self, upstream: Path):
        self.upstream = Path(upstream).resolve()
        self._profiles: dict[str, dict[str, Any]] = {}
        self._evidence: dict[str, dict[str, Any]] = {}
        self._warnings: list[str] = []
        self._model_count = 0
        self._load()

    def _read(self, path: Path) -> bytes:
        """Refuse symlinks, traversal, directories and unexpectedly large inputs."""
        relative = path.relative_to(self.upstream)
        cursor = self.upstream
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"Symbolic link is not a catalog source: {relative.as_posix()}")
        if not path.resolve().is_relative_to(self.upstream):
            raise ValueError("Catalog source escapes the pinned checkout")
        if not path.is_file():
            raise ValueError(f"Missing regular source file: {relative.as_posix()}")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise ValueError(f"Catalog source exceeds {_MAX_FILE_BYTES} bytes: {relative.as_posix()}")
        with path.open("rb") as source:
            raw = source.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError("Catalog source grew beyond the size limit")
        return raw

    def _metadata(self, folder: Path) -> tuple[dict[str, str], list[str]]:
        fallback = {"tv": folder.name, "brand": "Unknown", "os": "Unknown", "notes": ""}
        errors: list[str] = []
        try:
            data = json.loads(self._read(folder / "tv.json").decode("utf-8-sig"), object_pairs_hook=_json_object)
            if not isinstance(data, dict):
                raise ValueError("tv.json must contain an object")
            for key in ("tv", "brand", "os", "notes"):
                value = data.get(key)
                if not isinstance(value, str) or (key != "notes" and not value.strip()):
                    errors.append(f"tv.json field {key!r} must be a string" + ("" if key == "notes" else " with a value"))
                elif any(ord(c) < 32 for c in value):
                    errors.append(f"tv.json field {key!r} contains a control character")
                else:
                    fallback[key] = value
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(f"Cannot import tv.json: {error}")
        return fallback, errors

    def _load(self) -> None:
        examples = self.upstream / "examples"
        if not examples.is_dir() or examples.is_symlink():
            self._warnings.append("Pinned checkout has no regular examples directory.")
            return
        for folder in sorted(examples.iterdir(), key=lambda p: p.name.casefold()):
            if folder.is_symlink():
                self._warnings.append(f"Ignored symbolic link under examples: {folder.name}")
                continue
            if not folder.is_dir():
                continue
            files = sorted(folder.glob("debloat*.txt"), key=lambda p: (p.name != "debloat.txt", p.name))
            if not (folder / "tv.json").exists() and not (folder / "tv.json").is_symlink() and not files:
                continue
            self._model_count += 1
            metadata, errors = self._metadata(folder)
            if not files:
                files = [folder / "debloat.txt"]  # Keep malformed/missing lists visible.
            for path in files:
                self._import_profile(folder, path, metadata, errors)

    def _import_profile(self, folder: Path, path: Path, metadata: dict[str, str], metadata_errors: list[str]) -> None:
        profile_id = f"{folder.name}/{path.name}"
        source = path.relative_to(self.upstream).as_posix()
        errors = list(metadata_errors)
        warnings = [_ADVISORY]
        comments: list[dict[str, Any]] = []
        parsed: dict[str, dict[str, str]] = {}
        header: list[str] = []
        leading = True
        pending_comments: list[str] = []
        digest = ""
        try:
            raw = self._read(path)
            digest = sha256(raw).hexdigest()
            text = raw.decode("utf-8-sig")
            if "\x00" in text:
                errors.append("NUL byte in package list")
            # split only normal line endings, so exotic separators cannot conceal entries.
            for number, line in enumerate(text.split("\n"), 1):
                line = line.removesuffix("\r")
                active, marker, comment = line.partition("#")
                package = active.strip(" \t")
                if not package:
                    if marker:
                        comment = comment.strip()
                        comments.append({"line": number, "text": comment})
                        pending_comments.append(comment)
                        if leading:
                            header.append(comment)
                        if _WARNING_RE.search(comment):
                            warnings.append(f"Source line {number}: {comment}")
                    elif line.strip():
                        errors.append(f"Line {number}: unsupported whitespace/control character")
                    continue
                leading = False
                if not PACKAGE_RE.fullmatch(package):
                    errors.append(f"Line {number}: malformed package entry; the complete active line must be a package name")
                    pending_comments.clear()
                    continue
                description = "\n".join(c for c in [*pending_comments, comment.strip() if marker else ""] if c)
                pending_comments.clear()
                if marker:
                    comments.append({"line": number, "text": comment.strip(), "package": package})
                    if _WARNING_RE.search(comment):
                        warnings.append(f"Source line {number}: {comment.strip()}")
                if package in parsed:
                    warnings.append(f"Duplicate package {package} on line {number}; merged into one entry.")
                    previous = parsed[package]["comment"]
                    if description and description != previous:
                        parsed[package]["comment"] = "\n".join(filter(None, [previous, description]))
                else:
                    parsed[package] = {"name": package, "comment": description}
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(f"Cannot import package list: {error}")
        if not parsed:
            errors.append("No active package entries in this list.")
        # No valid-looking subset can be accidentally applied when any entry is malformed.
        usable = not errors
        warnings.extend(errors)
        profile: dict[str, Any] = {
            "id": profile_id,
            **metadata,
            "variant": "default" if path.name == "debloat.txt" else path.stem.removeprefix("debloat-"),
            "list_name": path.name,
            "packages": list(parsed.values()) if usable else [],
            "package_count": len(parsed) if usable else 0,
            "parsed_package_count": len(parsed),
            "source": source,
            "digest": digest,
            "warnings": list(dict.fromkeys(warnings)),
            "errors": errors,
            "usable": usable,
            "comments": comments,
            "header": "\n".join(header),
        }
        self._profiles[profile_id] = profile
        # Only the introductory identity header contributes matches. Later package
        # commentary often references different TVs as comparisons or cautions.
        identity_header = "\n".join(header[:12])
        fields: dict[str, set[str]] = {"device": set(), "product": set()}
        for match in _FIELD_RE.finditer(identity_header):
            value = next((v for v in match.groups()[1:] if v), "").strip().casefold().rstrip(".")
            if value and value not in _STOP_VALUES:
                fields["product" if match.group(1).casefold() == "product" else "device"].add(value)
        self._evidence[profile_id] = {
            "header": identity_header,
            "fields": fields,
            "versions": _android_versions(metadata["os"]),
            "package_names": set(parsed) if usable else set(),
        }

    @staticmethod
    def _public_metadata(profile: dict[str, Any]) -> dict[str, Any]:
        return deepcopy({k: v for k, v in profile.items() if k not in {"packages", "comments", "header"}})

    def summary(self) -> dict[str, Any]:
        usable = sum(p["usable"] for p in self._profiles.values())
        return {
            "models": self._model_count,
            "profiles": len(self._profiles),
            "brands": len({p["brand"] for p in self._profiles.values() if p["brand"] != "Unknown"}),
            "variants": sum(p["list_name"] != "debloat.txt" for p in self._profiles.values()),
            "usable_profiles": usable,
            "unusable_profiles": len(self._profiles) - usable,
            "warnings": list(self._warnings),
        }

    def list_profiles(self, query: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("Profile query must be text")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("Profile limit must be a nonnegative integer")
        needle = query.casefold().strip()
        selected = (p for p in self._profiles.values() if not needle or needle in " ".join(
            str(p[k]) for k in ("id", "tv", "brand", "os", "notes", "variant")
        ).casefold())
        return [self._public_metadata(p) for _, p in zip(range(min(limit, 10000)), selected)]

    def get(self, profile_id: str) -> dict[str, Any]:
        # An ID is a dictionary key, never a user-supplied path to open.
        if not isinstance(profile_id, str) or profile_id not in self._profiles:
            raise KeyError("Unknown profile ID")
        return deepcopy(self._profiles[profile_id])

    def match(self, identity: dict[str, Any], packages: set[str]) -> list[dict[str, Any]]:
        values = {key: value.strip() for key, value in identity.items() if isinstance(value, str)}
        brand_values = [values.get("brand", ""), values.get("manufacturer", "")]
        model = values.get("model", "")
        android = re.match(r"^(\d+)", values.get("android", ""))
        android_major = int(android.group(1)) if android else None
        installed = {p for p in packages if isinstance(p, str) and PACKAGE_RE.fullmatch(p)}
        results: list[dict[str, Any]] = []
        for profile_id, profile in self._profiles.items():
            evidence = self._evidence[profile_id]
            score = 0.0
            identity_evidence = False
            exact_model = False
            reasons: list[str] = []
            warnings = list(profile["warnings"])
            brand_match = any(_phrase_in(profile["brand"], b) for b in brand_values if b)
            if brand_match and profile["brand"] != "Unknown":
                score += 20
                identity_evidence = True
                reasons.append(f"Brand/manufacturer matches {profile['brand']}.")
            elif any(brand_values):
                score -= 12
                reasons.append("Reported brand/manufacturer does not match this example.")
            model_text = f"{profile['tv']}\n{evidence['header']}"
            specific_model = _specific_model(model)
            if _tokens(model) == _tokens(profile["brand"]):
                specific_model = False
            # A model equal to a documented device/product codename may describe a
            # shared board. Keep that as board evidence, not an exact retail model.
            if model.casefold() in (evidence["fields"]["device"] | evidence["fields"]["product"]):
                specific_model = False
            if model and _phrase_in(model, model_text):
                if specific_model:
                    exact_model = True
                    identity_evidence = True
                    score += 60
                    reasons.append(f"Exact whole-token model label: {model}.")
                else:
                    reasons.append(f"Shared/generic model label {model!r}; it cannot identify a unique TV.")
            elif specific_model:
                meaningful = {t for t in _tokens(model) if t not in _GENERIC_MODEL_TOKENS and len(t) > 2}
                overlap = meaningful & set(_tokens(model_text))
                if overlap and (len(overlap) >= 2 or any(any(c.isdigit() for c in t) for t in overlap)):
                    score += min(18, 18 * len(overlap) / max(len(meaningful), 1))
                    identity_evidence = True
                    reasons.append("Partial model tokens match: " + ", ".join(sorted(overlap)) + ".")
            for key, weight in (("device", 12), ("product", 10)):
                value = values.get(key, "").casefold()
                if value and value in evidence["fields"][key]:
                    score += weight
                    identity_evidence = True
                    reasons.append(f"Reported {key} appears in the example header: {values[key]} (shared boards are possible).")
            versions = evidence["versions"]
            if android_major is not None and versions:
                if android_major in versions:
                    score += 8
                    reasons.append(f"Android major version {android_major} is listed; firmware is still unverified.")
                else:
                    score -= 15
                    warning = f"Reported Android {android_major} differs from listed major versions {', '.join(map(str, sorted(versions)))}."
                    warnings.append(warning)
                    reasons.append(warning)
            package_names = evidence["package_names"]
            overlap_count = len(package_names & installed)
            ratio = overlap_count / len(package_names) if package_names else 0.0
            if overlap_count:
                score += 5 * ratio  # At most five points: never a model-detection mechanism.
                reasons.append(f"Installed packages overlap {overlap_count}/{len(package_names)} listed entries; advisory only.")
            if not identity_evidence or score <= 0:
                continue
            result = self._public_metadata(profile)
            result.update({
                "score": round(score, 2),
                "reasons": reasons,
                "exact_model": exact_model,
                "warnings": warnings,
                "package_overlap": {"installed": overlap_count, "profile": len(package_names), "ratio": round(ratio, 4)},
            })
            results.append(result)
        return sorted(results, key=lambda p: (-p["score"], not p["exact_model"], p["id"]))
