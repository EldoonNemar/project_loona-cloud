#!/usr/bin/env python3
"""Point Loona at the loona-shadow cloud stand-in, then watch the capture.

One-shot flow the moment the robot is up:
  1. Discover her (mDNS -> UDP multicast -> subnet scan), or use --host.
  2. Connect over mTLS and verify she answers (battery).
  3. Send command 405 (switch_server) to aim her cloud traffic at the shadow.
  4. Optionally tail shim/data/requests.jsonl as she starts calling.

Usage:
    python tools/point_at_shadow.py                     # auto-discover + switch
    python tools/point_at_shadow.py --host 192.168.1.10 # skip discovery
    python tools/point_at_shadow.py --sn M004KY...      # target one robot
    python tools/point_at_shadow.py --no-switch --tail  # just connect + watch
"""

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loona import LoonaClient, discovery as D  # noqa: E402

# Default shadow hostname. Override with --host, or set SHADOW_HOST in the
# environment. Point this at whatever host your shadow shim serves on.
SHADOW_DEFAULT = os.environ.get("SHADOW_HOST", "your-shadow.example.com")
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "loona-shadow", "shim", "data",
    "requests.jsonl")


def discover(args) -> None:
    if args.host:
        print(f"[*] using host {args.host}:{args.port}")
        return
    print("[*] discovering Loona "
          f"(sn={args.sn or 'any'}) ...")
    found = D.find_loona_any(args.sn, timeout=args.timeout,
                             use_multicast=not args.no_multicast,
                             use_subnet=not args.no_subnet)
    if not found:
        print("[!] no Loona found (tried mDNS, multicast, subnet scan)")
        sys.exit(1)
    print(f"[+] found Loona at {found.host}:{found.port} "
          f"(via {found.txt.get('via', 'mDNS')})")
    args.host = found.host
    args.port = found.port or args.port


def connect_and_verify(args) -> LoonaClient:
    print(f"[*] connecting to {args.host}:{args.port} (mTLS) ...")
    robot = LoonaClient(args.host, args.port)
    auth = {}

    def on_frame(cmd, body):
        if cmd == 1 and not auth:
            try:
                auth.update(json.loads(body.decode("utf-8", "replace")))
            except Exception:
                auth["raw"] = body[:80]

    robot.add_listener(on_frame)
    robot.connect()
    print(f"[+] connected. battery={robot.battery}")

    # give the robot a moment to push its auth frame (command 1)
    deadline = time.monotonic() + 4.0
    while not auth and time.monotonic() < deadline:
        time.sleep(0.2)
    if auth:
        print(f"[i] auth frame: {json.dumps(auth)}")
    else:
        print("[i] no auth frame yet (ok if the app slot is free)")

    f = robot.ask_battery(timeout=6)
    if f is None:
        print("[!] connected but no battery reply -- robot may be busy")
    else:
        print(f"[+] robot is responding (battery reply: {f.body.hex()})")
    return robot


def switch(robot: LoonaClient, host: str) -> None:
    print(f"[*] switching cloud host -> {host} (command 405)")
    robot.switch_server(host)
    print("[+] switch command sent")


def tail(path: str, seconds: float) -> None:
    if not os.path.exists(path):
        print(f"[!] log not found at {path} (is loona-shadow running?)")
        return
    print(f"[*] tailing {path} for {seconds:.0f}s "
          f"(Ctrl-C to stop early) ...")
    seen = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            # skip existing lines, then follow
            fh.seek(0, os.SEEK_END)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                line = fh.readline()
                if not line:
                    time.sleep(0.3)
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen += 1
                body = d.get("body", "")
                if isinstance(body, str) and len(body) > 200:
                    body = body[:200] + "..."
                print(f"  {d.get('method')} {d.get('path')}  "
                      f"body={body}")
    except KeyboardInterrupt:
        pass
    print(f"[*] saw {seen} request(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="robot IP (skip discovery)")
    parser.add_argument("--port", type=int, default=9676)
    parser.add_argument("--sn", help="serial number to target")
    parser.add_argument("--shadow-host", default=SHADOW_DEFAULT)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--no-multicast", action="store_true")
    parser.add_argument("--no-subnet", action="store_true")
    parser.add_argument("--no-switch", action="store_true",
                        help="connect + verify only, do not send 405")
    parser.add_argument("--tail", type=float, default=0.0,
                        help="tail the shadow log for N seconds after switching")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    discover(args)
    robot = connect_and_verify(args)
    try:
        if not args.no_switch:
            switch(robot, args.shadow_host)
            time.sleep(1.0)
        if args.tail:
            tail(LOG_PATH, args.tail)
    finally:
        robot.close()
    print("[*] done")


if __name__ == "__main__":
    main()
