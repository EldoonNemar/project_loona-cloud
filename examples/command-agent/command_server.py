#!/usr/bin/env python3
"""Example command agent: an OpenAI-compatible endpoint for robot-command
intents (e.g. charging settings).

The shadow routes a `command`-type agent here. This service is where you'd
dispatch the actual robot command via the `loona` SDK. For now it answers with
a speakable confirmation so the pipeline is testable end to end.

Stdlib-only.

    python3 command_server.py [port]     # default 8090
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply(content, model="command-agent"):
    return {
        "id": "chatcmpl-cmd",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content,
                        "tool_calls": []},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            req = {}

        # ---- Hook: dispatch the real robot command here ------------------
        # e.g.
        #   from loona import LoonaClient
        #   robot = LoonaClient(...)
        #   robot.connect()
        #   robot.set_auto_recharge(...)   # whatever the intent maps to
        #   robot.close()
        # ------------------------------------------------------------------
        user_text = ""
        for msg in reversed(req.get("messages", [])):
            if msg.get("role") == "user":
                c = msg.get("content")
                user_text = c if isinstance(c, str) else json.dumps(c)
                break
        print(f"[command-agent] intent text={user_text!r}", flush=True)
        content = "Sure, I can adjust my charging settings."

        body = json.dumps(reply(content)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"[command-agent] serving /v1/chat/completions on 0.0.0.0:{port}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
