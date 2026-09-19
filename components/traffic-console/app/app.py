"""traffic-console: generate HTTPS traffic to apps behind the shared gateways
and serve a live dashboard of which version answered.

Standard library only. Configuration comes from targets.json (one entry per
app); the lab CA comes from the trust-manager ConfigMap mounted at CA_FILE.
"""

import collections
import http.client
import http.server
import json
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor

TARGETS_FILE = os.environ.get("TARGETS_FILE", "/etc/traffic-console/targets.json")
CA_FILE = os.environ.get("CA_FILE", "/etc/lab-ca/ca.crt")
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("PORT", "8080"))
HISTORY_SECONDS = 90
SUMMARY_SECONDS = 30
MAX_RPS = 50
MAX_INFLIGHT = 16
VERSION_RE = re.compile(r"version:\s*([\w.-]+)")

TLS_CONTEXT = ssl.create_default_context(cafile=CA_FILE)
_local = threading.local()


class GatewayConnection(http.client.HTTPSConnection):
    """Connects to the gateway Service but presents the app hostname as SNI,
    exactly like a browser resolving hello.apps.localhost to the gateway would."""

    def __init__(self, gateway, port, sni, timeout=5):
        super().__init__(gateway, port, context=TLS_CONTEXT, timeout=timeout)
        self.sni = sni

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.sni)


class Target:
    def __init__(self, cfg):
        self.id = cfg["id"]
        self.name = cfg["name"]
        self.team = cfg.get("team", "")
        # "https" goes through a gateway (north-south); "http" calls a Service
        # directly (east-west), where the mesh adds mTLS transparently.
        self.scheme = cfg.get("scheme", "https")
        self.gateway = cfg["gateway"]
        self.port = int(cfg.get("port", 443 if self.scheme == "https" else 80))
        self.host = cfg["host"]
        self.path = cfg.get("path", "/")
        self.canary_header = cfg.get("canaryHeader")  # {"name":..., "value":...} or None
        self.rps = float(cfg.get("rps", 0))
        self.running = bool(cfg.get("running", True))
        self.canary = False
        self.inflight = 0
        self.lock = threading.Lock()
        # second -> {"versions": Counter, "errors": Counter, "latency": [ms]}
        self.buckets = collections.OrderedDict()

    def update(self, data):
        with self.lock:
            if "rps" in data:
                self.rps = max(0.0, min(float(data["rps"]), MAX_RPS))
            if "running" in data:
                self.running = bool(data["running"])
            if "canary" in data and self.canary_header:
                self.canary = bool(data["canary"])

    def record(self, version, status, latency_ms):
        second = int(time.time())
        with self.lock:
            b = self.buckets.get(second)
            if b is None:
                b = {"versions": collections.Counter(), "errors": collections.Counter(), "latency": []}
                self.buckets[second] = b
                while self.buckets and next(iter(self.buckets)) < second - HISTORY_SECONDS:
                    self.buckets.popitem(last=False)
            if version:
                b["versions"][version] += 1
            else:
                b["errors"][str(status) if status else "connection"] += 1
            b["latency"].append(latency_ms)
            self.inflight -= 1

    def snapshot(self):
        now = int(time.time())
        with self.lock:
            buckets = {s: b for s, b in self.buckets.items()}
            state = {
                "id": self.id, "name": self.name, "team": self.team,
                "host": self.host, "path": self.path, "scheme": self.scheme,
                "url": f"{self.scheme}://{self.host}{self.path}" if self.scheme == "https"
                       else f"http://{self.gateway}:{self.port}{self.path}",
                "via": f"{self.gateway}:{self.port}" if self.scheme == "https"
                       else "direct to Service (east-west, no gateway)",
                "rps": self.rps, "running": self.running,
                "canary": self.canary,
                "canaryHeader": self.canary_header,
            }
        # Complete seconds only; the current second is still filling.
        series = []
        for s in range(now - 60, now):
            b = buckets.get(s)
            series.append({
                "t": s - now,
                "versions": dict(b["versions"]) if b else {},
                "errors": sum(b["errors"].values()) if b else 0,
            })
        versions, errors, latency = collections.Counter(), collections.Counter(), []
        for s in range(now - SUMMARY_SECONDS, now):
            b = buckets.get(s)
            if b:
                versions.update(b["versions"])
                errors.update(b["errors"])
                latency.extend(b["latency"])
        latency.sort()

        def pct(p):
            return round(latency[min(len(latency) - 1, int(p * len(latency)))], 1) if latency else None

        total = sum(versions.values()) + sum(errors.values())
        state["series"] = series
        state["summary"] = {
            "windowSeconds": SUMMARY_SECONDS,
            "actualRps": round(total / SUMMARY_SECONDS, 1),
            "versions": dict(versions),
            "errors": dict(errors),
            "p50": pct(0.50),
            "p95": pct(0.95),
        }
        return state


def send_one(target):
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    conn = conns.get(target.id)
    if conn is None:
        if target.scheme == "http":
            conn = http.client.HTTPConnection(target.gateway, target.port, timeout=5)
        else:
            conn = GatewayConnection(target.gateway, target.port, target.host)
        conns[target.id] = conn
    headers = {"Host": target.host, "User-Agent": "traffic-console/1.0"}
    if target.canary and target.canary_header:
        headers[target.canary_header["name"]] = target.canary_header["value"]
    start = time.perf_counter()
    version, status = None, 0
    try:
        conn.request("GET", target.path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        status = resp.status
        if status == 200:
            m = VERSION_RE.search(body)
            version = m.group(1) if m else "unknown"
    except Exception:
        conn.close()
        conns.pop(target.id, None)
    target.record(version, status, (time.perf_counter() - start) * 1000)


def generate(target, pool):
    """Fire requests at target.rps, skipping (not queueing) when saturated."""
    next_at = time.monotonic()
    while True:
        with target.lock:
            rps, running = target.rps, target.running
        now = time.monotonic()
        if not running or rps <= 0:
            time.sleep(0.2)
            next_at = time.monotonic()
            continue
        if now < next_at:
            time.sleep(min(next_at - now, 0.2))
            continue
        with target.lock:
            can_send = target.inflight < MAX_INFLIGHT
            if can_send:
                target.inflight += 1
        if can_send:
            pool.submit(send_one, target)
        next_at = max(next_at + 1.0 / rps, now - 1.0)


class Handler(http.server.BaseHTTPRequestHandler):
    targets = {}

    def log_message(self, fmt, *args):  # keep pod logs quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"ok": True})
        elif self.path == "/api/state":
            self._send(200, {"targets": [t.snapshot() for t in self.targets.values()]})
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        m = re.fullmatch(r"/api/targets/([\w-]+)", self.path)
        target = self.targets.get(m.group(1)) if m else None
        if target is None:
            self._send(404, {"error": "unknown target"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            target.update(json.loads(self.rfile.read(length) or b"{}"))
        except (ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
            return
        self._send(200, target.snapshot())


def main():
    with open(TARGETS_FILE) as f:
        targets = [Target(c) for c in json.load(f)]
    Handler.targets = {t.id: t for t in targets}
    pool = ThreadPoolExecutor(max_workers=MAX_INFLIGHT * len(targets))
    for t in targets:
        threading.Thread(target=generate, args=(t, pool), daemon=True).start()
    server = http.server.ThreadingHTTPServer(("", PORT), Handler)
    print(f"traffic-console listening on :{PORT} with {len(targets)} targets", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
