# loona-cloud

A self-hosted replacement for Keyi's cloud. Point Loona at it (via the
firmware's switchable cloud host, command `405`) and **her entire cloud —
speech-to-text, GPT, image generation, intent routing, telemetry, firmware
checks — runs on your own hardware, with nothing leaving your house.**

By default the shim runs in **local mode**: it answers her AI endpoints with
*your* LLM, *your* image generator, and local Whisper. Every request is also
captured to `shim/data/requests.jsonl` so you can watch exactly what she does.

> **Companion repo:** the robot SDK + control tools live in
> [`project_loona`](https://github.com/EldoonNemar/project_loona) (mTLS socket client, discovery,
> WebRTC camera/mic, CLI). The pointing tools in `tools/` here need that
> package installed (`pip install /path/to/project_loona`).

## Quickstart — get her talking in 5 minutes

The goal: Loona's voice and brain run on **your** hardware. This is the
shortest path to "she talks to me, locally."

**1. Pick your brain** — she needs an OpenAI-compatible
`/v1/chat/completions` endpoint. Use what you already have, or run one of ours:

| You have | Do this |
|----------|---------|
| a Raspberry Pi 5 (8GB) | run [`agents/llama.cpp`](agents/llama.cpp/) with Qwen2.5-3B |
| a GPU box | run [vLLM](https://docs.vllm.ai/) or [Ollama](https://ollama.com) |
| Ollama / LM Studio / llama.cpp already running | just point at it (table below) |
| a powerful LAN box running a model | just point at it (table below) |
| an OpenAI / other API key | point at it (works, but not local) |

Model size by hardware (Q4 GGUF — her replies are 1–2 sentences, so small is fine):

| Hardware | Model | Speed |
|----------|-------|-------|
| Pi 5, 8GB | Qwen2.5-3B | ~5–15 tok/s |
| Pi 4, 8GB | Qwen2.5-3B | ~3–8 tok/s |
| Pi 4/5, 4GB | Qwen2.5-1.5B | ~5–10 tok/s |
| RTX GPU | 7B–14B | fast |

**2. Configure** — copy the example config and set `llm.url`:

```bash
cd loona-cloud
cp shim/data/tools.json.example shim/data/tools.json
```

| Your endpoint | `llm.url` | `model` |
|---------------|-----------|---------|
| Ollama | `http://HOST:11434/v1/chat/completions` | `qwen2.5:3b` |
| LM Studio | `http://HOST:1234/v1/chat/completions` | any |
| llama.cpp | `http://HOST:8080/v1/chat/completions` | any |
| vLLM | `http://HOST:8000/v1/chat/completions` | your model |
| OpenAI | `https://api.openai.com/v1/chat/completions` | `gpt-4o-mini` (+ `api_key`) |

`HOST` = the machine running that server (its LAN IP, or `localhost` if it's
this box). That's the **only required field** — STT, intent routing, and the
chat agents all inherit from `llm`.

**3. Start the shadow, point her at it:**

```bash
pip install faster-whisper          # her ears (recommended)
./run_local.sh                      # TLS on :8001
# separate terminal:
.venv/bin/python tools/loona_shadow_point.py
```

**4. Talk to her** — say "Hello Loona" and ask her something. Watch it work:

```bash
tail -f shim/data/requests.jsonl | python3 -m json.tool --compact
```

Done — her brain is your LLM. To add **drawing** or **robot commands**, see
[Agents: add capabilities](#agents-add-capabilities).

## The three modes

| `MODE` | Behavior |
|--------|----------|
| `local` (default) | Answer her AI endpoints with local services (your LLM, Whisper, image gen). See [Local AI](#local-ai-her-brain-ears-and-eyes--all-yours). |
| `canned` | Return canned JSON from `responses.json`. For pure capture / protocol study. |
| `forward` | Set `UPSTREAM=https://usloona.keyirobot.cn:8001` to relay to the real Keyi cloud and capture the genuine request/response pairs. Used once to learn the schemas. |


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

## Local AI (her brain, ears, and eyes — all yours)

In `local` mode (the default) the shim replaces Keyi's AI with local,
**config-driven, URL-pluggable** backends. Everything is set in
`shim/data/tools.json` (hot-reloaded on edit — no restart).

### How a request flows

```
you: "draw me a crayon"
  1. POST /inference/stt          -> Whisper transcribes her mic
  2. POST /inference/resolveintent-> {"intent":"GPT"}
  3. POST /inference/intent       -> LLM router picks the agent + tool
  4. POST /v1/chat/completions    -> routed to that agent's URL (your LLM)
  5. (if visual_tool)             -> the live camera frame is injected
```

### The shared brain (`llm`) and the intent router

Two top-level blocks control the LLMs:

- **`llm`** — the shared default brain. Chat agents and the intent router
  **inherit it** when their own `url` is empty. Point it at the local
  llama.cpp docker agent (the recommended default) or any OpenAI endpoint.
- **`intent`** — the router. The robot sends the full routing prompt itself
  (an OpenAI chat completion); the shim forwards it to the intent LLM and
  returns the LLM's `{Best_agent, Tool_requirement}` decision — wired exactly
  like Keyi's cloud (they do the same with `llama31-chat`). Empty `url` →
  inherits `llm`. Set a `url` to route with a smaller/external LLM instead of
  the shared brain.

`resolveintent` is a coarse "proceed to GPT?" gate, not the agent+tool
router, so it stays a hardcoded `{intent:"GPT"}` (an LLM call there is wasted
latency).

### The agents (tools.json → `agents`)

Each agent maps to one of her intent categories. A chat agent is **enabled when
it has a resolvable `url`** — its own, or the shared `llm` it inherits (or it
is `builtin`). Image/command agents need their own `url`. If the router picks a
disabled agent, we fall back to `chat_agent`, then to a canned reply.

| agent | type | what it does |
|-------|------|--------------|
| `chat_agent` | `chat` | general conversation → your OpenAI-compatible LLM |
| `draw_agent` | `image` | "draw X" → an image-gen endpoint (AUTOMATIC1111 or OpenAI-style) |
| `assist_agent` | `chat` | "how do I / it's broken" → troubleshooting LLM |
| `charge_setting_agent` | `command` | "recharge settings" → (hook for a robot-command URL) |
| `close_agent` | `builtin` | "bye / good night" → handled locally |

See [`agents/`](agents/) for ready-to-run backends for each type:
**llama.cpp** (chat, Pi-friendly), **image inference** (draw, dummy + Stable
Diffusion), and the **command agent** (robot-command hook).

### The visual tool

When the router decides the request needs what she's looking at
(`visual_tool` — e.g. "what is that in front of me?"), the next GPT call
injects the live camera frame (from the `loona` repo's WebRTC capture at
`visual.frame_path`) into the LLM request as an image. Set `visual.enabled`
to `false` to disable.

### Enable / disable by URL

Because "enabled = has a URL", turning a capability on/off is just setting or
clearing its `url`:

```jsonc
"llm": { "url":"http://llama.cpp:8080/v1/chat/completions",
         "model":"qwen2.5-3b-instruct", "reasoning_effort":"none" },
"intent": { "url":"" },              // empty -> inherits llm (the router)
"agents": {
  "chat_agent": { "type":"chat", "url":"",
                  "system":"You are Loona, a small friendly pet robot..." },
  "draw_agent": { "type":"image", "url":"http://localhost:7860/sdapi/v1/txt2img" }
}
```

- **`llm`**: the shared brain. Any OpenAI-compatible `/v1/chat/completions`
  (Ollama, llama.cpp, vLLM, your own). `reasoning_effort:"none"` keeps
  reasoning models fast.
- **`intent`**: leave empty to route with the shared `llm`; set a `url` to
  route with a smaller/external LLM instead.
- **chat agents**: inherit `llm` when their `url` is empty; set a `url` to use
  a different model for that agent.
- **image**: AUTOMATIC1111 `/sdapi/v1/txt2img` or OpenAI
  `/v1/images/generations`.
- Leave a `url` empty to disable a non-chat agent (it falls back to chat).

### Speech-to-text (Whisper)

`/inference/stt` runs **faster-whisper** on CPU. `pip install faster-whisper`
(see `requirements.txt`). Without it, STT returns empty and the robot hears
nothing — everything else still works. Tune `stt.model` (`tiny`/`base`/`small`
— `base` is a good Pi default) and `stt.language`.

### Firmware freeze

`/check/loona` reports her current version as the newest, so she never phones
home for an OTA. You can't accidentally update her while she's on the shadow.

### Run it

```bash
pip install faster-whisper          # optional, for her ears
./run_local.sh                      # MODE=local by default, TLS on :8001
# then point her at it:
.venv/bin/python tools/loona_shadow_point.py
```

### Watch her traffic


```bash
tail -f shim/data/requests.jsonl | python3 -m json.tool --compact
```

### Test without the robot

```bash
python3 test_mock_robot.py     # replays her real telemetry POST over TLS
```

## Agents: add capabilities

The shadow ships with **ready-to-run backends** for each agent type in
[`agents/`](agents/). An agent is *enabled* when it has a URL; the intent
router picks the right one per request and falls back to chat if it's disabled.

| I want Loona to... | Agent | Run this |
|--------------------|-------|----------|
| Talk (general) | `chat_agent` | [`agents/llama.cpp`](agents/llama.cpp/) (or any LLM) |
| Draw pictures | `draw_agent` | [`agents/image`](agents/image/) (stable-diffusion.cpp) |
| Do things (settings, dock, ...) | `charge_setting_agent` | [`agents/command`](agents/command/) (robot-command hook) |

**To add one (e.g. drawing):**

1. Run the backend: `cd agents/image && docker compose up -d` (its README has
   the model download + exact commands).
2. Merge its `tools.json` entry into `shim/data/tools.json`:
   ```jsonc
   "draw_agent": { "type": "image",
                   "url": "http://HOST:1234/sdapi/v1/txt2img" }
   ```
3. Done — the config hot-reloads, no restart. Say "draw me a crayon."

Each `agents/*` README has the exact commands, model downloads, and a
`tools.json` to copy. All three can run at once. The detailed reference for
how routing/URLs work is in [Local AI](#local-ai-her-brain-ears-and-eyes--all-yours).

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
- **GPT model**: telemetry `model_time` reports `type:"gpt-5-mini"`. Keyi
  issues the robot a per-device OpenAI key via `POST /v2/key/gpt` (with a
  `proxyInfo` pointing at an OpenAI-compatible proxy), and the actual
  conversation goes through that proxy. In local mode we answer `/v2/key/gpt`
  with a fake key whose proxy points *back at us*, so the conversation is
  routed to your LLM instead.
- **STT**: `POST /inference/stt` — multipart WAV upload; response
  `{"combinedPhrases":[{"text":"..."}]}`. (Keyi also hands out an Azure
  Cognitive key via `/v2/key/microsoft`, but the robot uses the `/inference/stt`
  path.)
- **Intent routing**: `POST /inference/resolveintent` (`{"data","type"}` →
  `{"intent":"GPT","dis":0.999}`) then `POST /inference/intent` (an OpenAI
  chat completion that Keyi runs on `llama31-chat`) →
  `{"Best_agent":"chat_agent|draw_agent|...","Tool_requirement":"visual_tool|none_tool"}`.
- **LAN advertisement**: UDP broadcast to `192.168.1.255:10001` every ~5s.

## Files

```
loona-cloud/
├── run_local.sh           # start the shadow on the LAN (TLS:8001)
├── test_mock_robot.py     # replay her real telemetry POST (no robot needed)
├── certs/                 # gitignored; auto-generated / extracted
│   ├── shadow.crt         # self-signed server cert (auto-generated; she accepts it)
│   ├── shadow.key
│   └── app_client.*       # the app's mTLS client cert (forward mode only)
├── shim/
│   ├── server.py          # stdlib-only TLS/HTTP server (mode dispatch)
│   ├── local.py           # local AI handler (STT/intent/GPT/image, pluggable)
│   └── data/
│       ├── tools.json     # local AI config: agents, URLs, STT, visual (gitignored)
│       ├── tools.json.example
│       ├── responses.json # canned responses (canned mode; hot-reloads)
│       ├── requests.jsonl # the capture (grows as she calls)
│       └── captures.jsonl # forward-mode: real request+response pairs
├── agents/                # ready-to-run local AI backends
│   ├── llama.cpp/         # chat_agent: local GGUF LLM (Pi-friendly)
│   ├── image/             # draw_agent: stable-diffusion.cpp (Docker)
│   └── command/           # charge_setting_agent: robot-command hook
├── tests/
│   ├── test_draw_pipeline.py  # draw path, no model/GPU (uses image double)
│   └── dummy_image_server.py  # stdlib /sdapi/v1/txt2img test double
├── Dockerfile             # (optional) container build (TLS:8001)
└── docker-compose.yml     # (optional) containerized shim
```

## Run in Docker

The shim is fully containerized — TLS on `:8001`, self-signed cert
auto-generated on first run, `./shim/data` mounted so your config and captures
persist:

```bash
sudo docker compose up -d --build
sudo docker compose logs -f shim
```

Then point her at this box's LAN IP (`tools/loona_shadow_point.py`).

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
