# loona-cloud

A local stand-in for Keyi's cloud API. Point Loona at it (via the firmware's
switchable cloud host, command `405`), and every request she makes is captured
to `shim/data/requests.jsonl` while we answer with canned JSON from
`shim/data/responses.json`. This is how we reverse-engineer her cloud protocol
end-to-end — telemetry, GPT inference, TTS, update checks — **with nothing
leaving your house.**

> **Companion repo:** the robot SDK + control tools live in
> [`loona`](https://github.com/<you>/loona) (mTLS socket client, discovery,
> WebRTC camera/mic, CLI). The pointing tools in `tools/` here need that
> package installed (`pip install /path/to/project_loona`).

## Primary mode: direct LAN (no cloud, no tunnel)

Verified against the real robot: she connects to a host:port over **TLS** and
**accepts a self-signed certificate** (no pinning, no client cert). So the
shadow runs straight on your LAN and she talks to it directly. No Cloudflare,
no AWS, no Keyi.

```
robot ──TLS:8001 (self-signed cert, accepted)──▶ loona-cloud (this box)
```

### Run it

```bash
./run_local.sh                 # TLS on 0.0.0.0:8001 (foreground)
# or in the background:
nohup ./run_local.sh > /tmp/shadow.log 2>&1 &
```

### Point Loona at it

With the robot connected over the local mTLS socket (needs the `loona` SDK
installed):

```bash
.venv/bin/python tools/loona_shadow_point.py            # uses config.json host
# or explicitly:
.venv/bin/python tools/loona_shadow_point.py --shadow-host 192.168.1.11
```

That sends command `405` (`{"host":"<shadow-ip>"}`). She will now send all her
cloud traffic to this box on port 8001.

### Watch her traffic

```bash
tail -f shim/data/requests.jsonl | python3 -m json.tool --compact
```

### Test without the robot

```bash
python3 test_mock_robot.py     # replays her real telemetry POST over TLS
```

## What it does

- **Logs everything**: method, path, query, headers, body, remote addr,
  timestamp → `shim/data/requests.jsonl` (one JSON object per line).
- **Answers canned JSON**: looked up by exact path, then `prefix*`, then the
  `_default` envelope `{code:0,result:0,type:0,msg:"success",data:{}}`.
  Edit `responses.json` live — it hot-reloads on mtime change.
- **Never crashes** on a request; unknown paths get the default envelope.

## What we know about her cloud protocol (from captures)

- **Transport**: TLS 1.3 to `<host>:8001`. Self-signed server cert accepted.
  No client cert presented.
- **Auth header**: `X-Loona-Token` — an HS256 JWT *she* generates:
  `{SN, iss:"keyitech", aud:["Loona"], exp, nbf}` (~7-day expiry). We accept it
  as-is; no need to forge.
- **Version header**: `X-Total-Version: <firmware>.MII` (e.g. `1.5.22.MII`).
- **Telemetry**: `POST /datapoint/loona` — a JSON array of usage events
  (`swhich_mode`, `battery`, `v25_face_mode`, `model_time`, `tcp_disconnect`,
  `hallucination`, ...). See `docs/captures/` for raw captures.
- **GPT model**: telemetry `model_time` reports `type:"gpt-5-mini"`. The real
  GPT inference endpoint is still to be captured (trigger a live GPT chat while
  pointed at the shadow).
- **LAN advertisement**: UDP broadcast to `192.168.1.255:10001` every ~5s.

## Files

```
loona-cloud/
├── run_local.sh           # start the shadow on the LAN (TLS:8001)
├── test_mock_robot.py     # replay her real telemetry POST (no robot needed)
├── certs/
│   ├── shadow.crt         # self-signed server cert (she accepts it)
│   └── shadow.key
├── shim/
│   ├── server.py          # stdlib-only TLS/HTTP server
│   └── data/
│       ├── responses.json # canned responses (edit me, hot-reloads)
│       └── requests.jsonl # the capture (grows as she calls)
├── Dockerfile             # (optional) container build
├── docker-compose.yml     # (optional) shim + cloudflared tunnel
└── .env                   # (optional) TUNNEL_TOKEN=
```

## Optional: tunnel mode (only if she can't reach your LAN)

The direct-LAN mode above is the default and needs no tunnel. If you ever need
to reach the shadow from off-LAN (e.g. the robot is on a different network), you
can front the plain-HTTP shim with a Cloudflare tunnel:

```bash
# put TUNNEL_TOKEN in .env, then:
sudo docker compose up -d --build
sudo docker compose logs -f shim
```

Then point her at the public hostname instead of the LAN IP. Note this routes
her (small JSON) traffic through Cloudflare — avoid it if you want strictly
local.

## Iterating

As her behavior reveals what each endpoint expects, add real-shaped responses
to `responses.json`. E.g. to tell her "no firmware update":

```json
"/api/v1/protocol/appapiloona": {
  "code": 0, "result": 0, "type": 0, "msg": "success",
  "data": { "has_update": false }
}
```

(Exact field names come from the capture — fill them in as you learn them.)

## Notes / caveats

- **Cert trust: resolved.** She accepts a self-signed server cert (verified
  against the real robot over TLS 1.3). No pinning, no client cert required.
  This is what makes the fully-local shadow possible.
- **Keep firmware updates OFF** while pointed at the shadow — answer "no
  update" from `responses.json` so she doesn't try to pull a firmware image from
  us. (We don't want to be serving firmware.)
- **Switching back**: to restore her to Keyi's real cloud, send command 405 with
  the regional host again, e.g. `r.switch_server("usloona.keyirobot.cn")`.
- **JWT**: she regenerates `X-Loona-Token` periodically; we never validate it,
  so it expiring is a non-issue for the shadow.
