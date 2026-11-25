#!/usr/bin/env python3
"""Mock Loona robot: replays her real cloud requests against loona-shadow.

Used to verify the shadow end-to-end without the physical robot (e.g. while
she's charging). Connects over TLS exactly like the robot does (self-signed
cert accepted, no client cert) and sends the captured telemetry POST.

Usage:
  python3 test_mock_robot.py [--host 127.0.0.1] [--port 8001]
"""
import argparse
import json
import ssl
import urllib.request

# The exact telemetry batch captured from the real robot (docs/captures/).
TELEMETRY = [
    {"event_id": "v25_face_mode", "order_id": 900,
     "property": {"col0": "0", "col1": "5", "col2": "0", "col3": "1",
                  "col4": "3", "col5": "8", "col6": "0"},
     "sn": "M004/KY001XXXXX_XXXXXXX_XXXX",
     "timestamp": 1795566435191, "version": "1.5.22.MII"},
    {"event_id": "swhich_mode", "order_id": 901,
     "property": {"current_mode": 200, "last_mode_id": 2, "last_mode_time": 68},
     "sn": "M004/KY001XXXXX_XXXXXXX_XXXX",
     "timestamp": 1795566435223, "version": "1.5.22.MII"},
    {"event_id": "model_time", "order_id": 902,
     "property": {"time": 680, "type": "gpt-5-mini"},
     "sn": "M004/KY001XXXXX_XXXXXXX_XXXX",
     "timestamp": 1795566435909, "version": "1.5.22.MII"},
    {"event_id": "battery", "order_id": 907,
     "property": {"battery": 54},
     "sn": "M004/KY001XXXXX_XXXXXXX_XXXX",
     "timestamp": 1795566476555, "version": "1.5.22.MII"},
]

# A token shaped like the robot's (HS256, SN/iss/aud/exp/nbf). Not verified
# by the shadow, so a placeholder serial and signature are fine for the mock.
FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJTTiI6Ik0wMDQvS1kwMDEwMDAwMF8wMDAwMDBfMDAwMCIsImlzcyI6ImtleWl0ZWNoIiwiYXVkIjpbIkxvb25hIl0sImV4cCI6MTc5NjE2NzcwNSwibmJmIjoxNzk1NTYyOTA1fQ."
    "mocksignature"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()

    # The robot accepts our self-signed cert; the mock does the same.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"https://{args.host}:{args.port}/datapoint/loona"
    body = json.dumps(TELEMETRY, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Host", f"{args.host}:{args.port}")
    req.add_header("Accept", "*/*")
    req.add_header("X-total-version", "1.5.22.MII")
    req.add_header("X-loona-token", FAKE_JWT)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    print(f"POST {url}  ({len(body)} bytes)")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        print(f"HTTP {resp.status}  {resp.headers.get('Content-Type')}")
        data = resp.read().decode("utf-8", "replace")
        print(f"response: {data}")
    print("MOCK ROBOT TEST OK")


if __name__ == "__main__":
    main()
