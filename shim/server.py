#!/usr/bin/env python3
"""loona-shadow: a logging stand-in for Keyi's cloud API.

Every request the robot makes is captured to requests.jsonl (method, path,
headers, body, remote addr). Responses are canned from responses.json so we
can answer her without touching the real cloud.

Stdlib-only. Runs directly or in Docker.

Two modes:
  * **Direct LAN (default, no cloud)**: TLS on port 8001 with a self-signed
    cert. The robot connects straight to this box over the LAN (command 405
    points her here). Nothing leaves the house. This is the primary mode.
  * **Plain HTTP**: for fronting by a tunnel (PORT=8080, TLS=0).

Env:
  PORT            listen port (default 8001)
  TLS             1 = TLS with self-signed cert (default 1), 0 = plain HTTP
  CERT / KEY      PEM cert/key for TLS (default ../certs/shadow.{crt,key})
  DATA_DIR        where requests.jsonl + responses.json live (default ./data)
  DEFAULT_CODE    success code to use in the default envelope (default 0)
"""
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8001"))
TLS = os.environ.get("TLS", "1") != "0"
CERT = os.environ.get("CERT", os.path.join(_HERE, "..", "certs", "shadow.crt"))
KEY = os.environ.get("KEY", os.path.join(_HERE, "..", "certs", "shadow.key"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_HERE, "data"))
RESPONSES_PATH = os.environ.get("RESPONSES_PATH", os.path.join(DATA_DIR, "responses.json"))
LOG_PATH = os.environ.get("LOG_PATH", os.path.join(DATA_DIR, "requests.jsonl"))
DEFAULT_CODE = int(os.environ.get("DEFAULT_CODE", "0"))

_log_lock = threading.Lock()
_resp_cache = {"mtime": 0.0, "rules": {}, "default": None}
_resp_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _default_envelope():
    return {
        "code": DEFAULT_CODE,
        "result": 0,
        "type": 0,
        "msg": "success",
        "title": "",
        "url": "",
        "data": {},
    }


def _load_responses():
    """Load responses.json, keyed by path. A key ending in '*' is a prefix
    match. The key '*' (or '_default') is the fallback envelope."""
    with _resp_lock:
        try:
            mtime = os.path.getmtime(RESPONSES_PATH)
        except OSError:
            mtime = 0.0
        if mtime != _resp_cache["mtime"]:
            rules, default = {}, None
            try:
                with open(RESPONSES_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for key, val in raw.items():
                    if key in ("*", "_default"):
                        default = val
                    else:
                        rules[key] = val
                _resp_cache.update(mtime=mtime, rules=rules, default=default)
            except Exception as exc:  # keep serving on a bad edit
                print(f"[shim] failed to load responses.json: {exc}", flush=True)
        return _resp_cache["rules"], _resp_cache["default"]


def _lookup(path):
    rules, default = _load_responses()
    if path in rules:
        return rules[path], "exact"
    for key, val in rules.items():
        if key.endswith("*") and path.startswith(key[:-1]):
            return val, "prefix"
    if default is not None:
        return default, "default"
    return _default_envelope(), "builtin"


def _log(entry):
    line = json.dumps(entry, ensure_ascii=False)
    with _log_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(f"[shim] {entry['method']} {entry['path']} -> "
          f"{entry.get('response_code', '?')} ({entry.get('match', '?')})",
          flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "loona-shadow/1.0"
    protocol_version = "HTTP/1.1"

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _handle(self, method):
        started = time.time()
        split = urlsplit(self.path)
        path = split.path
        query = split.query
        body = self._read_body()

        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = None

        entry = {
            "ts": _now(),
            "method": method,
            "path": path,
            "query": query,
            "headers": {k: v for k, v in self.headers.items()},
            "remote": self.client_address[0],
            "body_len": len(body),
            "body": body_text if body_text is not None else body.hex(),
        }

        payload, match = _lookup(path)
        if isinstance(payload, (dict, list)):
            out = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            out = str(payload).encode("utf-8")
            ctype = "text/plain; charset=utf-8"

        entry["response_code"] = 200
        entry["match"] = match
        _log(entry)

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(out)
        except BrokenPipeError:
            pass
        entry["elapsed_ms"] = int((time.time() - started) * 1000)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(RESPONSES_PATH):
        with open(RESPONSES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"_default": _default_envelope()}, fh, indent=2)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    if TLS:
        if not (os.path.exists(CERT) and os.path.exists(KEY)):
            # Auto-generate a self-signed cert so the repo works out of the
            # box. The robot does not pin our server cert (verified
            # empirically), so any valid cert works.
            import subprocess
            os.makedirs(os.path.dirname(CERT), exist_ok=True)
            print(f"[shim] generating self-signed TLS cert -> {CERT}",
                  flush=True)
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", KEY, "-out", CERT, "-days", "3650", "-nodes",
                "-subj", "/O=KEYI/CN=loona-shadow",
            ], check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
        # The robot does not present a client cert and does not pin our
        # server cert (verified empirically), so no extra verification needed.
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        print(f"[shim] TLS listening on 0.0.0.0:{PORT}  "
              f"cert={CERT}  data={DATA_DIR}", flush=True)
    else:
        print(f"[shim] plain HTTP on 0.0.0.0:{PORT}  data={DATA_DIR}",
              flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

