# Examples — plugging local AI into the shadow

Each subdirectory is a worked example for one **agent type** in
`shim/data/tools.json`. An agent is *enabled* when it has a non-empty `url`
(or is `builtin`); the intent router picks an agent per request and falls back
to `chat_agent` if the chosen one is disabled.

| Example | Agent | Type | What it gives Loona |
|---------|-------|------|---------------------|
| [`llama.cpp/`](llama.cpp/) | `chat_agent` | `chat` | General conversation via a local GGUF model (runs on a Pi) |
| [`image-inference/`](image-inference/) | `draw_agent` | `image` | "Draw me X" → a generated image she displays |
| [`command-agent/`](command-agent/) | `charge_setting_agent` | `command` | A hook for robot-command dispatch (e.g. charging settings) |

## How to use an example

1. Run the example's server (each README has the command / compose).
2. Copy its `tools.json` to `shim/data/tools.json` **or** merge just the
   relevant `agents` entry into your existing one.
3. Replace `HOST` with the machine running the server (the shadow's LAN IP if
   it's the same box).
4. The config hot-reloads — no shadow restart needed.

All three can run at once: point `chat_agent`, `draw_agent`, and
`charge_setting_agent` at their respective servers and Loona gets chat, drawing,
and command handling — all local.
