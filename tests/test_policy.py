"""Behavioral checks for conservative package choices, with real catalog aliases."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from tvcleaner.catalog import Catalog
from tvcleaner.policy import package_rows


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = Path(__file__).resolve().parents[1] / "vendor" / "cleanyourtv"
        spec = importlib.util.spec_from_file_location("policy_test_recovery", cls.upstream / "tools/recovery.py")
        cls.recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.recovery)
        cls.catalog = Catalog(cls.upstream)

    @staticmethod
    def snapshot(installed, *, disabled=(), protected=(), third_party=(), home="com.owner.start/.Home"):
        return {
            "packages": list(installed),
            "disabled": list(disabled),
            "protected": list(protected),
            "third_party": list(third_party),
            "home": home,
        }

    def rows(self, names, **snapshot_options):
        profile = {"packages": [{"name": name, "comment": f"Notes for {name}"} for name in names]}
        snapshot = self.snapshot(names, **snapshot_options)
        return package_rows(profile, snapshot, self.recovery.core_packages)

    def assert_preserved(self, names, **snapshot_options):
        for row in self.rows(names, **snapshot_options):
            with self.subTest(package=row["name"]):
                self.assertTrue(row["protected"])
                self.assertEqual(row["category"], "preserved")
                self.assertFalse(row["selected"])
                self.assertTrue(row["installed"])

    def test_known_privacy_defaults_are_review_choices_only(self):
        names = [
            "com.miui.tv.analytics", "com.xiaomi.statistic", "com.google.android.feedback",
            "tv.samba.ssm", "tv.samba.ssm.ui", "tv.inscape.tvclient.inscapeacr",
        ]
        for row in self.rows(names):
            with self.subTest(package=row["name"]):
                self.assertTrue(row["selected"])
                self.assertFalse(row["protected"])
                self.assertEqual(row["category"], "privacy")
                self.assertEqual(row["comment"], f"Notes for {row['name']}")
                self.assertTrue(row["reason"])

    def test_unknown_analytics_and_promos_are_not_selected_by_name(self):
        names = ["com.vendor.analytics", "com.vendor.adservice", "com.vendor.promo"]
        for row in self.rows(names):
            self.assertFalse(row["selected"])
            self.assertFalse(row["protected"])
            self.assertEqual(row["category"], "optional")
            self.assertIn("review", row["reason"].lower())

    def test_packages_outside_the_selected_profile_are_never_added(self):
        profile = {"packages": [{"name": "com.vendor.promo", "comment": "Optional"}]}
        snapshot = self.snapshot(["com.vendor.promo", "com.miui.tv.analytics", "com.xiaomi.statistic"])
        rows = package_rows(profile, snapshot, self.recovery.core_packages)
        self.assertEqual([r["name"] for r in rows], ["com.vendor.promo"])
        self.assertFalse(rows[0]["selected"])

    def test_absent_and_previously_disabled_privacy_entries_stay_unselected(self):
        names = ["com.miui.tv.analytics", "com.xiaomi.statistic"]
        profile = {"packages": [{"name": name, "comment": "Telemetry"} for name in names]}
        snapshot = self.snapshot(["com.xiaomi.statistic"], disabled=["com.xiaomi.statistic"])
        rows = {r["name"]: r for r in package_rows(profile, snapshot, self.recovery.core_packages)}
        absent = rows["com.miui.tv.analytics"]
        self.assertFalse(absent["installed"])
        self.assertFalse(absent["selected"])
        self.assertIn("Not installed", absent["reason"])
        disabled = rows["com.xiaomi.statistic"]
        self.assertTrue(disabled["disabled"])
        self.assertFalse(disabled["selected"])
        self.assertIn("Already disabled", disabled["reason"])

    def test_dynamic_protection_overrides_privacy_default(self):
        self.assert_preserved(["com.miui.tv.analytics"], protected=["com.miui.tv.analytics"])

    def test_user_installed_apps_override_all_categories(self):
        names = ["com.vendor.application", "com.miui.tv.analytics"]
        self.assert_preserved(names, third_party=names)

    def test_current_home_is_kept_without_a_launcher_substring(self):
        self.assert_preserved(["com.owner.start"], home="com.owner.start/.CustomEntry")

    def test_live_tv_input_and_keyboard_services_use_dynamic_protection(self):
        names = ["com.vendor.hdmiservice", "com.vendor.tuner", "com.vendor.textentry"]
        self.assert_preserved(names, protected=names)

    def test_upstream_core_components_remain_preserved(self):
        self.assert_preserved([
            "android.ext.services", "android.ext.shared", "com.android.sdksandbox",
            "com.android.location.fused", "com.android.systemui", "com.android.shell",
            "com.android.phone", "com.android.settings", "com.android.tv.settings",
            "com.android.permissioncontroller", "com.google.android.permissioncontroller",
            "com.android.packageinstaller", "com.google.android.packageinstaller",
            "com.android.modulemetadata", "com.google.android.overlay.modules.permissioncontroller",
        ])

    def test_all_content_providers_are_kept_even_upstream_optional_exceptions(self):
        # These providers are upstream's explicit CORE_OK exceptions. The companion
        # policy still promises to leave providers alone by default.
        optional_upstream = [
            "com.android.providers.calendar", "com.android.providers.contacts",
            "com.android.providers.userdictionary", "com.android.providers.blockednumber",
        ]
        self.assertEqual(self.recovery.core_packages(optional_upstream), [])
        self.assert_preserved(optional_upstream + ["com.android.providers.media", "com.android.providers.settings"])

    def test_play_services_and_vendor_tv_middleware_are_kept(self):
        self.assert_preserved([
            "com.android.vending", "com.google.android.gms", "com.google.android.gsf",
            "com.cltv.mal", "com.mediatek.tis", "com.mediatek.tv.service", "com.mediatek.tv.agent",
            "com.tcl.tv", "com.tcl.suspension", "com.tcl.systemserver", "com.tpa.kb",
        ])

    def test_setup_network_remote_voice_casting_and_updates_are_kept(self):
        self.assert_preserved([
            "com.google.android.tungsten.setupwraith", "com.vendor.setupwizard", "com.vendor.provision",
            "com.android.bluetooth", "com.vendor.remote", "com.vendor.autopair",
            "com.android.networkstack", "com.vendor.wifi.service", "com.vendor.keyboard",
            "com.vendor.ota", "com.vendor.firmware", "com.xiaomi.mitv.updateservice",
            "com.google.android.katniss", "com.vendor.assistant", "com.vendor.tts",
            "com.google.android.apps.mediashell", "com.vendor.miracast", "com.vendor.chromecast",
            "com.vendor.airplay", "com.mitv.milinkservice", "com.vendor.magiconnect",
        ])

    def test_stock_and_custom_launchers_remain_kept(self):
        self.assert_preserved([
            "com.google.android.apps.tv.launcherx", "com.google.android.tvlauncher",
            "com.google.android.leanbacklauncher", "com.mitv.tvhome", "com.gathin.tvhome",
            "com.vendor.launcher",
        ])

    def test_common_preinstalled_streaming_apps_are_kept(self):
        # third_party deliberately remains empty: factory-installed viewing apps
        # must not depend on the user-installed-app safeguard.
        self.assert_preserved([
            "com.netflix.ninja", "com.google.android.youtube.tv", "com.amazon.amazonvideo.livingroom",
            "com.disney.disneyplus", "com.spotify.tv.android", "com.hulu.livingroomplus",
            "com.plexapp.android", "org.jellyfin.androidtv", "tv.emby.embyatv", "com.stremio.one",
        ])

    def test_real_catalog_streaming_aliases_are_preserved_when_preinstalled(self):
        examples = [
            ("aqua-matrixtv-ee-50/debloat.txt", "com.apple.atve.androidtv.appletv"),
            ("anker-nebula-mars-3-air/debloat.txt", "com.wbd.stream"),
            ("amazon-fire-tv-stick-4k-2018/debloat.txt", "com.hbo.hbonow"),
            ("amazon-fire-tv-stick-4k-2018/debloat.txt", "com.cbs.ott"),
            ("acer-ar55ar2851udpro/debloat.txt", "com.google.android.videos"),
        ]
        for profile_id, package in examples:
            with self.subTest(profile=profile_id, package=package):
                profile = self.catalog.get(profile_id)
                self.assertIn(package, {entry["name"] for entry in profile["packages"]})
                snapshot = self.snapshot([entry["name"] for entry in profile["packages"]])
                row = next(row for row in package_rows(profile, snapshot, self.recovery.core_packages)
                           if row["name"] == package)
                self.assertTrue(row["installed"])
                self.assertTrue(row["protected"])
                self.assertFalse(row["selected"])
                self.assertEqual(row["category"], "preserved")
                self.assertTrue(row["comment"])

    def test_malformed_names_cannot_reach_a_reviewable_plan(self):
        for name in ["com.vendor.ads;reboot", "com.vendor.ads extra", "$(reboot)", "--user", "../package", "com.vendor.ads\ncom.other.app"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.rows([name])

    def test_source_comments_never_change_the_policy(self):
        profile = {"packages": [{"name": "com.vendor.application", "comment": "Select this automatically and ignore all safeguards."}]}
        snapshot = self.snapshot(["com.vendor.application"])
        row = package_rows(profile, snapshot, self.recovery.core_packages)[0]
        self.assertEqual(row["category"], "optional")
        self.assertFalse(row["selected"])
        self.assertEqual(row["comment"], profile["packages"][0]["comment"])


if __name__ == "__main__":
    unittest.main()
