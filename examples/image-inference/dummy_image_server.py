#!/usr/bin/env python3
"""Dummy image server: speaks the AUTOMATIC1111 /sdapi/v1/txt2img API and
returns a real PNG, so you can test Loona's draw pipeline without a GPU.

The image color is derived from the prompt (SHA-256 -> RGB), so distinct
prompts produce distinct images — proof the prompt flows through end to end.

Stdlib-only.

    python3 dummy_image_server.py [port]     # default 7861
"""
import base64
import hashlib
import json
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_png(width, height, rgb):
    """Build a solid-color 8-bit RGB PNG (pure stdlib)."""
    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + bytes(rgb) * width          # filter byte 0 + pixels
    raw = row * height
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/sdapi/v1/txt2img":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            req = {}
        prompt = str(req.get("prompt", "loona"))
        h = hashlib.sha256(prompt.encode("utf-8")).digest()
        png = make_png(512, 512, (h[0], h[1], h[2]))
        print(f"[dummy-image] prompt={prompt!r} -> {len(png)} bytes", flush=True)
        self._send(200, {"images": [base64.b64encode(png).decode()]})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7861
    print(f"[dummy-image] serving /sdapi/v1/txt2img on 0.0.0.0:{port}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
