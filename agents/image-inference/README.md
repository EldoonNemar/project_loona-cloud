# Image inference — "draw me X" (her artist)

When Loona hears "draw me a crayon", the intent router picks `draw_agent`
(type `image`) and the shadow calls your image endpoint, then returns the image
as `b64_json` for her to display. The shadow speaks both the AUTOMATIC1111
`/sdapi/v1/txt2img` shape and the OpenAI `/v1/images/generations` shape, so any
backend that offers either works.

## Option A — Docker: stable-diffusion.cpp (real art, CPU or GPU)

[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) is a
lightweight pure-C++ Stable Diffusion runtime (SD1.x/SDXL/SD3/FLUX/Qwen-Image/
Z-Image/...). It ships a Docker image with a built-in server that exposes both
`/sdapi/v1/txt2img` and `/v1/images/generations` on port **1234**.

```bash
cd agents/image-inference
mkdir -p models/checkpoints
# download a checkpoint, e.g. Stable Diffusion 1.5:
curl -L -o models/checkpoints/v1-5-pruned-emaonly.safetensors \
  https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors
docker compose up -d
```

Then point `draw_agent` at `http://<this-host>:1234/sdapi/v1/txt2img` (the
`tools.json` here already does). The Web UI is at `http://<this-host>:1234/` if
you want to fiddle with models/recipes.

- **CPU** works out of the box (fine on a Pi, just slow — expect tens of
  seconds per image).
- **NVIDIA GPU**: build the CUDA image and uncomment the `deploy` block in
  `docker-compose.yml` (needs the NVIDIA Container Toolkit).

## Option B — AUTOMATIC1111 (alternative real SD)

If you already run [AUTOMATIC1111](https://github.com/AUTOMATIC1111/stable-diffusion-webui),
just point `draw_agent` at its API:

```json
"draw_agent": { "type": "image", "url": "http://HOST:7860/sdapi/v1/txt2img" }
```

## Point the shadow at it

Copy `tools.json` to `shim/data/tools.json` (or merge the `draw_agent` entry).
Replace `HOST` with the image server's machine.

## Testing the pipeline (no model needed)

To verify the draw path (intent → `draw_agent` → image endpoint → `b64_json`)
without downloading a model, run the test — it uses a tiny stdlib image
double:

```bash
python3 tests/test_draw_pipeline.py     # -> DRAW PIPELINE TEST OK
```

