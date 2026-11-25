#!/usr/bin/env bash
# Run loona-shadow directly on the LAN (no tunnel, no cloud).
#
# The robot connects straight to this box over TLS on port 8001 (command 405
# points her here). Nothing leaves the house.
#
# Usage:
#   ./run_local.sh            # foreground, TLS on 0.0.0.0:8001
#   PORT=8001 ./run_local.sh  # override port
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
export TLS="${TLS:-1}"
export PORT="${PORT:-8001}"

echo "[run_local] starting loona-shadow (TLS=$TLS port=$PORT)"
echo "[run_local] point the robot here with:  r.switch_server('<this-box-ip>')"
exec "$PY" shim/server.py
