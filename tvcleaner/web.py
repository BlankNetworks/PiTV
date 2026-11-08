"""Local control plane. Deliberately never exposed to the internet."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .storage import atomic_json, read_json
from . import updates


class Jobs:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.lock = threading.Lock()
        self.items = {}

    def submit(self, function, *args):
        with self.lock:
            if sum(item["state"] == "running" for item in self.items.values()) >= 8:
                raise ValueError("There are already several operations running. Wait for one to finish.")
            if len(self.items) > 200:
                self.items = {key: value for key, value in self.items.items() if value["state"] == "running"}
            job_id = secrets.token_hex(16)
            self.items[job_id] = {"state": "running", "result": None, "error": None}
        def run():
            try:
                result = function(*args)
                item = {"state": "done", "result": result, "error": None}
            except Exception as error:
                item = {"state": "error", "result": None, "error": str(error)}
            with self.lock:
                self.items[job_id] = item
        self.pool.submit(run)
        return {"job_id": job_id}

    def get(self, job_id):
        with self.lock:
            return dict(self.items[job_id])


def get_token(state):
    path = Path(state) / "access.json"
    value = read_json(path)
    if value is None:
        value = {"token": secrets.token_urlsafe(32)}
        atomic_json(path, value)
    return value["token"]


def make_server(service, host="127.0.0.1", port=8080):
    jobs = Jobs()

    class Handler(BaseHTTPRequestHandler):
        server_version = "TV-Cleaner"

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, fmt, *args):
            # Never log URL, pairing code, authorization header or body.
            pass

        def respond(self, status, data, content_type="application/json"):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            for key, value in {"Content-Type": content_type, "Content-Length": str(len(body)),
                               "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                               "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                               "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def local_request_allowed(self):
            host = self.headers.get("Host", "")
            try:
                name = urlsplit("//" + host).hostname
                if name not in ("pitv.local", "tvcleaner.local", "localhost"):
                    address = ipaddress.ip_address(name)
                    if not (address.is_private or address.is_link_local or address.is_loopback):
                        raise ValueError("Host is not local")
            except (ValueError, TypeError):
                self.respond(403, {"error": "Use this Pi's local address or pitv.local."})
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                    self.respond(403, {"error": "Requests must come from this control page."})
                    return False
            return True

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                self.respond(200, (service.root / "tvcleaner/static/index.html").read_bytes(), "text/html; charset=utf-8")
                return
            if url.path in ("/advanced", "/advanced.html"):
                self.respond(200, (service.root / "tvcleaner/static/advanced.html").read_bytes(), "text/html; charset=utf-8")
                return
            if url.path == "/favicon.ico":
                self.respond(204, b"", "image/x-icon")
                return
            if not url.path.startswith("/api/"):
                self.respond(404, {"error": "Not found"})
                return
            public_visit = url.path.startswith("/api/visit/")
            path = url.path.replace("/api/visit/", "/api/", 1) if public_visit else url.path
            if public_visit and path not in ("/api/status", "/api/runs", "/api/update/status") and not path.startswith("/api/jobs/"):
                self.respond(404, {"error": "Not found"})
                return
            if not self.local_request_allowed():
                return
            try:
                if path == "/api/status":
                    data = service.status()
                elif path == "/api/profiles":
                    query = parse_qs(url.query).get("q", [""])[0][:200]
                    data = {"profiles": service.catalog.list_profiles(query=query)}
                elif path == "/api/runs":
                    data = service.list_runs()
                elif path == "/api/recipes":
                    data = service.list_recipes()
                elif path == "/api/update/status":
                    data = updates.status(service.state)
                elif path.startswith("/api/jobs/"):
                    data = jobs.get(path.rsplit("/", 1)[-1])
                else:
                    self.respond(404, {"error": "Not found"})
                    return
                self.respond(200, data)
            except KeyError:
                self.respond(404, {"error": "This operation is no longer available. Refresh the TV status."})
            except Exception as error:
                self.respond(400, {"error": str(error)})

        def do_POST(self):
            raw_path = urlsplit(self.path).path
            public_visit = raw_path.startswith("/api/visit/")
            path = raw_path.replace("/api/visit/", "/api/", 1) if public_visit else raw_path
            if public_visit and path not in ("/api/scan", "/api/connect", "/api/pair", "/api/inspect", "/api/plan", "/api/apply", "/api/restore", "/api/update/check", "/api/update/install"):
                self.respond(404, {"error": "Not found"})
                return
            if not self.local_request_allowed():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.respond(415, {"error": "Use JSON requests."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    self.respond(413, {"error": "Request is empty or too large."})
                    return
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("Request must be a JSON object.")
                routes = {
                    "/api/scan": (service.scan, (value.get("cidr", ""),)),
                    "/api/connect": (service.connect, (value.get("endpoint", ""),)),
                    "/api/pair": (service.pair, (value.get("endpoint", ""), value.get("code", ""))),
                    "/api/inspect": (service.inspect, (value.get("endpoint", ""),)),
                    "/api/plan": (service.plan, (value.get("endpoint", ""), value.get("profile_id", ""))),
                    "/api/apply": (service.apply, (value.get("plan_id", ""), value.get("packages", []), value.get("enroll", False))),
                    "/api/restore": (service.restore, (value.get("run_id", ""), value.get("endpoint", ""))),
                    "/api/report": (service.support_report, (value.get("endpoint", ""),)),
                    "/api/recipes/create": (service.create_recipe, (value.get("run_id", ""), value.get("checks"), value.get("notes", ""))),
                    "/api/automatic/unblock": (service.clear_automatic_block, (value.get("endpoint", ""),)),
                    "/api/update/check": (updates.check, ()),
                }
                if path == "/api/automatic":
                    self.respond(200, service.set_automatic(value.get("enabled"), value.get("cidr", ""), value.get("network_mode", "manual"), value.get("usb", True), value.get("policy", "enrolled")))
                elif path == "/api/recipes/enabled":
                    self.respond(200, service.set_recipe_enabled(value.get("id", ""), value.get("enabled")))
                elif path == "/api/update/install":
                    self.respond(200, updates.request_install(service.state, value.get("version"), value.get("sha256"), demo=service.demo))
                elif path in routes:
                    function, args = routes[path]
                    self.respond(202, jobs.submit(function, *args))
                else:
                    self.respond(404, {"error": "Not found"})
            except Exception as error:
                self.respond(400, {"error": str(error)})

        def do_OPTIONS(self):
            self.respond(403, {"error": "Cross-origin access is disabled."})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.jobs = jobs
    return server


def serve(service, host, port):
    server = make_server(service, host, port)
    stop = threading.Event()
    def automatic_worker():
        while not stop.is_set():
            try:
                service.automatic_pass()
            except Exception:
                # automatic_pass records the error for the phone UI and preserves the worker.
                pass
            stop.wait(15)
    worker = threading.Thread(target=automatic_worker, daemon=True)
    worker.start()
    print(f"TV control page listening on {host}:{server.server_port}", flush=True)
    print("Local controls are available on the Pi's reachable LAN without a token.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        server.jobs.pool.shutdown(wait=True)
