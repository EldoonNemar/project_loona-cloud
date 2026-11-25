#!/usr/bin/env python3
"""Point Loona's cloud connection at loona-shadow (or restore it).

Sends command 405 ({"host": "<host>"}) over the local mTLS socket. After this,
all of her cloud traffic (telemetry, GPT, TTS, update checks) goes to the
named host on port 8001.

Examples:
    # Point her at the shadow running on this box (default: own LAN IP):
    python tools/loona_shadow_point.py --host 192.168.1.10

    # Point her at a specific shadow host:
    python tools/loona_shadow_point.py --host 192.168.1.10 --shadow-host 192.168.1.11

    # Restore her to Keyi's real cloud (host from loona/config.json):
    python tools/loona_shadow_point.py --host 192.168.1.10 --restore
"""

import argparse
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loona import LoonaClient, find_loona, load_config  # noqa: E402


def _own_lan_ip() -> str:
    """Best-effort: the IP of this box on the LAN (what the robot can reach)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sn", help="robot serial number (for discovery)")
    ap.add_argument("--host", help="robot IP (skips discovery)")
    ap.add_argument("--port", type=int, default=9676)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--cert-dir", default=None)
    ap.add_argument("--shadow-host", default=None,
                    help="host to point her at (default: this box's LAN IP)")
    ap.add_argument("--restore", action="store_true",
                    help="switch back to the real cloud host from config.json")
    args = ap.parse_args()

    # Resolve the robot address.
    if args.host:
        robot_host, robot_port = args.host, args.port
    else:
        found = find_loona(args.sn, timeout=args.timeout)
        if not found:
            print("error: robot not found on LAN", file=sys.stderr)
            sys.exit(1)
        robot_host, robot_port = found.host, found.port

    # Resolve the target cloud host.
    if args.restore:
        cfg = load_config()
        target = cfg.get("host", "usloona.keyirobot.cn")
        action = "RESTORE to real cloud"
    else:
        target = args.shadow_host or _own_lan_ip()
        action = "POINT at shadow"

    print(f"connecting to robot at {robot_host}:{robot_port} ...")
    robot = LoonaClient(robot_host, robot_port, cert_dir=args.cert_dir)
    for attempt in range(6):
        try:
            robot.connect()
            break
        except Exception as e:
            print(f"  connect attempt {attempt + 1} failed: {e}")
            import time
            time.sleep(3)
    if not robot.connected:
        print("error: could not connect to robot", file=sys.stderr)
        sys.exit(1)

    print(f"authenticated: {robot.authenticated}  sn={getattr(robot, 'sn', None)}")
    robot.switch_server(target)
    print(f"{action}: sent command 405 -> {target!r}")
    print(f"Her cloud traffic now goes to {target}:8001 (TLS).")
    if not args.restore:
        print("Watch it:  tail -f loona-shadow/shim/data/requests.jsonl")
    robot.close()


if __name__ == "__main__":
    main()
