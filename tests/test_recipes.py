"""Exact-match recipes must never widen a reviewed cleanup onto a different TV."""
from copy import deepcopy
import hashlib
import json
import unittest

from tvcleaner.recipes import (
    CHECK_FIELDS, SELECTOR_FIELDS, RecipeError, build_recipe, public_recipe,
    recipe_matches, validate_recipe,
)


UPSTREAM = "1234567890abcdef1234567890abcdef12345678"
ANALYTICS = "com.vendor.analytics"
FEEDBACK = "com.vendor.feedback"
PREEXISTING = "com.vendor.preexisting"
HOME = "com.example.home"
IME = "com.example.ime"


def refresh_digest(value):
    """Simulate an editor who can rehash data, not only an accidental byte edit."""
    payload = {key: item for key, item in value.items() if key not in ("enabled", "digest")}
    value["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return value


class RecipeFixture(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "id": "example-tv-42/debloat.txt", "digest": "a" * 64, "usable": True,
            "packages": [{"name": name, "comment": "Owner reviewed example"} for name in
                         (ANALYTICS, FEEDBACK, PREEXISTING, "com.android.settings", "com.netflix.ninja")],
        }
        self.snapshot = {
            "identity": {
                "manufacturer": "Example", "brand": "Example", "model": "TV-42",
                "device": "example42", "product": "example42_global", "android": "12", "sdk": "31",
                "build": "STT1.220101.001.42",
                "fingerprint": "Example/example42_global/example42:12/STT1.220101.001.42/42:user/release-keys",
                "family": "android_tv", "serial": "PRIVATE-SERIAL-NOT-IN-RECIPE",
                "android_id": "aabbccddeeff1122",
            },
            "packages": sorted(["android", "com.android.settings", HOME, IME, ANALYTICS, FEEDBACK,
                                PREEXISTING, "com.user.first"]),
            "disabled": [PREEXISTING],
            "third_party": ["com.user.first"],
            "protected": sorted([HOME, IME]),
            "home": HOME + "/.MainActivity",
            "raw": {"sensitive": "not copied"},
        }
        self.checks = {key: True for key in CHECK_FIELDS}

    def build(self, **overrides):
        arguments = {"profile": self.profile, "snapshot": self.snapshot,
                     "packages": [ANALYTICS, FEEDBACK], "upstream_commit": UPSTREAM,
                     "checks": self.checks, "notes": "Owner checked the household's required functions."}
        arguments.update(overrides)
        return build_recipe(**arguments)

    def matches(self, recipe, **overrides):
        arguments = {"recipe": recipe, "snapshot": self.snapshot, "profile": self.profile,
                     "upstream_commit": UPSTREAM}
        arguments.update(overrides)
        return recipe_matches(**arguments)


class RecipeCreationTests(RecipeFixture):
    def test_builds_explicit_owner_attestation_and_matches_original(self):
        recipe = self.build()
        self.assertEqual(recipe["schema_version"], 1)
        self.assertRegex(recipe["id"], r"^[a-f0-9]{32}$")
        self.assertIs(type(recipe["created_at"]), int)
        self.assertTrue(recipe["enabled"])
        self.assertEqual(recipe["attestation"]["kind"], "owner_attested")
        self.assertEqual(recipe["attestation"]["checks"], self.checks)
        self.assertEqual(recipe["system_packages"], sorted(set(self.snapshot["packages"]) - {"com.user.first"}))
        self.assertEqual(recipe["baseline_disabled"], [PREEXISTING])
        self.assertEqual(validate_recipe(recipe), recipe)
        self.assertTrue(self.matches(recipe))

    def test_does_not_copy_device_identifiers_or_raw_state(self):
        recipe = self.build()
        encoded = json.dumps(recipe)
        self.assertNotIn("PRIVATE-SERIAL", encoded)
        self.assertNotIn("aabbccddeeff1122", encoded)
        self.assertNotIn("sensitive", encoded)
        self.assertEqual(set(recipe["selector"]), set(SELECTOR_FIELDS))

    def test_input_order_is_canonicalized_without_mutating_evidence(self):
        self.snapshot["packages"].reverse()
        self.snapshot["protected"].reverse()
        original_snapshot, original_profile = deepcopy(self.snapshot), deepcopy(self.profile)
        recipe = self.build(packages=[FEEDBACK, ANALYTICS])
        self.assertEqual(recipe["packages"], [ANALYTICS, FEEDBACK])
        self.assertEqual(recipe["system_packages"], sorted(recipe["system_packages"]))
        self.assertEqual(self.snapshot, original_snapshot)
        self.assertEqual(self.profile, original_profile)

    def test_every_owner_check_must_be_literal_true(self):
        for key in CHECK_FIELDS:
            for invalid in (False, 1, "true", None):
                with self.subTest(key=key, value=invalid):
                    checks = {**self.checks, key: invalid}
                    with self.assertRaises(RecipeError):
                        self.build(checks=checks)

    def test_missing_extra_or_nonobject_checks_are_rejected(self):
        missing = dict(self.checks)
        missing.pop("restore")
        for checks in (missing, {**self.checks, "independently_certified": True}, [], None):
            with self.subTest(checks=checks), self.assertRaises(RecipeError):
                self.build(checks=checks)

    def test_empty_unknown_pattern_and_untyped_identity_are_rejected(self):
        for field in SELECTOR_FIELDS:
            for bad in ("", " ", "unknown", "null", "*", "TV-*", "[a-z]+", "^TV42$", 42, None):
                with self.subTest(field=field, value=bad):
                    snapshot = deepcopy(self.snapshot)
                    snapshot["identity"][field] = bad
                    with self.assertRaises(RecipeError):
                        self.build(snapshot=snapshot)

    def test_missing_identity_and_snapshot_evidence_are_rejected(self):
        for field in SELECTOR_FIELDS:
            snapshot = deepcopy(self.snapshot)
            del snapshot["identity"][field]
            with self.subTest(field=field), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)
        for field in ("identity", "packages", "disabled", "third_party", "home", "protected"):
            snapshot = deepcopy(self.snapshot)
            del snapshot[field]
            with self.subTest(field=field), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)

    def test_only_supported_families_and_explicit_versions_are_accepted(self):
        for field, value in (("family", "tizen"), ("family", "phone"), ("android", "12+"),
                             ("sdk", "31-34"), ("sdk", "0")):
            snapshot = deepcopy(self.snapshot)
            snapshot["identity"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)
        snapshot = deepcopy(self.snapshot)
        snapshot["identity"]["family"] = "fire_os"
        self.assertEqual(self.build(snapshot=snapshot)["selector"]["family"], "fire_os")

    def test_package_lists_must_be_bare_unique_names(self):
        for bad in ("com.example.*", "com.example.app;reboot", "com.example.app --user 0",
                    " com.example.app", "com.example.app\n", "com..app", "android", "", None):
            with self.subTest(package=bad), self.assertRaises(RecipeError):
                self.build(packages=[bad])
        for selected in ([], [ANALYTICS, ANALYTICS], ANALYTICS, {ANALYTICS}):
            with self.subTest(selected=selected), self.assertRaises(RecipeError):
                self.build(packages=selected)

    def test_package_must_belong_to_the_pinned_profile(self):
        self.snapshot["packages"].append("com.vendor.other")
        with self.assertRaises(RecipeError):
            self.build(packages=["com.vendor.other"])

    def test_missing_protected_or_third_party_selections_are_rejected(self):
        for kind in ("absent", "protected", "third_party"):
            snapshot = deepcopy(self.snapshot)
            if kind == "absent":
                snapshot["packages"].remove(ANALYTICS)
            else:
                snapshot[kind].append(ANALYTICS)
            with self.subTest(kind=kind), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)

    def test_generic_core_and_known_streaming_apps_cannot_enter_recipe(self):
        for package in ("com.android.settings", "com.netflix.ninja", "com.vendor.firmwareupdate",
                        "com.android.providers.settings"):
            snapshot, profile = deepcopy(self.snapshot), deepcopy(self.profile)
            if package not in snapshot["packages"]:
                snapshot["packages"].append(package)
            if package not in [entry["name"] for entry in profile["packages"]]:
                profile["packages"].append({"name": package})
            with self.subTest(package=package), self.assertRaises(RecipeError):
                self.build(profile=profile, snapshot=snapshot, packages=[package])

    def test_inconsistent_snapshot_inventories_are_rejected(self):
        for field in ("disabled", "third_party", "protected"):
            snapshot = deepcopy(self.snapshot)
            snapshot[field].append("com.not.installed")
            with self.subTest(field=field), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)
        snapshot = deepcopy(self.snapshot)
        snapshot["third_party"].append("android")
        with self.assertRaises(RecipeError):
            self.build(snapshot=snapshot)

    def test_snapshot_package_types_and_duplicates_are_rejected(self):
        for field in ("packages", "disabled", "third_party", "protected"):
            for bad in ("com.vendor.app", None, ["com.vendor.app", "com.vendor.app"], [123]):
                snapshot = deepcopy(self.snapshot)
                snapshot[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(RecipeError):
                    self.build(snapshot=snapshot)

    def test_home_must_be_an_enabled_protected_component(self):
        for value in ("", HOME, HOME + "/.ResolverActivity", HOME + "/.Main;reboot", HOME + "//.Main"):
            snapshot = deepcopy(self.snapshot)
            snapshot["home"] = value
            with self.subTest(home=value), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)
        for kind in ("disabled", "unprotected", "uninstalled"):
            snapshot = deepcopy(self.snapshot)
            if kind == "disabled":
                snapshot["disabled"].append(HOME)
            elif kind == "unprotected":
                snapshot["protected"].remove(HOME)
            else:
                snapshot["packages"].remove(HOME)
                snapshot["protected"].remove(HOME)
            with self.subTest(kind=kind), self.assertRaises(RecipeError):
                self.build(snapshot=snapshot)

    def test_custom_user_installed_home_can_be_preserved(self):
        self.snapshot["third_party"].append(HOME)
        recipe = self.build()
        self.assertNotIn(HOME, recipe["system_packages"])
        self.assertIn(HOME, recipe["protected"])
        self.assertTrue(self.matches(recipe))

    def test_disabled_user_apps_are_retained_in_baseline_without_all_user_inventory(self):
        self.snapshot["disabled"].append("com.user.first")
        recipe = self.build()
        self.assertIn("com.user.first", recipe["baseline_disabled"])
        self.assertNotIn("com.user.first", recipe["system_packages"])
        self.assertTrue(self.matches(recipe))

    def test_invalid_profile_and_manifest_are_rejected(self):
        for key, invalid in (("usable", False), ("usable", 1), ("digest", "a" * 63),
                             ("id", "../debloat.txt"), ("id", "example/../../debloat.txt"),
                             ("id", "example/recipe.sh"), ("packages", [])):
            profile = deepcopy(self.profile)
            profile[key] = invalid
            with self.subTest(key=key), self.assertRaises(RecipeError):
                self.build(profile=profile)
        profile = deepcopy(self.profile)
        profile["packages"].append({"name": ANALYTICS})
        with self.assertRaises(RecipeError):
            self.build(profile=profile)
        for commit in ("main", "123abc", "A" * 40, None, 1):
            with self.subTest(commit=commit), self.assertRaises(RecipeError):
                self.build(upstream_commit=commit)

    def test_notes_and_demo_flags_are_typed_and_bounded(self):
        for notes in (None, {"shell": "reboot"}, "bad\x00note", "x" * 4097):
            with self.subTest(notes_type=type(notes)), self.assertRaises(RecipeError):
                self.build(notes=notes)
        for demo in (1, "true", None):
            with self.subTest(demo=demo), self.assertRaises(RecipeError):
                self.build(demo=demo)
        self.assertEqual(self.build(notes="first\nsecond\tcheck")["attestation"]["notes"], "first\nsecond\tcheck")


class RecipeMatchingTests(RecipeFixture):
    def test_different_device_ids_can_match_the_same_model_and_firmware(self):
        recipe = self.build()
        other = deepcopy(self.snapshot)
        other["identity"]["serial"] = "ANOTHER-DEVICE"
        other["identity"]["android_id"] = "1122334455667788"
        self.assertTrue(self.matches(recipe, snapshot=other))
        del other["identity"]["serial"]
        del other["identity"]["android_id"]
        self.assertTrue(self.matches(recipe, snapshot=other))

    def test_every_selector_field_must_match_exactly(self):
        recipe = self.build()
        for key in SELECTOR_FIELDS:
            other = deepcopy(self.snapshot)
            if key == "family":
                replacement = "fire_os"
            elif key in ("android", "sdk"):
                replacement = str(int(other["identity"][key]) + 1)
            else:
                replacement = other["identity"][key] + "different"
            other["identity"][key] = replacement
            with self.subTest(field=key):
                self.assertFalse(self.matches(recipe, snapshot=other))
        other = deepcopy(self.snapshot)
        other["identity"]["brand"] = "example"
        self.assertFalse(self.matches(recipe, snapshot=other))

    def test_unrelated_enabled_user_app_installations_can_differ(self):
        recipe = self.build()
        other = deepcopy(self.snapshot)
        other["packages"].remove("com.user.first")
        other["packages"].extend(["com.user.second", "com.user.third"])
        other["third_party"] = ["com.user.second", "com.user.third"]
        self.assertTrue(self.matches(recipe, snapshot=other))

    def test_added_removed_or_reclassified_system_packages_block_matching(self):
        recipe = self.build()
        for kind in ("added", "removed", "selected_becomes_user_app", "other_becomes_user_app"):
            other = deepcopy(self.snapshot)
            if kind == "added":
                other["packages"].append("com.vendor.newsystem")
            elif kind == "removed":
                other["packages"].remove(ANALYTICS)
            elif kind == "selected_becomes_user_app":
                other["third_party"].append(ANALYTICS)
            else:
                other["third_party"].append("com.android.settings")
            with self.subTest(kind=kind):
                self.assertFalse(self.matches(recipe, snapshot=other))

    def test_partly_or_completely_disabled_selection_is_idempotently_eligible(self):
        recipe = self.build()
        for extra in ([ANALYTICS], [FEEDBACK], [ANALYTICS, FEEDBACK]):
            other = deepcopy(self.snapshot)
            other["disabled"].extend(extra)
            with self.subTest(disabled=extra):
                self.assertTrue(self.matches(recipe, snapshot=other))
        self.snapshot["disabled"].extend([ANALYTICS, FEEDBACK])
        self.assertEqual(self.build()["baseline_disabled"], [PREEXISTING])

    def test_any_other_disabled_state_change_blocks_matching(self):
        recipe = self.build()
        for disabled in ([], [PREEXISTING, "com.android.settings"], [PREEXISTING, "com.user.first"]):
            other = deepcopy(self.snapshot)
            other["disabled"] = disabled
            with self.subTest(disabled=disabled):
                self.assertFalse(self.matches(recipe, snapshot=other))

    def test_home_activity_and_protected_set_must_match(self):
        recipe = self.build()
        other = deepcopy(self.snapshot)
        other["home"] = HOME + "/.OtherActivity"
        self.assertFalse(self.matches(recipe, snapshot=other))
        for protected in ([HOME], [HOME, IME, ANALYTICS], [HOME, IME, "com.android.settings"]):
            other = deepcopy(self.snapshot)
            other["protected"] = protected
            with self.subTest(protected=protected):
                self.assertFalse(self.matches(recipe, snapshot=other))

    def test_inventory_order_does_not_affect_matching(self):
        recipe = self.build()
        other = deepcopy(self.snapshot)
        for key in ("packages", "disabled", "protected", "third_party"):
            other[key].reverse()
        self.assertTrue(self.matches(recipe, snapshot=other))

    def test_profile_and_upstream_changes_block_matching(self):
        recipe = self.build()
        for key, new in (("id", "different-tv/debloat.txt"), ("digest", "b" * 64), ("usable", False)):
            profile = deepcopy(self.profile)
            profile[key] = new
            with self.subTest(key=key):
                self.assertFalse(self.matches(recipe, profile=profile))
        profile = deepcopy(self.profile)
        profile["packages"] = [entry for entry in profile["packages"] if entry["name"] != ANALYTICS]
        self.assertFalse(self.matches(recipe, profile=profile))
        self.assertFalse(self.matches(recipe, upstream_commit="b" * 40))
        self.assertFalse(self.matches(recipe, upstream_commit="main"))

    def test_mode_isolation_and_disable_switch(self):
        normal = self.build()
        demo = self.build(demo=True)
        self.assertTrue(self.matches(demo, demo=True))
        self.assertFalse(self.matches(demo))
        self.assertFalse(self.matches(normal, demo=True))
        self.assertFalse(self.matches(normal, demo=1))
        original_digest = normal["digest"]
        normal["enabled"] = False
        self.assertEqual(validate_recipe(normal)["digest"], original_digest)
        self.assertFalse(self.matches(normal))

    def test_missing_and_malformed_inputs_fail_closed_without_throwing(self):
        recipe = self.build()
        for snapshot in (None, [], {}, {"identity": None}, {**self.snapshot, "packages": "all"}):
            with self.subTest(snapshot=snapshot):
                self.assertFalse(self.matches(recipe, snapshot=snapshot))
        for field in ("identity", "packages", "disabled", "third_party", "home", "protected"):
            snapshot = deepcopy(self.snapshot)
            del snapshot[field]
            with self.subTest(field=field):
                self.assertFalse(self.matches(recipe, snapshot=snapshot))
        for recipe_value in (None, [], {}, {"schema_version": 1}):
            self.assertFalse(self.matches(recipe_value))
        self.assertFalse(self.matches(recipe, profile=None))


class StoredRecipeValidationTests(RecipeFixture):
    def test_digest_detects_content_edits(self):
        recipe = self.build()
        recipe["attestation"]["notes"] = "Changed after approval"
        with self.assertRaises(RecipeError):
            validate_recipe(recipe)
        self.assertFalse(self.matches(recipe))

    def test_unknown_executable_and_device_identifier_fields_are_rejected(self):
        for field in ("shell", "command", "download_url", "executable", "serial", "android_id"):
            recipe = self.build()
            recipe[field] = "untrusted"
            refresh_digest(recipe)
            with self.subTest(field=field), self.assertRaises(RecipeError):
                validate_recipe(recipe)
        recipe = self.build()
        recipe["selector"]["serial"] = "private-device"
        refresh_digest(recipe)
        with self.assertRaises(RecipeError):
            validate_recipe(recipe)

    def test_schema_ids_timestamps_and_flags_are_strictly_typed(self):
        for field, bad in (("schema_version", 2), ("schema_version", True), ("id", "not-an-id"),
                           ("id", "A" * 32), ("created_at", 1.5), ("created_at", True),
                           ("created_at", 0), ("enabled", 1), ("demo", "false")):
            recipe = self.build()
            recipe[field] = bad
            refresh_digest(recipe)
            with self.subTest(field=field, value=bad), self.assertRaises(RecipeError):
                validate_recipe(recipe)

    def test_missing_fields_and_unknown_attestation_are_rejected(self):
        for field in self.build():
            recipe = self.build()
            del recipe[field]
            with self.subTest(field=field), self.assertRaises(RecipeError):
                validate_recipe(recipe)
        for kind in ("certified", "manufacturer_verified", "owner_attested ", None):
            recipe = self.build()
            recipe["attestation"]["kind"] = kind
            refresh_digest(recipe)
            with self.subTest(kind=kind), self.assertRaises(RecipeError):
                validate_recipe(recipe)

    def test_rehash_does_not_allow_inconsistent_or_unprotected_selection(self):
        changes = (
            ("packages", []),
            ("packages", [ANALYTICS, ANALYTICS]),
            ("packages", ["com.not.installed"]),
            ("packages", ["com.android.settings"]),
            ("packages", [HOME]),
            ("baseline_disabled", sorted([ANALYTICS, PREEXISTING])),
            ("baseline_disabled", sorted([HOME, PREEXISTING])),
            ("protected", [IME]),
            ("system_packages", []),
        )
        for field, value in changes:
            recipe = self.build()
            recipe[field] = value
            refresh_digest(recipe)
            with self.subTest(field=field, value=value), self.assertRaises(RecipeError):
                validate_recipe(recipe)

    def test_rehashed_wildcard_and_nonboolean_checks_still_fail(self):
        recipe = self.build()
        recipe["selector"]["fingerprint"] = "Example/*"
        refresh_digest(recipe)
        with self.assertRaises(RecipeError):
            validate_recipe(recipe)
        recipe = self.build()
        recipe["attestation"]["checks"]["restore"] = 1
        refresh_digest(recipe)
        with self.assertRaises(RecipeError):
            validate_recipe(recipe)

    def test_recipe_arrays_must_be_canonical_and_unique(self):
        for field in ("packages", "system_packages", "protected", "baseline_disabled"):
            recipe = self.build()
            recipe[field].append(recipe[field][0])
            refresh_digest(recipe)
            with self.subTest(field=field), self.assertRaises(RecipeError):
                validate_recipe(recipe)
        recipe = self.build()
        recipe["packages"].reverse()
        refresh_digest(recipe)
        with self.assertRaises(RecipeError):
            validate_recipe(recipe)

    def test_validated_and_public_copies_cannot_mutate_private_recipe(self):
        recipe = self.build()
        clone = validate_recipe(recipe)
        clone["attestation"]["notes"] = "Changed copy"
        self.assertNotEqual(clone["attestation"]["notes"], recipe["attestation"]["notes"])
        summary = public_recipe(recipe)
        for field in ("system_packages", "protected", "baseline_disabled", "raw", "serial", "android_id"):
            self.assertNotIn(field, summary)
        self.assertEqual(summary["package_count"], 2)
        self.assertEqual(summary["system_package_count"], len(recipe["system_packages"]))
        summary["selector"]["model"] = "Changed public copy"
        self.assertEqual(recipe["selector"]["model"], "TV-42")


if __name__ == "__main__":
    unittest.main()
