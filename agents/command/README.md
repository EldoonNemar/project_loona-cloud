# Command agent — robot-command dispatch (the hook)

`charge_setting_agent` (type `command`) is the hook for intents that should
*do something to the robot* rather than just talk — e.g. "adjust my charging
settings", "go to my dock".

## Current behavior

The shadow routes a `command` agent to its `url` as an OpenAI-compatible
`/v1/chat/completions` endpoint. So today, a command agent is a small service
you run that (a) understands the intent and (b) can talk to the robot via the
`loona` SDK to issue the actual command. The example server below shows the
shape; it answers with a speakable confirmation and marks where you'd dispatch
the real command.

> This is intentionally a thin hook. Wiring a specific command (e.g. setting
> the auto-recharge threshold) means calling the matching `loona` SDK method
> from inside this service. The intent router already separates "command"
> intents from plain chat, so you get a clean place to add robot actions.

## Run the example

**Docker (easiest):**
```bash
cd agents/command
docker compose up -d
```

**Or natively:**
```bash
python3 agents/command/command_server.py 8090
```

Then point `charge_setting_agent` at `http://HOST:8090/v1/chat/completions`
(the `tools.json` here already does).

## Point the shadow at it

Copy `tools.json` to `shim/data/tools.json` (or merge the
`charge_setting_agent` entry). Replace `HOST` with the command server's machine.
