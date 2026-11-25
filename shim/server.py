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
import hashlib
import http.client
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
# Forward (MITM) mode: when UPSTREAM is set, each request is relayed to the
# real Keyi cloud and the genuine response is captured + relayed back to the
# robot. Used once to learn the exact response schemas (STT/GPT/TTS), after
# which we switch back to canned/local mode.
UPSTREAM = os.environ.get("UPSTREAM", "")  # e.g. https://usloona.keyirobot.cn
CAPTURE_PATH = os.environ.get("CAPTURE_PATH",
                              os.path.join(DATA_DIR, "captures.jsonl"))
# MODE: local (default; answer AI endpoints with local services via local.py),
#       canned (return responses.json), or forward (relay to UPSTREAM).
# Setting UPSTREAM forces forward mode regardless of MODE.
MODE = os.environ.get("MODE", "local")

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


def _log_capture(entry):
    """Log a full request+real-response pair (forward mode)."""
    line = json.dumps(entry, ensure_ascii=False)
    with _log_lock:
        with open(CAPTURE_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(f"[shim] FORWARD {entry['method']} {entry['path']} -> "
          f"{entry.get('upstream_status', '?')}", flush=True)


# Hop-by-hop headers that must not be forwarded to / from the upstream.
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers", "transfer-encoding",
               "upgrade", "host", "content-length"}

_upstream_ctx = None

# SHA256 of the real Keyi cloud leaf cert(s), computed out-of-band with
# openssl. We pin instead of validating the CA chain, because Python's strict
# TLS rejects Keyi's self-signed root (missing keyCertSign usage) even though
# the chain is valid. Pinning still protects against MITM: if the presented
# cert isn't a real Keyi one, we abort.
#
# Keyi runs TWO services on the same host:
#   * :443  -> the *app* API (login, appapiloona, ...)
#   * :8001 -> the *robot* API (check/loona, datapoint/loona, inference/stt,
#              GPT, TTS, ...). The robot talks to THIS one.
# Each presents a different leaf cert, so we pin both.
# To refresh after a Keyi cert rotation:
#   echo | openssl s_client -connect usloona.keyirobot.cn:<port> 2>/dev/null \
#     | openssl x509 -outform DER | sha256sum
_PINNED_CLOUD_CERT_SHA256 = {
    # usloona.keyirobot.cn:443 (app API)
    "e0b158ba26ed3a210c28e178ccf969ba9fd490fbcae5bf7aa8ee64539c537397",
    # usloona.keyirobot.cn:8001 (robot API)
    "a378c401aeb0d5e1320c8fc9eb94ddcb7353459d4be9c8f0933dc8a1ca118469",
}


def _get_upstream_ctx():
    """TLS context for the upstream (real Keyi) connection.

    Server-side: verified by certificate pinning (see _PINNED_CLOUD_CERT_SHA256)
    rather than CA-chain validation, so the context is unverified.
    Client-side: Keyi's nginx requires mTLS, so we present the app's client
    cert+key (extracted from the APK; Keyi accepts it for these endpoints).
    """
    global _upstream_ctx
    if _upstream_ctx is None:
        ctx = ssl._create_unverified_context()
        crt = os.environ.get("UPSTREAM_CLIENT_CRT",
                             os.path.join(_HERE, "..", "certs",
                                          "app_client.crt.pem"))
        key = os.environ.get("UPSTREAM_CLIENT_KEY",
                             os.path.join(_HERE, "..", "certs",
                                          "app_client.key.pem"))
        if os.path.exists(crt) and os.path.exists(key):
            ctx.load_cert_chain(crt, key)
        else:
            print("[shim] WARNING: no upstream client cert; mTLS will fail",
                  flush=True)
        _upstream_ctx = ctx
    return _upstream_ctx


def _verify_pin(conn):
    """Abort unless the upstream presented a pinned Keyi cloud cert."""
    der = conn.sock.getpeercert(binary_form=True)
    if not der:
        raise ssl.SSLCertVerificationError("no upstream cert presented")
    fp = hashlib.sha256(der).hexdigest()
    if fp not in _PINNED_CLOUD_CERT_SHA256:
        raise ssl.SSLCertVerificationError(
            f"cloud cert pin mismatch: got {fp}")
    return fp


def _forward(method, path, query, headers, body):
    """Relay the request to the real Keyi cloud.

    Returns (status, response_headers_dict, response_body_bytes). Her auth
    headers (X-loona-token, Ocp-Apim-Subscription-Key, X-total-version, ...)
    are passed through verbatim so Keyi authenticates her as normal.
    """
    split = urlsplit(UPSTREAM)
    host = split.netloc
    url = path + (f"?{query}" if query else "")
    conn = http.client.HTTPSConnection(host, timeout=60,
                                       context=_get_upstream_ctx())
    fwd = {k: v for k, v in headers if k.lower() not in _HOP_BY_HOP}
    fwd["Host"] = host
    conn.request(method, url, body=body if body else None, headers=fwd)
    _verify_pin(conn)  # abort if the upstream isn't the real Keyi cloud
    resp = conn.getresponse()
    resp_body = resp.read()
    resp_headers = {k: v for k, v in resp.getheaders()}
    status = resp.status
    conn.close()
    return status, resp_headers, resp_body


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

        if UPSTREAM:
            self._handle_forward(entry, method, path, query, body, started)
            return

        if MODE == "local":
            self._handle_local(entry, method, path, query, body, started)
            return

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

    def _handle_forward(self, entry, method, path, query, body, started):
        """Forward to the real cloud, capture the genuine response, relay it."""
        try:
            status, resp_headers, resp_body = _forward(
                method, path, query, self.headers.items(), body)
        except Exception as exc:  # network/TLS/upstream error
            print(f"[shim] forward failed for {path}: {exc}", flush=True)
            entry["mode"] = "forward-error"
            entry["error"] = repr(exc)
            _log_capture(entry)
            # Fall back to the canned envelope so the robot never hangs.
            payload, _ = _lookup(path)
            out = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                   if isinstance(payload, (dict, list))
                   else str(payload).encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            try:
                self.wfile.write(out)
            except BrokenPipeError:
                pass
            return

        entry["mode"] = "forward"
        entry["upstream"] = UPSTREAM
        entry["upstream_status"] = status
        entry["upstream_headers"] = resp_headers
        entry["upstream_body_len"] = len(resp_body)
        try:
            entry["upstream_body"] = resp_body.decode("utf-8")
        except UnicodeDecodeError:
            entry["upstream_body"] = resp_body.hex()
        _log_capture(entry)

        # Relay the genuine response back to the robot, byte-for-byte.
        self.send_response(status)
        skip = {"connection", "keep-alive", "transfer-encoding", "date",
                "server"}
        for k, v in resp_headers.items():
            if k.lower() in skip:
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        try:
            self.wfile.write(resp_body)
        except BrokenPipeError:
            pass
        entry["elapsed_ms"] = int((time.time() - started) * 1000)

    def _handle_local(self, entry, method, path, query, body, started):
        """Answer with the local AI handler (local.py)."""
        try:
            import local
            status, resp_headers, resp_body = local.handle(
                method, path, query, self.headers.items(), body)
        except Exception as exc:
            print(f"[shim] local handler error for {path}: {exc}", flush=True)
            status, resp_headers, resp_body = 200, \
                {"Content-Type": "application/json; charset=utf-8"}, \
                json.dumps(_default_envelope()).encode("utf-8")
        entry["mode"] = "local"
        entry["response_code"] = status
        _log(entry)
        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        try:
            self.wfile.write(resp_body)
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

