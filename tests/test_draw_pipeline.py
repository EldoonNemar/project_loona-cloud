#!/usr/bin/env python3
"""Test the local draw pipeline end to end (no robot, no SD model, no GPU).

Exercises the real local.py handler the way the robot would drive it:

    "draw me a crayon"
      1. POST /inference/intent       -> router must pick draw_agent
      2. POST /v1/chat/completions    -> routed to draw_agent's image URL,
                                          returns the image as b64_json

The image backend is the dummy /sdapi/v1/txt2img server (tests/
dummy_image_server.py), which returns a real PNG whose color encodes the
prompt. We assert the response is a valid PNG and that the prompt reached it
(distinct prompts -> distinct images).

Usage:
    python3 tests/test_draw_pipeline.py
"""
import base64
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)          # tests/  -> dummy_image_server
sys.path.insert(0, os.path.join(REPO, "shim"))  # shim/ -> local

import dummy_image_server  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chat_body(text):
    return json.dumps({"messages": [{"role": "user", "content": text}]}).encode()


def main():
    # 1. Start the dummy image backend on a free port.
    httpd = dummy_image_server.start()
    port = httpd.server_address[1]
    img_url = f"http://127.0.0.1:{port}/sdapi/v1/txt2img"

    # 2. Point a throwaway tools.json at it (draw_agent enabled).
    tmp = tempfile.mkdtemp(prefix="loona_draw_test_")
    tools_path = os.path.join(tmp, "tools.json")
    with open(tools_path, "w", encoding="utf-8") as fh:
        json.dump({
            "proxy_host": "127.0.0.1:8001",
            "stt": {"enabled": False},
            "agents": {
                "chat_agent": {"type": "chat", "url": ""},
                "draw_agent": {"type": "image", "url": img_url},
                "assist_agent": {"type": "chat", "url": ""},
                "charge_setting_agent": {"type": "command", "url": ""},
                "close_agent": {"type": "builtin"},
            },
            "visual": {"enabled": False},
        }, fh)
    # local.py reads TOOLS_PATH at import time -> set it before importing.
    os.environ["TOOLS_PATH"] = tools_path
    import local  # noqa: E402

    # 3. Intent routing: "draw me a crayon" must pick draw_agent.
    status, _, body = local.handle("POST", "/inference/intent", "", {},
                                   _chat_body("draw me a crayon"))
    assert status == 200, f"intent status {status}"
    routed = json.loads(body)
    content = routed["choices"][0]["message"]["content"]
    decision = json.loads(content)
    print(f"  intent -> {decision}")
    assert decision["Best_agent"] == "draw_agent", \
        f"expected draw_agent, got {decision['Best_agent']}"

    # 4. GPT proxy: must call the image backend and return a valid PNG.
    status, _, body = local.handle("POST", "/v1/chat/completions", "", {},
                                   _chat_body("draw me a crayon"))
    assert status == 200, f"chat status {status}"
    gen = json.loads(body)
    b64 = gen["data"][0]["b64_json"]
    png = base64.b64decode(b64)
    print(f"  image -> {len(png)} bytes, sig ok: {png[:8] == PNG_SIG}")
    assert png[:8] == PNG_SIG, "returned image is not a valid PNG"

    # 5. Prompt actually flowed through: a different prompt -> different image.
    local.handle("POST", "/inference/intent", "", {}, _chat_body("draw a cat"))
    status, _, body = local.handle("POST", "/v1/chat/completions", "", {},
                                   _chat_body("draw a cat"))
    png2 = base64.b64decode(json.loads(body)["data"][0]["b64_json"])
    assert png2[:8] == PNG_SIG, "second image is not a valid PNG"
    assert png2 != png, "distinct prompts should yield distinct images"

    httpd.shutdown()
    print("DRAW PIPELINE TEST OK")


if __name__ == "__main__":
    main()
