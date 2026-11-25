# Image inference — "draw me X" (her artist)

When Loona hears "draw me a crayon", the intent router picks `draw_agent`
(type `image`) and the shadow calls your image endpoint, then returns the image
as `b64_json` for her to display.

Two ways to run it:

## Option A — Dummy server (test the pipeline, no GPU)

`dummy_image_server.py` is a stdlib-only server that speaks the
AUTOMATIC1111 `/sdapi/v1/txt2img` API and returns a real PNG. The color is
derived from the prompt, so different prompts give different images — proving
the prompt actually flows through. Use this to confirm the full
intent → draw_agent → image → display path works before investing in a GPU.

```bash
python3 examples/image-inference/dummy_image_server.py 7861
# point draw_agent at: http://HOST:7861/sdapi/v1/txt2img
```

## Option B — Real Stable Diffusion (AUTOMATIC1111)

For actual art, run [AUTOMATIC1111](https://github.com/AUTOMATIC1111/stable-diffusion-webui)
and point `draw_agent` at its API:

```json
"draw_agent": { "type": "image", "url": "http://HOST:7860/sdapi/v1/txt2img" }
```

The shadow already speaks the `/sdapi/v1/txt2img` shape (sends
`{prompt, steps, width, height}`, reads `images[0]` base64). Any OpenAI-style
`/v1/images/generations` endpoint also works.

## Point the shadow at it

Copy `tools.json` to `shim/data/tools.json` (or merge the `draw_agent` entry).
Replace `HOST` with the image server's machine.
