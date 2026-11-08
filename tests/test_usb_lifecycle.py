"""Full cleanup/restore through unmodified upstream scripts and the USB bridge.

Only the native ADB executable and unused Python TCP-ADB dependency are fixtures.
No physical USB device or network is accessed. The real controller, Bash guards,
vendor recovery writer, native-client adapter and USB bridge execute normally.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tvcleaner.demo import DemoTransport
from tvcleaner.service import CleanerService
from tvcleaner.storage import read_json
from tvcleaner.transport import upstream_endpoint

ROOT = Path(__file__).resolve().parents[1]
USB = "usb:TV-TEST-1234"


class FileTv(DemoTransport):
    binary = "/usr/bin/adb"

    def __init__(self, state_file):
        super().__init__()
        self.state_file = state_file

    def snapshot(self, endpoint):
        value = super().snapshot(endpoint)
        state = json.loads(self.state_file.read_text())
        value["disabled"] = sorted(state["disabled"])
        value["home"] = state["home"]
        value["raw"]["settings_global"] = "\n".join(f"{key}={item}" for key, item in sorted(state["settings"].items()))
        return value


class UsbUpstreamLifecycleTests(unittest.TestCase):
    def test_actual_guarded_disable_and_exact_restore_over_usb_bridge(self):
        with tempfile.TemporaryDirectory(prefix="tvcleaner-usb-pipeline-") as directory:
            folder = Path(directory)
            fixture = folder / "source"
            upstream = fixture / "vendor/cleanyourtv"
            fixture.mkdir()
            shutil.copyfile(ROOT / "UPSTREAM.json", fixture / "UPSTREAM.json")
            manifest = json.loads((ROOT / "UPSTREAM.json").read_text())
            for relative in manifest["files"]:
                destination = upstream / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "vendor/cleanyourtv" / relative, destination)

            initial = DemoTransport().snapshot(USB)
            state_file = folder / "fixture-tv.json"
            log_file = folder / "fixture-commands.jsonl"
            state_file.write_text(json.dumps({
                "packages": initial["packages"], "disabled": [], "home": initial["home"],
                "settings": {"window_animation_scale": "0.5", "transition_animation_scale": "1.5"},
            }))
            fake_adb = folder / "fixture-adb"
            fake_adb.write_text(f"#!{sys.executable}\n" + "import json,sys\nfrom pathlib import Path\n" +
                               f"state_path=Path({str(state_file)!r})\nlog_path=Path({str(log_file)!r})\n" + r'''
args=sys.argv[1:]
with log_path.open('a') as handle:
    handle.write(json.dumps(args)+'\n')
if args==['devices','-l']:
    print('List of devices attached\nTV-TEST-1234 device usb:1-1.3 product:tv model:Fixture_TV transport_id:1')
    raise SystemExit(0)
if args[:2]!=['-s','TV-TEST-1234']:
    raise SystemExit('Unexpected target')
if args[2:]==['get-state']:
    print('device')
    raise SystemExit(0)
if args[2:5]!=['shell','-T','-x'] or len(args)!=6:
    raise SystemExit('Unexpected native command')
command=args[5]
state=json.loads(state_path.read_text())
changed=False
if command=='pm list packages --user 0':
    print('\n'.join('package:'+name for name in sorted(state['packages'])))
elif command in ('pm list packages -d --user 0','pm list packages -d'):
    print('\n'.join('package:'+name for name in sorted(state['disabled'])))
elif command=='pm list packages -e --user 0':
    print('\n'.join('package:'+name for name in sorted(set(state['packages'])-set(state['disabled']))))
elif command.startswith('cmd package resolve-activity --brief ') and 'android.intent.category.HOME' in command:
    print(state['home'])
elif command=='getprop ro.adb.secure':
    print('1')
elif command.startswith('s=$(getprop ro.build.version.sdk); for p in ') and 'dumpsys package' in command:
    print('')
elif command.startswith('cmd package query-activities --brief -a android.intent.action.VIEW') and 'android.settings.SETTINGS' in command:
    print('com.android.tv.settings/.Input\n==\ncom.android.settings/.Settings')
elif command.startswith('settings get global '):
    print(state['settings'].get(command.removeprefix('settings get global '),'null'))
elif command.startswith('pm disable-user --user 0 '):
    name=command.removeprefix('pm disable-user --user 0 ')
    if name not in ('com.miui.tv.analytics','com.xiaomi.statistic'):
        raise SystemExit('Unexpected disabled package')
    state['disabled']=sorted(set(state['disabled'])|{name})
    changed=True
    print('Package '+name+' new state: disabled-user')
elif command.startswith('pm enable '):
    name=command.removeprefix('pm enable ')
    state['disabled']=sorted(set(state['disabled'])-{name})
    changed=True
    print('Package '+name+' new state: enabled')
elif command.startswith('cmd package set-home-activity --user 0 '):
    state['home']=command.removeprefix('cmd package set-home-activity --user 0 ')
    changed=True
    print('Success')
elif command.startswith('settings put global '):
    key,value=command.removeprefix('settings put global ').split(' ',1)
    state['settings'][key]=value
    changed=True
elif command.startswith('settings delete global '):
    state['settings'].pop(command.removeprefix('settings delete global '),None)
    changed=True
else:
    raise SystemExit('Unexpected fixture shell command: '+command)
if changed:
    state_path.write_text(json.dumps(state))
''', encoding="utf-8")
            fake_adb.chmod(0o700)

            # The production bridge is unchanged. This test-only launcher injects
            # a native executable that records requests and operates on JSON.
            bridge = fixture / "scripts/adb-usb-wrapper.py"
            bridge.parent.mkdir()
            bridge.write_text(f"#!{sys.executable} -I\nimport sys\nsys.path.insert(0,{str(ROOT)!r})\n"
                              "from tvcleaner.usb_bridge import main\n"
                              f"raise SystemExit(main(_native_adb={str(fake_adb)!r}))\n")
            bridge.chmod(0o700)

            # tv.py imports the optional pure-Python TCP dependency even when
            # using native ADB. Any attempt to use that unused client is fatal.
            python = upstream / "tools/venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text(f"#!{sys.executable} -I\n" + r'''
import runpy,sys,types
from pathlib import Path
def forbidden(*args,**kwargs):
    raise AssertionError('The Python TCP ADB client must not be used for USB')
for name in ('adb_shell','adb_shell.auth','adb_shell.adb_device','adb_shell.auth.keygen','adb_shell.auth.sign_pythonrsa'):
    sys.modules[name]=types.ModuleType(name)
sys.modules['adb_shell.adb_device'].AdbDeviceTcp=forbidden
sys.modules['adb_shell.auth.keygen'].keygen=forbidden
sys.modules['adb_shell.auth.sign_pythonrsa'].PythonRSASigner=forbidden
args=sys.argv[1:]
if args[:1]==['-I']:
    args=args[1:]
sys.argv=args
sys.path.insert(0,str(Path(args[0]).resolve().parent))
runpy.run_path(args[0],run_name='__main__')
''', encoding="utf-8")
            python.chmod(0o700)

            transport = FileTv(state_file)
            service = CleanerService(fixture, folder / "private-state", demo=False, transport=transport)
            before = transport.snapshot(USB)
            plan = service.plan(USB, "xiaomi-mi-tv-4a/debloat.txt")
            result = service.apply(plan["id"], ["com.miui.tv.analytics", "com.xiaomi.statistic"])
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(set(transport.snapshot(USB)["disabled"]), {"com.miui.tv.analytics", "com.xiaomi.statistic"})
            run = read_json(service.runs_dir / result["id"] / "run.json")
            record = read_json(service.runs_dir / result["id"] / run["batches"][0]["record_backup"])
            self.assertEqual(record["host"], upstream_endpoint(USB))
            self.assertIn(".invalid_5555-", run["batches"][0]["record_file"])
            restored = service.restore(result["id"], USB)
            self.assertEqual(restored["status"], "restored", restored)
            self.assertEqual(transport.snapshot(USB), before)
            service.verify_upstream()
            calls = [json.loads(line) for line in log_file.read_text().splitlines()]
            self.assertTrue(all(call == ["devices", "-l"] or call[:2] == ["-s", "TV-TEST-1234"] for call in calls))
            commands = [call[-1] for call in calls if call[2:5] == ["shell", "-T", "-x"]]
            self.assertTrue(any("TV_INPUT_HARDWARE" in command for command in commands))
            self.assertIn("pm disable-user --user 0 com.miui.tv.analytics", commands)
            self.assertIn("pm enable com.miui.tv.analytics", commands)
            self.assertFalse(any(command.startswith(("pm uninstall", "reboot")) for command in commands))


if __name__ == "__main__":
    unittest.main()
