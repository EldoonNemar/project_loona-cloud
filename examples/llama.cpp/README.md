# llama.cpp — local chat (her brain)

Run an OpenAI-compatible LLM server with [llama.cpp](https://github.com/ggml-org/llama.cpp)
and point `chat_agent` at it. This is the default "brain" for general
conversation, and it runs on modest hardware (a Raspberry Pi 5 with a 3B Q4
model is comfortable).

## 1. Get a GGUF model

Download a quantized model (`.gguf`) into `./models/`. Any of these work:

- Hugging Face (e.g. `Qwen/Qwen2.5-3B-Instruct-GGUF`, `lmstudio-community/...`)
- `ollama pull qwen2.5:3b` then copy from `~/.ollama/models`

A 3B `Q4_K_M` is a good Pi default. Bigger = smarter but slower.

## 2. Run the server

**Docker (easiest):**
```bash
cd examples/llama.cpp
docker compose up -d
```
This starts `llama-server` on `:8080` with the model from `./models/`.

**Or natively:**
```bash
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf \
             --host 0.0.0.0 --port 8080
```

Verify: `curl http://localhost:8080/v1/chat/completions -d '{...}'` or just
`curl http://localhost:8080/health`.

## 3. Point the shadow at it

Copy `tools.json` to `shim/data/tools.json` (or merge the `chat_agent` entry).
Replace `HOST` with the machine running llama.cpp.

That's it — Loona's general conversation now runs on your hardware.

## Notes

- `model` in `tools.json` is passed through; llama.cpp uses the loaded model
  regardless, so any name works.
- `reasoning_effort: "none"` keeps reasoning models from chain-of-thinking
  (adds latency). Leave it for non-reasoning models.
- To swap models, change the file in `./models/` and restart the container.
