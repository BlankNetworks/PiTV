#!/usr/bin/env bash
# Upgrade a previously installed appliance from a reviewed release ZIP.
# sudo bash scripts/update-pi.sh RELEASE.zip --sha256 EXPECTED_SHA256
# sudo bash scripts/update-pi.sh recover UPDATE_ID
set -euo pipefail
umask 077
RELEASE_TOOLS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in
  inspect|stage|update|recover)
    exec python3 -I "$RELEASE_TOOLS_ROOT/tvcleaner/releases.py" "$@"
    ;;
  -h|--help|"")
    echo "Usage: sudo bash scripts/update-pi.sh RELEASE.zip --sha256 EXPECTED_SHA256"
    echo "Inspect only: bash scripts/update-pi.sh inspect RELEASE.zip --sha256 EXPECTED_SHA256"
    echo "Interrupted update: sudo bash scripts/update-pi.sh recover UPDATE_ID"
    ;;
  *)
    exec python3 -I "$RELEASE_TOOLS_ROOT/tvcleaner/releases.py" update "$@"
    ;;
esac
