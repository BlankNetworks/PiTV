# Continue in Codex on the Mac

Open the extracted v0.2.0 development source in Codex on the Mac and paste the prompt below. Importing this chat alone does not transfer the source ZIP or files from the Pi.

```text
Continue this Universal TV Debloater project and install it on my Raspberry Pi 4. Run Codex locally on my Mac and use SSH to manage the Pi. Start with pi@192.168.2.155; reuse an existing working SSH account/configuration if it differs.

GOAL
Build a portable, headless appliance. After initial TV authorization, I want to power the Pi, connect it over a supported LAN or USB link, have it identify the exact TV/firmware, apply a verified cleanup automatically, and move on. Normal visits must not require a laptop, Codex or an open browser. I will request updates as TV support grows.

SOURCE AND CURRENT STATE
Use the attached/extracted universal-tv-debloater v0.2.0 development package. Compare and preserve existing local changes. Upstream is https://github.com/Gathin23/cleanyourtv, pinned at 04aec4b7c763ed642d3994d25e5b89dd57014a6d. Cloning upstream alone does not include this companion app.
Read README.md, RELEASE_NOTES.md, ARCHITECTURE.md and docs/RECIPES.md, UPDATING.md, DIRECT_LAN.md, HARDWARE_VALIDATION.md and COMPATIBILITY.md.
Implemented source includes network/USB ADB discovery, phone UI, systemd installer, backups/restore, exact firmware recipes, headless automation and a reviewed-release updater. Direct Pi-to-TV Ethernet addressing is unfinished. No preapproved hardware recipes are bundled. Development tests use simulations; the chat workspace did not install or validate a physical Pi or TV.

RECOVER BEFORE INSTALLING
My previous SSH/Codex session quit. Establish what completed before rerunning anything. Inspect Mac project files/Git status, then the Pi OS/architecture, existing installation, service, journal, processes, port 8080, saved runs and update state. Preserve private state, restore records, configuration and ADB keys. Do not reflash, reset or blindly reinstall.
Keep SSH host verification enabled. If a host key changed, establish the Pi's current fingerprint before replacing only its stale entry. Let me enter passwords locally, not in chat. Clearly distinguish Mac commands from Pi commands. Keep a local progress/checkpoint file so a new session can resume.

INSTALL AND FINISH
I authorize required dependencies, application installation/update, service setup and reversible fixes for this project. Inspect scripts before executing. Back up the existing application and private state. Use scripts/install-pi.sh for a fresh install and docs/UPDATING.md for upgrades/recovery. The first v0.1-to-v0.2 upgrade needs the documented bootstrap because v0.1 has no updater. Keep automatic cleanup paused during setup.
Normal LAN discovery must follow the current private Ethernet/Wi-Fi networks rather than hard-code my subnet. USB requires a TV exposing a genuine ADB device interface, with debugging enabled and authorization accepted; a normal TV USB socket may not support it. Use a proper Pi power supply and the TV's documented data connection. Do not bypass TV authorization or promise zero-touch pairing.
Finish direct Pi-to-TV Ethernet mode after inspecting real interfaces and the management route. Use an explicitly isolated TV-facing port, nonconflicting subnet and DHCP for that link. Never put a DHCP server on the household LAN or reconfigure our only SSH management connection. Use Wi-Fi or another interface for management. If that arrangement is unavailable, finish normal LAN/USB setup and state the exact remaining hardware requirement.

AUTOMATIC OPERATION
Start on boot and operate without cloud/AI access. Match exact TV identity, firmware and system state to an approved recipe or individual enrollment. Never try successive package lists by applying them. Unknown firmware, unsupported platforms or ambiguous targets must wait without changes. Deduplicate the same TV across USB/LAN. Preserve idempotence and block blind retries after failures.
Retain protected Home, remote/input, networking, update, casting and streaming services; small package batches; durable before-state and exact restore; and post-change verification/recovery. Run one supervised cleanup and restore before approving a recipe. Obtain my actual remote/input/playback/network/reboot observations; never invent hardware checks. Arm automation only after the required validation. With no TV available, finish Pi setup and leave automation paused with concrete first-TV steps.
Keep Android TV/Google TV/compatible Fire OS separate from Tizen, webOS, Roku, VIDAA, SmartCast and Vega. Do not promise every TV or removal of all in-app ads. Preserve the current launcher unless a separate reviewed and tested launcher procedure is added.

UPDATES AND COMPLETION
Use reviewed release ZIPs with an expected SHA-256; preserve configuration, keys, backups and recipes. Pause automation during updates and invalidate stale approvals when relevant. Do not silently pull changing upstream lists or call checksums digital signatures. Keep support reports free of device identifiers and household/private app data.
Run the project suite and syntax checks, then validate the real Pi service, permissions, dedicated ADB daemon, discovery and restart behavior. Test USB, direct Ethernet/DHCP and the phone UI when the hardware is available. Report simulated and physical validation separately.
Finish with what changed, installed version, service/automatic-mode status, actual phone URL, local token-retrieval command, TV authorization and first-test instructions, restore/update commands, and precise remaining limitations. Ask only for genuinely missing facts, credentials or physical actions. Complete all authorized work before stopping.
```

To resume an existing local CLI conversation, run `codex resume --last` from its project directory. A new session can instead use this prompt and the source files.
