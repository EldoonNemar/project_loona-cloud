# Agents — plugging local AI into the shadow

Each subdirectory is a worked example for one **agent type** in
`shim/data/tools.json`. An agent is *enabled* when it has a non-empty `url`
(or is `builtin`); the intent router picks an agent per request and falls back
to `chat_agent` if the chosen one is disabled.

| Example | Agent | Type | What it gives Loona |
|---------|-------|------|---------------------|
| [`llama.cpp/`](llama.cpp/) | `chat_agent` | `chat` | General conversation via a local GGUF model (runs on a Pi) |
| [`image/`](image/) | `draw_agent` | `image` | "Draw me X" → a generated image she displays (stable-diffusion.cpp) |
| [`command/`](command/) | `charge_setting_agent` | `command` | A hook for robot-command dispatch (e.g. charging settings) |

Each example has a `docker-compose.yml` (or a `Dockerfile`) so it runs with one
`docker compose up -d`, plus a `tools.json` you copy/merge into
`shim/data/tools.json`.

## How to use an example

1. Run the example's server (each README has the command / compose).
2. Copy its `tools.json` to `shim/data/tools.json` **or** merge just the
   relevant `agents` entry into your existing one. To merge, take the one
   agent object from the example's `tools.json` and drop it into your
   `shim/data/tools.json` under `"agents"`:
   ```jsonc
   // agents/image/tools.json has:
   { "agents": { "draw_agent": { "type":"image",
         "url":"http://HOST:1234/sdapi/v1/txt2img" } } }
   // merge into your shim/data/tools.json:
   { "agents": {
       "chat_agent": { ... },          // keep what you have
       "draw_agent": { "type":"image", // add the new one
         "url":"http://HOST:1234/sdapi/v1/txt2img" }
   } }
   ```
3. Replace `HOST` with the machine running the server (the shadow's LAN IP if
   it's the same box).
4. The config hot-reloads — no shadow restart needed.

All three can run at once: point `chat_agent`, `draw_agent`, and
`charge_setting_agent` at their respective servers and Loona gets chat, drawing,
and command handling — all local.
