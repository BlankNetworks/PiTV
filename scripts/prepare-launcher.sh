#!/usr/bin/env bash
# Optional source build on a supported workstation, before travelling with the Pi.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM_DIR="$PROJECT_DIR/vendor/cleanyourtv"
case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Darwin:arm64|Darwin:x86_64) ;;
  *) echo "Prepare this optional APK on an x86-64 Linux or supported Mac build host. Native Linux ARM AAPT2 is not supplied upstream." >&2; exit 1 ;;
esac
if ! command -v java >/dev/null; then
  echo "Install a full JDK 17 and Android SDK platform 34 plus build-tools 34.0.0 first." >&2
  exit 1
fi
if [ -z "${ANDROID_HOME:-}" ] || [ ! -f "$ANDROID_HOME/platforms/android-34/android.jar" ]; then
  echo "Set ANDROID_HOME to an SDK with platforms;android-34 and build-tools;34.0.0 installed." >&2
  exit 1
fi
cd "$UPSTREAM_DIR/launcher"
bash ./gradlew --no-daemon assembleRelease
python3 - "$PROJECT_DIR" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
apk=root/'vendor/cleanyourtv/launcher/app/build/outputs/apk/release/app-release.apk'
upstream=json.loads((root/'UPSTREAM.json').read_text())
(root/'launcher-artifact.json').write_text(json.dumps({'upstream_commit':upstream['commit'],'sha256':hashlib.sha256(apk.read_bytes()).hexdigest()},indent=2)+'\n')
print('Prepared optional launcher APK:',apk)
print('The first controller release does not automate a launcher handover. Follow docs/LAUNCHER.md for the upstream model-specific procedure.')
PY
