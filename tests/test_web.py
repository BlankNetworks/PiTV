import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tvcleaner.service import CleanerService
from tvcleaner.web import make_server

ROOT = Path(__file__).resolve().parents[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = CleanerService(ROOT, self.directory.name, demo=True)
        self.server = make_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.jobs.pool.shutdown(wait=True)
        self.directory.cleanup()

    def request(self, path, data=None, *, origin=None, method=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        connection.request(method or ("GET" if data is None else "POST"), path,
                           body=None if data is None else json.dumps(data), headers=headers)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        connection.close()
        return status, json.loads(body) if content_type == "application/json" else body

    def job(self, path, data):
        status, submitted = self.request(path, data)
        self.assertEqual(status, 202)
        for _ in range(100):
            job_path = "/api/visit/jobs/" if path.startswith("/api/visit/") else "/api/jobs/"
            status, result = self.request(job_path + submitted["job_id"])
            if result["state"] != "running":
                self.assertEqual(result["state"], "done", result)
                return result["result"]
            time.sleep(0.01)
        self.fail("Demo job did not complete")

    def test_all_controls_work_without_token(self):
        self.assertEqual(self.request("/api/status")[0], 200)
        self.assertEqual(self.request("/api/profiles")[0], 200)
        self.assertEqual(self.request("/api/recipes")[0], 200)
        self.assertEqual(self.request("/api/automatic", {"enabled": False, "network_mode": "auto"})[0], 200)
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"1 \xc2\xb7 Find TVs", page)
        self.assertNotIn(b"Pi access token", page)
        self.assertNotIn(b"token required", page)
        status, advanced = self.request("/advanced")
        self.assertEqual(status, 200)
        self.assertIn(b"Universal TV Debloater", advanced)
        self.assertNotIn(b"Pi access token", advanced)
        self.assertNotIn(b"tokenInput", advanced)

    def test_cross_origin_and_get_mutations_blocked(self):
        self.assertEqual(self.request("/api/status", host="attacker.example")[0], 403)
        self.assertEqual(self.request("/api/status", origin="https://example.invalid")[0], 403)
        self.assertEqual(self.request("/api/automatic", {"enabled": True, "cidr": "192.168.1.0/24"}, origin="https://example.invalid")[0], 403)
        self.assertEqual(self.request("/api/apply")[0], 404)
        self.assertFalse(self.service.config["automatic"]["enabled"])

    def test_visit_routes_remain_manual_only_and_same_origin(self):
        self.assertEqual(self.request("/api/visit/status")[0], 200)
        self.assertEqual(self.request("/api/visit/status", host="attacker.example")[0], 403)
        self.assertEqual(self.request("/api/visit/status", origin="http://attacker.example")[0], 403)
        self.assertEqual(self.request("/api/visit/automatic", {"enabled": True})[0], 404)
        self.assertFalse(self.service.config["automatic"]["enabled"])
        all_scan = self.job("/api/visit/scan", {"cidr": "ALL"})
        self.assertEqual(all_scan["devices"][0]["transport"], "demo")
        scan = self.job("/api/visit/scan", {"cidr": "192.168.1.0/24"})
        endpoint = scan["devices"][0]["endpoint"]
        self.job("/api/visit/connect", {"endpoint": endpoint})
        inspection = self.job("/api/visit/inspect", {"endpoint": endpoint})
        self.assertEqual(inspection["identity"]["model"], "MiTV-MOOQ1")
        plan = self.job("/api/visit/plan", {"endpoint": endpoint, "profile_id": "xiaomi-mi-tv-4a/debloat.txt"})
        selected = [row["name"] for row in plan["packages"] if row["selected"]]
        result = self.job("/api/visit/apply", {"plan_id": plan["id"], "packages": selected, "enroll": False})
        self.assertEqual(result["status"], "completed")
        restored = self.job("/api/visit/restore", {"run_id": result["id"], "endpoint": endpoint})
        self.assertEqual(restored["status"], "restored")

    def test_update_routes_on_both_pages(self):
        self.assertEqual(self.request("/api/visit/update/status")[1]["state"], "idle")
        with patch("tvcleaner.web.updates.check", return_value={"available": False, "current_version": "0.2.0"}):
            result = self.job("/api/visit/update/check", {})
            self.assertFalse(result["available"])
        with patch("tvcleaner.web.updates.request_install", return_value={"state": "requested", "version": "0.2.1"}) as install:
            status, result = self.request("/api/update/install", {"version": "0.2.1", "sha256": "a" * 64})
            self.assertEqual(status, 200)
            self.assertEqual(result["state"], "requested")
            self.assertTrue(install.call_args.kwargs["demo"])
        self.assertEqual(self.request("/api/visit/update/anything", {})[0], 404)

    def test_wifi_relay_routes_are_tokenless_and_do_not_touch_real_network_in_demo(self):
        status, wifi = self.request("/api/visit/wifi/status")
        self.assertEqual(status, 200)
        self.assertTrue(wifi["demo"])
        self.assertEqual(wifi["ap"]["ssid"], "PITV")
        networks = self.job("/api/visit/wifi/scan", {"interface": "wlan1"})
        self.assertEqual(networks["networks"][0]["ssid"], "Demo TV Wi-Fi")
        self.assertTrue(self.job("/api/visit/wifi/connect", {"interface": "wlan1", "ssid": "Demo TV Wi-Fi", "password": "examplepass"})["demo"])
        self.assertEqual(self.request("/api/visit/wifi/ap", {"ssid": "PITV", "password": "pitv12345"}, origin="http://attacker.example")[0], 403)

    def test_complete_demo_api_workflow(self):
        status, initial = self.request("/api/status")
        self.assertTrue(initial["demo"])
        self.assertGreaterEqual(initial["catalog"]["profiles"], 433)
        scan = self.job("/api/scan", {"cidr": "192.168.1.0/24"})
        endpoint = scan["devices"][0]["endpoint"]
        self.job("/api/connect", {"endpoint": endpoint})
        inspection = self.job("/api/inspect", {"endpoint": endpoint})
        self.assertEqual(inspection["identity"]["model"], "MiTV-MOOQ1")
        self.assertNotIn("serial", inspection["identity"])
        plan = self.job("/api/plan", {"endpoint": endpoint, "profile_id": "xiaomi-mi-tv-4a/debloat.txt"})
        selected = [row["name"] for row in plan["packages"] if row["selected"]]
        self.assertEqual(len(selected), 3)
        result = self.job("/api/apply", {"plan_id": plan["id"], "packages": selected, "enroll": True})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["changed"]), 3)
        restored = self.job("/api/restore", {"run_id": result["id"], "endpoint": endpoint})
        self.assertEqual(restored["status"], "restored")
        self.assertFalse(self.service.transport.disabled)

    def test_bad_input_and_missing_job(self):
        self.assertEqual(self.request("/api/automatic", {"enabled": "yes", "cidr": "192.168.1.0/24"})[0], 400)
        self.assertEqual(self.request("/api/jobs/nonexistent")[0], 404)
        self.assertEqual(self.request("/api/automatic", {"enabled": True, "cidr": "8.8.8.0/24"})[0], 400)


if __name__ == "__main__":
    unittest.main()
