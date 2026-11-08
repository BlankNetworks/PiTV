"""Catalog tests do not contact a TV or invoke ADB/upstream executables."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from tvcleaner.catalog import Catalog


class CatalogFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "examples").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def profile(self, slug="acme-a100", tv="Acme A100", brand="Acme", os="Android TV 12", notes="Keep the TV input.", lists=None):
        folder = self.root / "examples" / slug
        folder.mkdir()
        (folder / "tv.json").write_text(json.dumps({"tv": tv, "brand": brand, "os": os, "notes": notes}), encoding="utf-8")
        for name, content in (lists or {"debloat.txt": "com.acme.telemetry # tracker\n"}).items():
            (folder / name).write_text(content, encoding="utf-8")
        return folder

    def test_reads_all_variants_and_preserves_comments_notes_and_digest(self):
        raw = "# Acme A100, device board_a, product acme_prod\n# WARNING: keep updates.\n\ncom.acme.telemetry # metrics only\n# Owner chose this option.\ncom.acme.promo\n# com.acme.casting # deliberately kept\n"
        self.profile(lists={"debloat.txt": raw, "debloat-keep-cast.txt": "com.acme.promo\n"})
        catalog = Catalog(self.root)
        self.assertEqual(catalog.summary()["profiles"], 2)
        self.assertEqual(catalog.summary()["variants"], 1)
        self.assertEqual(catalog.summary()["models"], 1)
        profile = catalog.get("acme-a100/debloat.txt")
        self.assertTrue(profile["usable"])
        self.assertEqual(profile["notes"], "Keep the TV input.")
        self.assertEqual(profile["source"], "examples/acme-a100/debloat.txt")
        self.assertEqual(profile["digest"], sha256(raw.encode()).hexdigest())
        self.assertEqual([p["name"] for p in profile["packages"]], ["com.acme.telemetry", "com.acme.promo"])
        self.assertIn("metrics only", profile["packages"][0]["comment"])
        self.assertEqual(profile["packages"][1]["comment"], "Owner chose this option.")
        self.assertTrue(any("deliberately kept" in c["text"] for c in profile["comments"]))
        self.assertTrue(any("WARNING: keep updates." in w for w in profile["warnings"]))
        variant = catalog.get("acme-a100/debloat-keep-cast.txt")
        self.assertEqual(variant["variant"], "keep-cast")
        self.assertEqual(variant["list_name"], "debloat-keep-cast.txt")
        self.assertEqual(catalog.list_profiles("KEEP-CAST")[0]["id"], variant["id"])

    def test_duplicate_active_entries_merge_once_and_preserve_both_comments(self):
        self.profile(lists={"debloat.txt": "com.acme.telemetry # first\ncom.acme.telemetry # second\n"})
        profile = Catalog(self.root).get("acme-a100/debloat.txt")
        self.assertTrue(profile["usable"])
        self.assertEqual(len(profile["packages"]), 1)
        self.assertEqual(profile["packages"][0]["comment"], "first\nsecond")
        self.assertTrue(any("Duplicate package" in w for w in profile["warnings"]))

    def test_malformed_or_injected_line_disables_the_whole_list(self):
        malicious = [
            "com.acme.ads;reboot", "com.acme.ads && reboot", "com.acme.ads extra",
            "com.acme.$(id)", "com.acme.`id`", "--user 0", "/etc/passwd", "singleword",
            "com.acme.ads\x00", "com.acme.ads\u2028com.other.app", "com.acme.ads\tignored",
            "com.acme.ads\rcom.other.app", "com.acme.ads|reboot", "com.acme.ads > /tmp/out",
        ]
        for index, entry in enumerate(malicious):
            with self.subTest(entry=entry):
                slug = f"bad-{index}"
                self.profile(slug=slug, lists={"debloat.txt": "com.acme.good\n" + entry + "\n"})
                profile = Catalog(self.root).get(f"{slug}/debloat.txt")
                self.assertFalse(profile["usable"])
                self.assertEqual(profile["packages"], [])
                self.assertEqual(profile["package_count"], 0)
                self.assertTrue(profile["errors"])
                self.assertTrue(profile["digest"])

    def test_shell_text_in_comments_is_preserved_only_as_data(self):
        self.profile(lists={"debloat.txt": "# $(touch /tmp/not-run)\ncom.acme.ads # ; reboot\n"})
        profile = Catalog(self.root).get("acme-a100/debloat.txt")
        self.assertTrue(profile["usable"])
        self.assertEqual(profile["packages"][0]["name"], "com.acme.ads")
        self.assertIn("; reboot", profile["packages"][0]["comment"])

    def test_invalid_metadata_keeps_profile_visible_and_unusable(self):
        folder = self.profile()
        (folder / "tv.json").write_text('{"tv": "Acme A100", "brand": 123, "os": "Android 12", "notes": ""}')
        catalog = Catalog(self.root)
        self.assertEqual(len(catalog.list_profiles()), 1)
        self.assertFalse(catalog.get("acme-a100/debloat.txt")["usable"])
        self.assertEqual(catalog.summary()["unusable_profiles"], 1)

    def test_duplicate_json_keys_fail_closed(self):
        folder = self.profile()
        (folder / "tv.json").write_text('{"tv":"A100","tv":"A200","brand":"Acme","os":"Android 12","notes":""}')
        profile = Catalog(self.root).get("acme-a100/debloat.txt")
        self.assertFalse(profile["usable"])
        self.assertTrue(any("Duplicate JSON key" in warning for warning in profile["warnings"]))

    def test_missing_list_remains_visible(self):
        folder = self.profile()
        (folder / "debloat.txt").unlink()
        catalog = Catalog(self.root)
        self.assertEqual(catalog.summary()["profiles"], 1)
        self.assertFalse(catalog.get("acme-a100/debloat.txt")["usable"])

    def test_empty_or_invalid_utf8_lists_fail_closed(self):
        for index, raw in enumerate([b"# nothing active\n", b"com.acme.good\n\xff\n"]):
            folder = self.profile(slug=f"empty-{index}")
            (folder / "debloat.txt").write_bytes(raw)
            profile = Catalog(self.root).get(f"empty-{index}/debloat.txt")
            self.assertFalse(profile["usable"])
            self.assertEqual(profile["packages"], [])

    def test_symlink_sources_never_read_external_content(self):
        folder = self.profile()
        outside = self.root / "external.txt"
        outside.write_text("com.outside.secret\n")
        (folder / "debloat.txt").unlink()
        (folder / "debloat.txt").symlink_to(outside)
        profile = Catalog(self.root).get("acme-a100/debloat.txt")
        self.assertFalse(profile["usable"])
        self.assertEqual(profile["packages"], [])
        self.assertEqual(profile["digest"], "")
        self.assertTrue(any("Symbolic link" in warning for warning in profile["warnings"]))

    def test_symlink_metadata_and_folders_are_not_followed(self):
        folder = self.profile()
        copied = self.root / "metadata.json"
        (folder / "tv.json").rename(copied)
        (folder / "tv.json").symlink_to(copied)
        (self.root / "examples" / "alias").symlink_to(folder, target_is_directory=True)
        catalog = Catalog(self.root)
        self.assertEqual(catalog.summary()["profiles"], 1)
        self.assertFalse(catalog.get("acme-a100/debloat.txt")["usable"])
        self.assertTrue(catalog.summary()["warnings"])

    def test_get_rejects_traversal_and_returns_isolated_data(self):
        self.profile()
        catalog = Catalog(self.root)
        for profile_id in ["../tv.json", "/etc/passwd", "acme-a100/../../secret", "acme-a100\\debloat.txt", None]:
            with self.assertRaises(KeyError):
                catalog.get(profile_id)
        profile = catalog.get("acme-a100/debloat.txt")
        profile["packages"].clear()
        profile["warnings"].append("changed")
        self.assertEqual(catalog.get(profile["id"])["package_count"], 1)
        self.assertEqual(len(catalog.get(profile["id"])["packages"]), 1)
        self.assertNotIn("changed", catalog.get(profile["id"])["warnings"])

    def test_query_limits_and_nonexistent_examples(self):
        self.profile()
        catalog = Catalog(self.root)
        self.assertEqual(catalog.list_profiles(limit=0), [])
        self.assertEqual(len(catalog.list_profiles("aCmE", limit=1)), 1)
        self.assertEqual(catalog.list_profiles("not-existing"), [])
        with self.assertRaises(ValueError):
            catalog.list_profiles(limit=-1)
        with self.assertRaises(ValueError):
            catalog.list_profiles(query=None)
        missing = Catalog(self.root / "missing")
        self.assertEqual(missing.summary()["profiles"], 0)
        self.assertTrue(missing.summary()["warnings"])

    def test_exact_model_uses_token_boundaries(self):
        self.profile(tv="Acme A100 Plus (2023)")
        catalog = Catalog(self.root)
        exact = catalog.match({"brand": "Acme", "model": "A100 Plus", "android": "12"}, set())[0]
        self.assertTrue(exact["exact_model"])
        self.assertTrue(any("whole-token" in r for r in exact["reasons"]))
        partial_sku = catalog.match({"brand": "Acme", "model": "A10", "android": "12"}, set())[0]
        self.assertFalse(partial_sku["exact_model"])
        self.assertLess(partial_sku["score"], exact["score"])

    def test_partial_model_evidence_is_not_exact(self):
        self.profile(tv="Acme A100 Plus")
        result = Catalog(self.root).match({"brand": "Acme", "model": "A100 Ultra"}, set())[0]
        self.assertFalse(result["exact_model"])
        self.assertTrue(any("Partial model" in r for r in result["reasons"]))

    def test_generic_smart_tv_string_is_ambiguous(self):
        self.profile(slug="acme-a100", tv="Acme A100 (Smart TV Pro, G08)", lists={"debloat.txt": '# Acme A100, model "Smart TV Pro", device G08, product G08_4K_GB\ncom.acme.ads\n'})
        self.profile(slug="acme-b200", tv="Acme B200 (Smart TV Pro, G08)", lists={"debloat.txt": '# Acme B200, model "Smart TV Pro", device G08, product G08_4K_GB\ncom.acme.ads\n'})
        results = Catalog(self.root).match({"brand": "Acme", "model": "Smart TV Pro", "device": "G08", "product": "G08_4K_GB", "android": "12"}, {"com.acme.ads"})
        self.assertEqual(len(results), 2)
        self.assertTrue(all(not r["exact_model"] for r in results))
        self.assertTrue(all(any("Shared/generic" in reason for reason in r["reasons"]) for r in results))
        self.assertTrue(all(any("shared boards" in reason for reason in r["reasons"]) for r in results))
        self.assertEqual(results[0]["score"], results[1]["score"])

    def test_header_device_key_value_syntax(self):
        self.profile(lists={"debloat.txt": "# Acme A100, device:board_a, product=acme_prod\ncom.acme.ads\n"})
        results = Catalog(self.root).match({"device": "board_a", "product": "acme_prod"}, set())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["exact_model"])
        self.assertEqual(results[0]["score"], 22)
        self.assertTrue(any("Reported device" in r for r in results[0]["reasons"]))
        self.assertTrue(any("Reported product" in r for r in results[0]["reasons"]))

    def test_unusable_profile_can_be_seen_but_supplies_no_partial_plan(self):
        self.profile(lists={"debloat.txt": "com.acme.good\ncom.acme.bad extra\n"})
        catalog = Catalog(self.root)
        matches = catalog.match({"brand": "Acme", "model": "A100"}, {"com.acme.good"})
        self.assertFalse(matches[0]["usable"])
        self.assertEqual(matches[0]["package_count"], 0)
        self.assertEqual(matches[0]["package_overlap"]["installed"], 0)
        self.assertEqual(catalog.get(matches[0]["id"])["packages"], [])

    def test_oversize_source_remains_visible_but_unusable(self):
        folder = self.profile()
        (folder / "debloat.txt").write_bytes(b"#" * (2 * 1024 * 1024 + 1))
        profile = Catalog(self.root).get("acme-a100/debloat.txt")
        self.assertFalse(profile["usable"])
        self.assertTrue(any("exceeds" in warning for warning in profile["warnings"]))

    def test_brand_year_and_shared_board_are_not_exact_models(self):
        self.profile(tv="Acme A100 (2023, G08)", lists={"debloat.txt": "# Acme A100, device G08\ncom.acme.ads\n"})
        catalog = Catalog(self.root)
        for model in ("Acme", "2023", "G08"):
            with self.subTest(model=model):
                matches = catalog.match({"brand": "Acme", "model": model, "device": "G08"}, set())
                self.assertFalse(matches[0]["exact_model"])

    def test_smarttv_4k_ffm_is_generic(self):
        self.profile(tv="Acme A100 (SmartTV 4K FFM)")
        result = Catalog(self.root).match({"brand": "Acme", "model": "SmartTV 4K FFM"}, set())[0]
        self.assertFalse(result["exact_model"])

    def test_package_overlap_and_android_version_cannot_identify_a_tv(self):
        self.profile(lists={"debloat.txt": "com.google.ads\ncom.google.metrics\n"})
        catalog = Catalog(self.root)
        self.assertEqual(catalog.match({"model": "Smart TV", "android": "12"}, {"com.google.ads", "com.google.metrics"}), [])
        without = catalog.match({"brand": "Acme"}, set())[0]
        with_packages = catalog.match({"brand": "Acme"}, {"com.google.ads", "com.google.metrics"})[0]
        self.assertEqual(with_packages["score"] - without["score"], 5)
        self.assertFalse(with_packages["exact_model"])

    def test_os_mismatch_warning_preserved_and_alternatives_parse(self):
        self.profile(os="Android TV 10 and 11")
        catalog = Catalog(self.root)
        matching = catalog.match({"brand": "Acme", "model": "A100", "android": "11.0"}, set())[0]
        mismatching = catalog.match({"brand": "Acme", "model": "A100", "android": "14"}, set())[0]
        self.assertTrue(any("11 is listed" in r for r in matching["reasons"]))
        self.assertTrue(any("differs from listed" in r for r in mismatching["warnings"]))
        self.assertGreater(matching["score"], mismatching["score"])

    def test_notes_and_later_package_comparisons_do_not_assert_exact_model(self):
        self.profile(notes="Do not use this on Acme B200.", lists={"debloat.txt": "# Acme A100\ncom.acme.ads\n# B200 uses a different package set.\ncom.acme.metrics\n"})
        result = Catalog(self.root).match({"brand": "Acme", "model": "B200"}, set())[0]
        self.assertFalse(result["exact_model"])
        self.assertIn("Do not use", result["notes"])
        self.assertNotIn("packages", result)


class PinnedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = Path(__file__).resolve().parents[1] / "vendor" / "cleanyourtv"
        cls.catalog = Catalog(cls.upstream)

    def test_complete_pinned_inventory(self):
        summary = self.catalog.summary()
        self.assertEqual(summary["models"], 442)
        self.assertEqual(summary["profiles"], 450)
        self.assertEqual(summary["brands"], 117)
        self.assertEqual(summary["variants"], 8)
        self.assertEqual(summary["usable_profiles"], 450)
        self.assertEqual(summary["unusable_profiles"], 0)
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(len(self.catalog.list_profiles()), 450)

    def test_each_list_has_exact_source_digest_and_unique_packages(self):
        for metadata in self.catalog.list_profiles():
            profile = self.catalog.get(metadata["id"])
            with self.subTest(profile=profile["id"]):
                self.assertEqual(profile["digest"], sha256((self.upstream / profile["source"]).read_bytes()).hexdigest())
                self.assertEqual(len({p["name"] for p in profile["packages"]}), len(profile["packages"]))
                self.assertTrue(profile["warnings"])

    def test_real_xiaomi_model_match(self):
        matches = self.catalog.match({"brand": "Xiaomi", "manufacturer": "Xiaomi", "model": "MiTV-MOOQ1", "device": "tarzan", "android": "10"}, {"com.miui.tv.analytics", "com.xiaomi.statistic"})
        expected = next(p for p in matches if p["id"] == "xiaomi-mi-tv-4a/debloat.txt")
        self.assertTrue(expected["exact_model"])
        self.assertEqual(matches[0]["id"], expected["id"])
        self.assertIn("Android TV 11 subset", expected["notes"])

    def test_real_shared_tcl_model_never_exact(self):
        matches = self.catalog.match({"brand": "TCL", "model": "Smart TV Pro", "device": "G08", "product": "G08_4K_GB", "android": "14"}, {"com.tcl.recommend", "com.tcl.tv"})
        self.assertGreater(len(matches), 1)
        self.assertTrue(all(not p["exact_model"] for p in matches))
        c745 = next(p for p in matches if p["id"] == "tcl-55c745/debloat.txt")
        self.assertTrue(any("Reported device" in r for r in c745["reasons"]))
        self.assertIn("different household", c745["notes"])

    def test_real_variants_are_separate_not_combined(self):
        main = self.catalog.get("google-chromecast-gtv-4k/debloat.txt")
        keep = self.catalog.get("google-chromecast-gtv-4k/debloat-keep-cast.txt")
        self.assertEqual(keep["variant"], "keep-cast")
        self.assertNotEqual(main["digest"], keep["digest"])
        self.assertGreater(len(main["packages"]), len(keep["packages"]))
        self.assertIn("com.google.android.tungsten.setupwraith", {p["name"] for p in main["packages"]})
        self.assertNotIn("com.google.android.tungsten.setupwraith", {p["name"] for p in keep["packages"]})


if __name__ == "__main__":
    unittest.main()
