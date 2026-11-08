"""Explicit simulation: no ADB, network discovery or TV commands are executed."""
import copy


class DemoTransport:
    binary = "demo"
    def __init__(self):
        self.disabled = set()

    def connect(self, endpoint):
        return "Demo television connected. No real TV was contacted."

    def pair(self, endpoint, code):
        return "Demo pairing accepted. No real TV was contacted."

    def identity_snapshot(self, endpoint):
        return {"identity": self.snapshot(endpoint)["identity"]}

    def snapshot(self, endpoint):
        identity = {"brand": "Xiaomi", "manufacturer": "Xiaomi", "model": "MiTV-MOOQ1",
                    "device": "tarzan", "product": "tarzan", "android": "10", "sdk": "29",
                    "build": "QTG3.200305.006.2591", "fingerprint": "DEMO/xiaomi/tarzan:10/example:user/release-keys",
                    "serial": "SIMULATED-DEVICE-001", "android_id": "0123456789abcdef", "family": "android_tv"}
        packages = ["android", "com.android.settings", "com.android.tv.settings", "com.google.android.gms",
                    "com.android.vending", "com.google.android.tvlauncher", "com.google.android.feedback",
                    "com.miui.tv.analytics", "com.xiaomi.statistic", "com.xiaomi.mitv.updateservice",
                    "com.google.android.youtube.tv", "com.netflix.ninja", "com.xiaomi.mimusic2",
                    "com.google.android.apps.mediashell", "com.mitv.milinkservice", "com.google.android.katniss"]
        return copy.deepcopy({"identity": identity, "packages": sorted(packages), "disabled": sorted(self.disabled),
                              "third_party": [], "home": "com.google.android.tvlauncher/.MainActivity",
                              "protected": ["com.google.android.tvlauncher", "com.android.tv.settings"],
                              "raw": {"getprop": "SIMULATED", "packages": "SIMULATED", "disabled": "SIMULATED",
                                      "settings_global": "SIMULATED", "settings_secure": "SIMULATED",
                                      "settings_system": "SIMULATED", "features": "feature:android.software.leanback"}})

    def screenshot(self, endpoint, destination):
        return None
