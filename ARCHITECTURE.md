# Universal TV Debloater for Raspberry Pi

Companion application around a pinned, unmodified CleanYourTV checkout. Python 3.10+ and the standard library for the controller/UI; upstream Python dependencies and Debian's native ARM64 `adb` for real TVs. No hosted website, no account, no TV rooting, no firmware flashing, no package uninstalling.

## Module contracts

`tvcleaner/catalog.py`: `Catalog(upstream: Path)`; `summary() -> dict`; `list_profiles(query='', limit=1000) -> list[dict]`; `get(profile_id) -> dict`; `match(identity: dict, packages: set[str]) -> list[dict]`. Profiles contain `id`, `tv`, `brand`, `os`, `notes`, `packages` (list of `{name, comment}`), `source` (relative path), `digest` (SHA256 of source list), and `warnings`. Matches are profile metadata plus `score`, `reasons`, `exact_model`. Scores are suggestions, never permission to modify a TV. Read ALL examples/*/tv.json and all debloat*.txt variants, preserving variant names. No writes to vendor. Malformed lists remain visible but unusable, with warnings.

`tvcleaner/transport.py`: `TransportError`; `AdbTransport(adb_binary=None, home=None, timeout=20)` with `connect(endpoint) -> str`, `pair(endpoint, code) -> str`, `shell(endpoint, command) -> str`, `snapshot(endpoint) -> dict`, `screenshot(endpoint, destination: Path)`. Strict private IPv4 endpoints and explicit usb:<serial> selectors; USB enumeration and authorization are checked before use. No hostname DNS rebinding. ADB uses the appliance-specific localhost:5038 server. No shell=True. Snapshot: `identity` dict with manufacturer, brand, model, device, product, android, sdk, build, fingerprint, serial, android_id, family; `packages`, `disabled`, `third_party` lists; `home` component string; `protected` list of dynamic HOME/input/IME packages; `raw` dict of getprop, packages, disabled, settings_global/settings_secure/settings_system, features, home. Read-only probes must fail on incomplete essential data; a non-TV Android device must not pass. Preserve unknown device families. Transport uses HOME passed by controller for ADB key location. Raw data stays in private state, not HTTP output.

`tvcleaner/discovery.py`: `local_networks() -> list[dict]` each `cidr`, `interface`; `discover(cidr: str, adb_binary=None) -> list[dict]` each `endpoint`, `address`, `port`, `transport`, `status`. Only RFC1918 or link-local IPv4 networks of /24 or smaller, max 256 addresses; bounded TCP 5555 probes and read-only adb mDNS service discovery for pairing/connect ports. Never auto-connect or mutate devices in discovery. Endpoint validation shared with transport. ADB authorization always remains on TV.

`tvcleaner/service.py`: root-owned orchestration. Load pinned catalog, read-only inspection, server-stored expiring plans, per-device backups, bounded batches through existing `tools/disable.sh`, verification, rollback through `tools/restore.sh`, per-device/fingerprint enrollment for unattended runs. One global mutation lock. Refuse changed TV identity/firmware/state and malformed/missing restoration records. Protect stock Home, setup, TV inputs, keyboards, remote, firmware updates, casting and streaming apps by default. Automatic cleanup uses exact enrolled identity/firmware approvals or owner-attested exact firmware recipes. New units may reuse a matching recipe after authorization. Unknown firmware gets no mutation. Recipes require linked successful cleanup/restore evidence plus owner functional checks. No trial disabling or brute forcing profiles.

`tvcleaner/web.py`: HTTP API plus static page, running as the dedicated tvcleaner account. Auth token for all /api endpoints, same-origin checks, no CORS, no token in query strings/logs. GET /api/status, /api/profiles?q=, /api/runs; POST /api/scan {cidr}, /api/connect {endpoint}, /api/pair {endpoint,code}, /api/inspect {endpoint}, /api/plan {endpoint,profile_id}, /api/apply {plan_id,packages,enroll:false}, /api/restore {run_id,endpoint}, /api/automatic {enabled,cidr}. Responses JSON. Long operations return {job_id}; GET /api/jobs/<id> returns {state:'running'|'done'|'error',result,error}. Connect/inspect/plan/apply/restore/scan all may be jobs. No changes on GET. No browser dependencies/CDN. Jobs are serialized for mutations. Offline demo mode must be clearly labeled and cannot touch devices.

## UI shapes

status: {version, demo, catalog:{profiles,brands,...}, networks:[{cidr,interface}], automatic:{enabled,cidr}, upstream:{commit}, launcher:{available}, limitations:[]}

scan result: {devices:[{endpoint,address,port,transport,status}]}

inspect result: {endpoint,identity:{brand,manufacturer,model,android,build,family},matches:[{id,tv,brand,os,score,reasons,exact_model}],package_count,disabled_count,automatic_eligible,warnings:[]}

plan result: {id,endpoint,profile:{id,tv,os,notes,warnings},identity:{brand,model,android,build},packages:[{name,comment,category,selected,protected,reason,installed,disabled}],warnings:[],expires_at,automatic_eligible}

apply/restore result: {id,status,summary,changed:[],skipped:[],errors:[],needs_manual_check:true}. runs list: {runs:[{id,created_at,status,tv,summary}]}. Job poll wraps response in result.

## Delivery

An installable source ZIP with all upstream profiles and tools, a Pi installation script, systemd service, local phone UI, CLI, offline demo and tests. Include attribution and pinned commit. Exclude .git, virtualenvs, credentials, real device state and build caches. No claim of physical TV verification until actual hardware is used. Non-Android platforms get a compatibility guide, not a fake adapter.

## Version 0.2 appliance additions

`tvcleaner/recipes.py` defines a strict data-only schema with a content digest and exact matching rules. No commands, regular expressions or broad model patterns are accepted. Owner recipes and evidence live privately in configuration; reviewed release recipes can live under `recipes/*.json`. No real hardware recipes are bundled initially. `automatic_blocks` persist failed attempts; the automatic worker uses a process lock and a thread lock. `appliance.json` provides durable status and attention reasons.

`tvcleaner/usb_bridge.py` plus `scripts/adb-usb-wrapper.py` adapt only the pinned upstream native ADB selector to an explicitly enumerated USB device. A synthetic .invalid label is never resolved. Original upstream disable/restore scripts remain unchanged.

`tvcleaner/releases.py` and `scripts/update-pi.sh` validate a user-provided ZIP and expected checksum, lock mutation state, preserve backups/credentials and keep automatic operation off through update. This is a manual trusted-release update path, not signed over-the-air distribution.

Direct Pi-to-TV Ethernet addressing is pending implementation. docs/DIRECT_LAN.md specifies an explicitly designated isolated TV-facing port and checks needed before configuring NetworkManager/DHCP. No direct-LAN helper is shipped, and the normal installer never changes networking.

Additional authenticated API: GET /api/recipes; POST /api/report, /api/recipes/create, /api/recipes/enabled, /api/automatic/unblock. Automatic configuration accepts network_mode (auto/manual/usb), usb, policy (recipes/enrolled), enabled and cidr.
