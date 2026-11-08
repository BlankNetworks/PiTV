"""Conservative defaults layered on the upstream package and Home guards."""
import re

PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")

# Each is still required to appear in the selected upstream profile and on the TV.
# These are defaults for REVIEW, not certification for an unseen television.
PRIVACY_DEFAULTS = {
    "com.miui.tv.analytics": "Xiaomi TV analytics",
    "com.xiaomi.statistic": "Xiaomi usage statistics",
    "com.google.android.feedback": "Google feedback reporting",
    "tv.samba.ssm": "Samba content recognition",
    "tv.samba.ssm.ui": "Samba content recognition interface",
    "tv.inscape.tvclient.inscapeacr": "Inscape content recognition",
}
ESSENTIAL = {
    "com.android.vending", "com.google.android.gms", "com.google.android.gsf",
    "com.google.android.apps.tv.launcherx", "com.google.android.tvlauncher",
    "com.google.android.leanbacklauncher", "com.mitv.tvhome", "com.gathin.tvhome",
    "com.google.android.tungsten.setupwraith", "com.google.android.tv.remote.service",
    "com.google.android.katniss", "com.google.android.apps.mediashell",
    "com.cltv.mal", "com.mediatek.tis", "com.mediatek.tv.service", "com.mediatek.tv.agent",
    "com.tcl.tv", "com.tcl.suspension", "com.tpa.kb", "com.tcl.systemserver",
    "com.apple.atve.androidtv.appletv", "com.wbd.stream", "com.hbo.hbonow", "com.hbo.hbomax",
    "com.cbs.ott", "com.cbs.ott.firetv", "com.google.android.videos", "com.google.android.music",
    "com.bilibili.tv", "com.bilibili.bilibilitv", "com.tencent.qqlive", "com.canal.android.canal",
}
KEEP_FEATURE = re.compile(
    r"launcher|setupwraith|setupwizard|provision|inputmethod|keyboard|bluetooth|remote|"
    r"autopair|tvinput|systemui|networkstack|firmware|update|(?:^|\.)ota(?:\.|$)|"
    r"miracast|chromecast|mediashell|airplay|milink|magiconnect|assistant|katniss|"
    r"(?:^|\.)tts(?:\.|$)|factory|fallbackhome|(?:^|\.)wifi(?:\.|$)", re.I)
STREAMING = re.compile(r"netflix|youtube|amazon|disney|hulu|spotify|plex|jellyfin|emby|"
                       r"stremio|tubitv|crunchyroll|videolan|paramount|peacock|video|music|player|"
                       r"stream|(?:^|\.)hbo(?:\.|$)|(?:^|\.)cbs(?:\.|$)|bbc|itv|dazn|sling|fubo|discovery", re.I)


def package_rows(profile, snapshot, upstream_core):
    present = set(snapshot["packages"])
    disabled = set(snapshot["disabled"])
    dynamic = set(snapshot.get("protected", [])) | set(snapshot.get("third_party", []))
    home = snapshot.get("home", "").split("/")[0]
    if home:
        dynamic.add(home)
    rows = []
    for entry in profile["packages"]:
        name = entry["name"]
        if not PACKAGE.fullmatch(name):
            raise ValueError("Profile contains an invalid package name")
        protected = bool(upstream_core([name]) or name in ESSENTIAL or name in dynamic
                         or KEEP_FEATURE.search(name) or STREAMING.search(name)
                         or name.startswith("com.android.providers."))
        category = "preserved" if protected else ("privacy" if name in PRIVACY_DEFAULTS else "optional")
        reason = ("Preserves Home, TV functions, updates, casting, voice or installed apps" if protected
                  else PRIVACY_DEFAULTS.get(name, "Optional item from this model's list; review its notes"))
        if name not in present:
            reason = "Not installed on this TV"
        elif name in disabled:
            reason = "Already disabled before this cleanup"
        rows.append({"name": name, "comment": entry.get("comment", ""), "category": category,
                     "selected": name in PRIVACY_DEFAULTS and not protected and name in present and name not in disabled,
                     "protected": protected, "reason": reason, "installed": name in present,
                     "disabled": name in disabled})
    return rows
