# Optional launcher preparation

Version 0.1.0 keeps the current Home app. This makes the package cleanup and recovery path independently testable before adding model-dependent Home handovers. Home-screen promotions may therefore remain after package cleanup.

The original launcher source is included under `vendor/cleanyourtv/launcher/`. The project does not supply a prebuilt APK. The Linux AAPT2 used by its Android Gradle plugin is x86-64, so the normal Linux build is not a native ARM64 Pi build. See the preserved upstream `tools/docker-build.md` for its emulation route.

On a supported build workstation with full JDK 17, Android SDK platform 34 and build-tools 34.0.0:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
bash scripts/prepare-launcher.sh
```

Use your actual SDK directory. The script builds the pinned source and records the resulting APK checksum in `launcher-artifact.json`. It does not contact a TV. A successful build does not establish compatibility with a particular television.

For a supervised installation, follow `vendor/cleanyourtv/PROMPT.md`, the exact TV folder's notes, `docs/launcher.md` and `tools/docker-build.md`. The upstream install script supports `SKIP_BUILD=1` when a previously built APK is in its expected output directory. Some TVs need different Home behavior, and Fire OS can refuse replacement. Do not disable a stock launcher first, bypass Home/setup guards or lower the minimum Android version to force an install.

Before this becomes an appliance feature, add a separate journaled launcher operation with source/SDK verification, model-specific compatibility tests, original Home restoration and real remote/input/reboot checks. The current source archive contains no built APK and does not claim home-screen ad removal on every model.
