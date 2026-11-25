FROM python:3.11-slim
WORKDIR /app

# openssl is needed on first run to auto-generate the self-signed TLS cert
# (the robot accepts it -- no pinning). Nothing else is required: the shim is
# stdlib-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

# server.py dispatches by mode; local.py is the local-AI handler (imported as
# `local` when MODE=local). Both must sit in the same dir.
COPY shim/server.py /app/server.py
COPY shim/local.py /app/local.py

# data dir holds tools.json + responses.json (editable) and the captures.
# Mount ./shim/data here so your config and captures persist on the host.
VOLUME ["/app/data"]

ENV DATA_DIR=/app/data \
    PORT=8001 \
    TLS=1 \
    MODE=local

# TLS on 8001: the robot connects straight to this box over the LAN (command
# 405 points her here). No tunnel, no cloud -- nothing leaves the house.
EXPOSE 8001
CMD ["python", "/app/server.py"]
