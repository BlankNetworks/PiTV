# Updating the portable appliance

The appliance can be updated in place when new controller code, TV lists or
validated cleanup recipes are ready. The update does not require reflashing the
Pi's SD card. TV backups, recovery records, ADB keys, the legacy access file,
enrollments and approved recipes stay in `/var/lib/tvcleaner/state`.

PiTV checks the latest stable release of `BlankNetworks/PiTV` on GitHub after boot
and about hourly. When a newer version is available, the dedicated service account
requests it through the same path as the **Install PiTV update** button. A
root-owned local worker downloads the exact versioned ZIP from that repo,
compares it to the SHA-256 reported by GitHub for the release asset, then uses
the installed ZIP updater below. It cannot accept a browser-supplied URL or
arbitrary local path. The PiTV app update is automatic; TV cleanup remains off
after updating. It never installs TV firmware. Anyone who can reach this tokenless
PiTV page on the trusted LAN can also request an available PiTV update. Three failed
automatic attempts for one release pause retries until a newer release or manual
review. Network failures checking the release feed are retried on the next timer run.

To publish a new release, edit and test the public repo, increase the literal
version in `tvcleaner/__init__.py`, and put the matching version at the top of
`RELEASE_NOTES.md`. Push to `main`. The GitHub Actions workflow runs the tests,
builds and inspects a ZIP from that commit, then publishes it as the latest stable
release. A push without a new version fails release publishing. No Git credential
is stored on the Pi, and it does not run `git pull` after first installation.

The repo's daily `check-upstream.yml` workflow checks the latest stable
CleanYourTV release. A newer tag produces a pinned vendor snapshot and a PiTV
version bump in a **pull request for review**. It never merges upstream changes
or approves an unattended TV cleanup recipe. Review the changed package lists,
upstream tools, model notes and catalog counts before merging. A merge to `main`
then follows the normal PiTV test-and-release workflow.

The GitHub asset digest detects a changed or incomplete download, but is not an
independent publisher signature. The release repo account remains the trust
source. A manual reviewed ZIP update is also supported as described below.

## Manual update from Mac-local Codex

Keep Codex on the Mac. Ask it to inspect the new release and transfer the release
ZIP to the Pi using SCP or SFTP. Run the update command **on the Pi** through SSH.
Use the updater from the already installed, trusted application:

```bash
sudo bash /opt/universal-tv-debloater/scripts/update-pi.sh \
  /home/pi/universal-tv-debloater-vNEXT.zip \
  --sha256 EXPECTED_SHA256
```

Replace the ZIP filename and `EXPECTED_SHA256` with the values in that release's
handoff. A successful update returns the new version, previous version, HTTP port
and private update-history location. It deliberately leaves automatic cleanup
**off**. Review the release and the applicable recipes, then explicitly enable the
desired automatic policy again. The updater never starts a cleanup on a real TV.

The update must be newer than the installed `major.minor.patch` version. Reinstalling
the same version and downgrading are refused. An unchanged upstream catalog can be
included in a newer controller release; a new upstream commit is not mandatory.

### First upgrade from v0.1.0

Version 0.1.0 did not include the updater. Verify the supplied release ZIP using
the Mac or Pi's system hashing tool **before executing any code from that ZIP**:

```bash
# Mac
shasum -a 256 universal-tv-debloater-v0.2.0.zip
```

```bash
# Pi
sha256sum universal-tv-debloater-v0.2.0.zip
```

Compare the result with the expected hash in the trusted handoff. Have local Codex
review `tvcleaner/releases.py` and `scripts/update-pi.sh` from the extracted release.
Then use that reviewed updater for this first migration:

```bash
sudo bash scripts/update-pi.sh \
  /home/pi/universal-tv-debloater-v0.2.0.zip \
  --sha256 EXPECTED_SHA256
```

Future updates can use the installed updater. Running a downloaded updater before
checking its provenance would execute that updater before its own ZIP checks can
help.

## Inspection without installation

The inspector uses the Python standard library and does not import or execute
code from the release it inspects:

```bash
bash scripts/update-pi.sh inspect /path/to/release.zip \
  --sha256 EXPECTED_SHA256
```

Its report includes the application version, upstream commit, pinned file count,
total file count and archive sizes. Pass `--installed-version 0.1.0` to check the
newer-version requirement during inspection too.

For controlled testing, `stage` extracts the checked release into an absent
directory without installing it or changing services:

```bash
bash scripts/update-pi.sh stage /path/to/release.zip \
  --sha256 EXPECTED_SHA256 --destination /path/to/new-staging-directory
```

Preflight rejects absolute and parent paths, unexpected archive roots, duplicate
and case-colliding filenames, symlinks and special files, privileged file modes,
encrypted entries, credential/runtime directories, malformed versions, invalid
CRCs, missing pinned files and source-manifest mismatches. Limits are 128 MiB for
the ZIP, 512 MiB expanded, 64 MiB per member and 16,000 entries. Upstream executable
tools and catalog text/JSON must be covered by the pinned source manifest.

## What changes during installation

1. Validate the complete ZIP and supplied hash without executing release code.
2. Acquire the update, TV-mutation and configuration locks. Refuse an active
   cleanup/restore, an unfinished TV recovery or an earlier interrupted update.
3. Stage and recheck the release. Save a private configuration snapshot and the
   existing service, command wrapper and USB rule. Disable automatic cleanup.
4. Stop the control service. Retain the previous application directory beside
   `/opt/universal-tv-debloater` and put the staged application in its place.
5. Run the verified release's existing Pi installer, including dependency setup.
6. Check the running/enabled service, installed CLI version, native ADB and the
   local HTTP status endpoint. Automatic cleanup must still be off.

Systemd drop-ins under `tvcleaner.service.d` are left in place, including a locally
chosen HTTP port. Normally the updater reads the effective port from systemd. If
that cannot be determined, add `--port YOUR_PORT`. The HTTP check connects to
`127.0.0.1`, uses no proxy and follows no redirects. A custom service that binds
only to a particular LAN address needs local review before this update workflow.

The updater preserves the runtime state directory. It does not restore an older
TV journal over newer TV data. Recipes and enrollments remain stored; the runtime
still checks their source, profile, firmware and device-state constraints before
any later unattended cleanup.

Starting with PiTV v0.2.2, the installer also writes persistent systemd login
and sleep drop-ins and masks the sleep targets so the Pi stays awake while idle.
The installer signals logind to reload its configuration. Deliberate `reboot` and
`poweroff` remain available. On the Pi, verify after updating with:

```bash
systemctl is-enabled sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
cat /etc/systemd/logind.conf.d/90-pitv-keep-awake.conf
cat /etc/systemd/sleep.conf.d/90-pitv-keep-awake.conf
```

All five targets should report `masked`. Updating the app cannot prevent power
loss, thermal protection, or a separate privileged process from deliberately
shutting down the Pi. If the application update fails after the installer applies
these settings, application rollback does not remove the new power policy.

## Failed or interrupted updates

If the installer or health checks fail, the updater stops the new service and
attempts to restore the previous application, service file, CLI wrapper and
`/etc/udev/rules.d/70-tvcleaner-adb.rules`. It restores the previous service's
enabled/running state and keeps automatic cleanup off. A service that cannot be
stopped blocks code replacement and requires local recovery.

History and private logs are kept under:

```text
/var/lib/tvcleaner-updates/<update-id>/
```

This includes `update.json`, the installation log and configuration/service
backups. The directory is root-only. Do not publish its contents: the original
configuration can contain device identifiers and private enrollment information.
The installer access token is redacted from the retained log when available.

A power interruption can occur between application-directory changes. This is
an application updater, not an A/B operating-system image. If an update is
unfinished, use a reviewed copy of the updater from the downloaded source:

```bash
sudo bash scripts/update-pi.sh recover UPDATE_ID
```

Use the ID printed by the failed update or present in its private history.
Recovery also works when the current application directory is absent but the
previous application backup is intact. It refuses completed updates, so this
command cannot silently downgrade a working release after later TV operations.
If recovery reports a missing or changed backup, keep all files and inspect the
record locally with Codex.

Application rollback does **not** uninstall newly installed Debian/Python system
dependencies or undo unrelated changes made outside this updater. Keep the
previous application and private update history until the new release has been
checked on the Pi. An enabled-at-boot check does not claim that a real reboot or a
physical TV cleanup was tested.

## Expanding TV coverage

A new community TV list is useful catalog evidence, but it does not establish
unattended compatibility with every firmware carrying that model name. A release
that adds automatic coverage should include an exact model/firmware recipe and
the actual supervised validation evidence behind it. Unknown firmware and
incompatible operating systems remain pending review.

For a future update request, provide the model, Android/Fire OS version, firmware
build and the application's sanitized support report when available. Do not send
raw backups or ADB keys. Local Codex can review the upstream changes, prepare a
new versioned source bundle, run tests, transfer it and use this updater. New
catalogs must be pinned as part of the reviewed release; do not run `git pull`
inside the appliance's vendor snapshot and retain old automatic approvals.

## Validation in this development release

Archive validation, refusal paths, concurrent-mutation blocking, state
preservation, installer/health failure rollback and interrupted-update recovery
are tested with temporary files and simulated service commands. No real systemd,
APT, Pi restart or physical-TV update was performed by those tests. The first
hardware upgrade remains a supervised installation check.
