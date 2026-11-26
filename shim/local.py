#!/usr/bin/env python3
"""loona-shadow local handler: replace Keyi's cloud with local, pluggable AI.

This module answers the robot's cloud endpoints *locally* (no Keyi, no AWS):

  /inference/stt            -> faster-whisper            (her ears)
  /inference/resolveintent  -> {"intent":"GPT",...}      (proceed to GPT)
  /inference/intent         -> LLM router (wired like the cloud) (picks agent)
  /v1/chat/completions      -> your OpenAI LLM           (her brain / GPT proxy)
  /v2/key/gpt               -> fake key + proxy = us     (redirects GPT to us)
  /v2/key/microsoft         -> fake Azure key            (ASR/TTS creds)
  /check/loona              -> "no update"               (freeze firmware)
  /datapoint/loona,/ping    -> ack                       (telemetry sink)

Everything is driven by ``tools.json`` (hot-reloaded). An agent is *enabled*
when it has a non-empty ``url`` (or is ``builtin``). When the router picks an
agent that is disabled, we fall back to ``chat_agent`` if it is enabled,
otherwise a canned "I can't do that right now" reply. The ``visual_tool``
requirement injects the live camera frame into the LLM request.

Stdlib-only for the HTTP/LLM/image paths. Whisper is a lazy, optional import:
if it isn't installed, STT returns an empty transcription (and logs once).
"""
import base64
import io
import json
import os
import re
import threading
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_PATH = os.environ.get(
    "TOOLS_PATH", os.path.join(_HERE, "data", "tools.json"))

_tools_lock = threading.Lock()
_tools_cache = {"mtime": 0.0, "cfg": None}

# Last intent-routing decision, so the following GPT call knows whether to
# attach the camera frame. Cleared after one use / after a short TTL.
_last_route = {"tool": "none_tool", "agent": "chat_agent", "ts": 0.0}
_route_lock = threading.Lock()

_whisper_model = None
_whisper_lock = threading.Lock()
_stt_warned = False


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _default_tools():
    return {
        "proxy_host": "127.0.0.1:8001",
        # Shared default LLM ("the brain"). Chat agents and the intent router
        # inherit this when their own url is empty. Point it at the local
        # llama.cpp docker agent, or any OpenAI-compatible endpoint.
        "llm": {"url": "", "model": "", "api_key": "",
                "reasoning_effort": "none"},
        # The intent router. Empty url -> inherits the shared "llm". Set a url
        # to route with a smaller/external LLM instead of the shared brain.
        "intent": {"url": "", "model": "", "api_key": ""},
        "stt": {"enabled": True, "engine": "whisper", "model": "base",
                "language": "en"},
        "agents": {
            "chat_agent": {"type": "chat", "url": "", "api_key": "",
                           "model": "", "reasoning_effort": "none",
                           "system": "You are Loona, a small friendly pet "
                                     "robot. Keep replies short and speakable."},
            "draw_agent": {"type": "image", "url": ""},
            "assist_agent": {"type": "chat", "url": ""},
            "charge_setting_agent": {"type": "command", "url": ""},
            "close_agent": {"type": "builtin"},
        },
        "visual": {"enabled": True,
                   "frame_path": "/tmp/loona_frames/latest.jpg"},
    }


def load_tools():
    """Load tools.json (hot-reload by mtime). Returns the config dict."""
    global _tools_cache
    with _tools_lock:
        try:
            mtime = os.path.getmtime(TOOLS_PATH)
        except OSError:
            mtime = 0.0
        if mtime != _tools_cache["mtime"] or _tools_cache["cfg"] is None:
            cfg = _default_tools()
            try:
                with open(TOOLS_PATH, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for k, v in raw.items():
                    if k == "agents" and isinstance(v, dict):
                        merged = {a: dict(d) for a, d in
                                  cfg["agents"].items()}
                        for a, d in v.items():
                            if a in merged and isinstance(d, dict):
                                merged[a].update(d)
                            else:
                                merged[a] = d
                        cfg["agents"] = merged
                    elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
            except Exception as exc:
                print(f"[local] failed to load tools.json: {exc}", flush=True)
            _tools_cache.update(mtime=mtime, cfg=cfg)
        return _tools_cache["cfg"]


def _effective_llm(cfg, agent_cfg):
    """Resolve the LLM endpoint for a chat agent / the intent router.

    The agent's own url/model/api_key/reasoning_effort win; any that are empty
    are filled from the shared "llm" block. Only chat agents and the intent
    router (no "type") inherit the shared brain; image/command agents need
    their own endpoint. Other fields (system, ...) always come from the agent.
    """
    eff = dict(agent_cfg or {})
    shared = cfg.get("llm") or {}
    if eff.get("type") in (None, "", "chat"):
        for k in ("url", "model", "api_key", "reasoning_effort"):
            if not eff.get(k) and shared.get(k):
                eff[k] = shared[k]
    return eff


def _agent_enabled(cfg, agent_cfg):
    """An agent is enabled if builtin, or has a resolvable url (its own, or the
    shared "llm" for chat agents)."""
    if not agent_cfg:
        return False
    if agent_cfg.get("type") == "builtin":
        return True
    if agent_cfg.get("type") == "chat":
        return bool(_effective_llm(cfg, agent_cfg).get("url"))
    return bool(agent_cfg.get("url"))


# --------------------------------------------------------------------------- #
# Outbound HTTP (stdlib)
# --------------------------------------------------------------------------- #
def _http_json(url, payload, api_key=None, timeout=90):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _openai_chat(eff, req, timeout=90):
    """Call an OpenAI-compatible chat endpoint. `req` is the full request dict
    (messages, max_tokens, ...); the model is overridden with the effective
    one. Returns the raw response."""
    payload = dict(req)
    if eff.get("model"):
        payload["model"] = eff["model"]
    if eff.get("reasoning_effort"):
        payload.setdefault("reasoning_effort", eff["reasoning_effort"])
    return _http_json(eff["url"], payload, eff.get("api_key"), timeout)


def _image_gen(agent_cfg, prompt, timeout=180):
    """Call an image-gen endpoint. Returns (bytes, content_type).

    Supports AUTOMATIC1111 /sdapi/v1/txt2img and OpenAI /v1/images/generations.
    """
    url = agent_cfg["url"].rstrip("/")
    if "/sdapi/" in url or url.endswith("txt2img"):
        payload = {"prompt": prompt, "steps": 20, "width": 512, "height": 512}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
        b64 = out.get("images", [None])[0]
        return base64.b64decode(b64), "image/png"
    payload = {"model": agent_cfg.get("model", ""), "prompt": prompt,
               "n": 1, "size": "512x512"}
    out = _http_json(url, payload, agent_cfg.get("api_key"), timeout)
    item = out.get("data", [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "image/png"
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", "image/png")
    raise ValueError("no image in generation response")


# --------------------------------------------------------------------------- #
# STT (Whisper, lazy)
# --------------------------------------------------------------------------- #
def _extract_wav(body, content_type):
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        return None
    boundary = ("--" + m.group(1).strip().strip('"')).encode()
    for part in body.split(boundary):
        if b"audio/wav" in part or b'name="audio"' in part:
            if b"\r\n\r\n" in part:
                content = part.split(b"\r\n\r\n", 1)[1]
                content = content.rstrip(b"\r\n")
                if content.endswith(b"--"):
                    content = content[:-2].rstrip(b"\r\n")
                return content
    return None


def _get_whisper(model_name):
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print(f"[local] loading whisper model '{model_name}' "
                  f"(first STT call)", flush=True)
            _whisper_model = WhisperModel(model_name, device="cpu",
                                          compute_type="int8")
    return _whisper_model


def _stt(body, content_type, stt_cfg):
    empty = {"combinedPhrases": [{"text": ""}]}
    if not stt_cfg.get("enabled", True):
        return empty
    wav = _extract_wav(body, content_type)
    if not wav:
        return empty
    try:
        import wave
        import numpy as np
        with wave.open(io.BytesIO(wav)) as w:
            ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
        else:
            return empty
        if ch > 1:
            samples = samples.reshape(-1, ch).mean(axis=1)
        model = _get_whisper(stt_cfg.get("model", "base"))
        segments, _ = model.transcribe(
            samples, language=stt_cfg.get("language", "en"), beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"combinedPhrases": [{"text": text}]}
    except ImportError:
        global _stt_warned
        if not _stt_warned:
            print("[local] faster-whisper not installed; STT returns empty. "
                  "pip install faster-whisper", flush=True)
            _stt_warned = True
        return empty
    except Exception as exc:
        print(f"[local] STT error: {exc}", flush=True)
        return empty



# --------------------------------------------------------------------------- #
# Intent routing (LLM router, wired like the cloud)
# --------------------------------------------------------------------------- #
_KNOWN_AGENTS = {"chat_agent", "draw_agent", "assist_agent",
                 "charge_setting_agent", "close_agent"}


def _parse_route(content):
    """Extract (agent, tool) from the LLM's JSON content. Tolerates extra text
    around the JSON object. Falls back to (chat_agent, none_tool)."""
    agent, tool = "chat_agent", "none_tool"
    m = re.search(r"\{.*\}", content or "", re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            a = obj.get("Best_agent", "")
            t = obj.get("Tool_requirement", "")
            if a in _KNOWN_AGENTS:
                agent = a
            if t in ("visual_tool", "none_tool"):
                tool = t
        except Exception:
            pass
    return agent, tool


def _pick_agent(cfg, wanted):
    """Return (agent_name, agent_cfg) to actually use, with fallback."""
    agents = cfg["agents"]
    if wanted in agents and _agent_enabled(cfg, agents[wanted]):
        return wanted, agents[wanted]
    chat = agents.get("chat_agent")
    if chat and _agent_enabled(cfg, chat):
        return "chat_agent", chat
    return None, None


# --------------------------------------------------------------------------- #
# Visual frame injection
# --------------------------------------------------------------------------- #
def _maybe_inject_frame(messages, cfg):
    with _route_lock:
        need = _last_route["tool"] == "visual_tool"
        fresh = (time.time() - _last_route["ts"]) < 30
        if need and fresh:
            _last_route["tool"] = "none_tool"  # consume
    if not (need and fresh):
        return messages
    vis = cfg.get("visual", {})
    if not vis.get("enabled", True):
        return messages
    path = vis.get("frame_path", "/tmp/loona_frames/latest.jpg")
    try:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
    except OSError:
        print(f"[local] visual frame not found at {path}", flush=True)
        return messages
    for msg in reversed(messages):
        if msg.get("role") == "user":
            if isinstance(msg.get("content"), str):
                msg["content"] = [
                    {"type": "text", "text": msg["content"]},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]
            elif isinstance(msg.get("content"), list):
                msg["content"].append(
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            return messages
    messages.insert(0, {
        "role": "user",
        "content": [
            {"type": "text", "text": "Here is what I am looking at:"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    })
    return messages


# --------------------------------------------------------------------------- #
# Endpoint helpers
# --------------------------------------------------------------------------- #
def _json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8")


def _ack():
    return {"code": 0, "result": 0, "type": 0, "msg": "success", "data": {}}


def _openai_reply(content, model="local"):
    return {"id": "chatcmpl-local", "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content,
                                     "tool_calls": []},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0}}



# --------------------------------------------------------------------------- #
# Endpoint handlers
# --------------------------------------------------------------------------- #
def _handle_stt(body, headers, cfg):
    ctype = headers.get("Content-Type", "")
    out, ctype_out = _json_bytes(_stt(body, ctype, cfg.get("stt", {})))
    return 200, {"Content-Type": ctype_out}, out


def _handle_resolveintent(body, headers, cfg):
    out, ctype = _json_bytes({"code": 0, "data": {"intent": "GPT",
                                                  "dis": 0.999},
                              "msg": "success"})
    return 200, {"Content-Type": ctype}, out


def _handle_intent(body, headers, cfg):
    """The agent router, wired like the cloud. The robot sends the full routing
    prompt (an OpenAI chat completion with the "task allocation" system prompt +
    the user's text); we forward it to the intent LLM and return the LLM's
    answer. We also parse Best_agent/Tool_requirement so the following
    /v1/chat/completions knows which agent to run and whether to attach the
    camera frame."""
    try:
        req = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        req = {}
    eff = _effective_llm(cfg, cfg.get("intent", {}))

    def _fallback():
        with _route_lock:
            _last_route.update(tool="none_tool", agent="chat_agent",
                               ts=time.time())
        content = json.dumps({"Best_agent": "chat_agent",
                              "Tool_requirement": "none_tool"})
        out, ctype = _json_bytes(_openai_reply(content, model="local-router"))
        return 200, {"Content-Type": ctype}, out

    if not eff.get("url"):
        return _fallback()  # no LLM configured for intent
    try:
        resp = _openai_chat(eff, req)
        content = resp["choices"][0]["message"]["content"]
        agent, tool = _parse_route(content)
        with _route_lock:
            _last_route.update(tool=tool, agent=agent, ts=time.time())
        out, ctype = _json_bytes(resp)  # return the LLM's raw answer
        return 200, {"Content-Type": ctype}, out
    except Exception as exc:
        print(f"[local] intent LLM failed: {exc}", flush=True)
        return _fallback()


def _handle_chat(body, headers, cfg):
    """The GPT proxy (OpenAI /v1/chat/completions). Route to the chosen agent's
    LLM, injecting the camera frame if the router asked for visual_tool."""
    try:
        req = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        req = {}
    messages = req.get("messages", [])

    with _route_lock:
        wanted = _last_route["agent"]
    agent_name, agent_cfg = _pick_agent(cfg, wanted)

    # image generation (draw_agent)
    if agent_name == "draw_agent" and agent_cfg.get("type") == "image":
        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                prompt = msg["content"]
                break
        try:
            img, _ = _image_gen(agent_cfg, prompt)
            resp = {"created": int(time.time()),
                    "data": [{"b64_json": base64.b64encode(img).decode(),
                              "revised_prompt": prompt}]}
            out, ctype = _json_bytes(resp)
            return 200, {"Content-Type": ctype}, out
        except Exception as exc:
            print(f"[local] image gen failed: {exc}", flush=True)
            agent_name, agent_cfg = _pick_agent(cfg, "chat_agent")

    if agent_cfg is None:
        out, ctype = _json_bytes(_openai_reply("I can't do that right now."))
        return 200, {"Content-Type": ctype}, out

    system = agent_cfg.get("system")
    if system:
        if messages and messages[0].get("role") == "system":
            messages = list(messages)
            messages[0] = {"role": "system", "content": system}
        else:
            messages = [{"role": "system", "content": system}] + list(messages)

    messages = _maybe_inject_frame(messages, cfg)

    try:
        req2 = dict(req)
        req2["messages"] = messages
        resp = _openai_chat(_effective_llm(cfg, agent_cfg), req2)
        out, ctype = _json_bytes(resp)
        return 200, {"Content-Type": ctype}, out
    except Exception as exc:
        print(f"[local] chat LLM failed ({agent_name}): {exc}", flush=True)
        out, ctype = _json_bytes(_openai_reply(
            "Sorry, my brain is offline right now."))
        return 200, {"Content-Type": ctype}, out



def _handle_gpt_key(body, headers, cfg):
    """Issue a fake OpenAI key and point the proxy at ourselves so the robot's
    GPT traffic comes back to this shim."""
    proxy = cfg.get("proxy_host", "127.0.0.1:8001")
    resp = {"code": 0, "data": {
        "apikey": "sk-local-loona-shadow",
        "organization": "org-local",
        "gptModel": "local",
        "code": 1,
        "proxyInfo": [{"ping": f"https://{proxy}/ping",
                       "proxy": f"https://{proxy}"}],
    }, "msg": "success"}
    out, ctype = _json_bytes(resp)
    return 200, {"Content-Type": ctype}, out


def _handle_ms_key(body, headers, cfg):
    """Fake Azure Cognitive key. ASR is handled by our /inference/stt."""
    proxy = cfg.get("proxy_host", "127.0.0.1:8001")
    uri = f"https://{proxy}/inference/stt"
    resp = {"code": 0, "data": [
        {"modelName": "tts", "info": {"key": "local", "uri": uri,
                                      "zoneIdentifier": "local"}},
        {"modelName": "asr", "info": {"key": "local", "uri": uri,
                                      "zoneIdentifier": "local"}},
    ], "msg": "success"}
    out, ctype = _json_bytes(resp)
    return 200, {"Content-Type": ctype}, out


def _handle_check_loona(body, headers, cfg):
    """Firmware check -> report the current version as newest (no update)."""
    try:
        req = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        req = {}
    ver = req.get("robot_version", "1.5.22.MII")
    resp = {"code": 0, "robot_data": {
        "robot_version": ver, "low_ios_version": "1.0.0",
        "low_google_version": "1.0.0", "is_alive": 1,
        "robot_desc": {"title_en": "", "title_zh": ""},
    }}
    out, ctype = _json_bytes(resp)
    return 200, {"Content-Type": ctype}, out


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def handle(method, path, query, headers, body):
    """Main entry. Returns (status, headers_dict, body_bytes)."""
    cfg = load_tools()
    p = path.split("?", 1)[0]

    if p == "/inference/stt":
        return _handle_stt(body, headers, cfg)
    if p == "/inference/resolveintent":
        return _handle_resolveintent(body, headers, cfg)
    if p == "/inference/intent":
        return _handle_intent(body, headers, cfg)
    if p.endswith("/chat/completions"):
        return _handle_chat(body, headers, cfg)
    if p == "/v2/key/gpt":
        return _handle_gpt_key(body, headers, cfg)
    if p == "/v2/key/microsoft":
        return _handle_ms_key(body, headers, cfg)
    if p == "/check/loona":
        return _handle_check_loona(body, headers, cfg)
    if p in ("/datapoint/loona", "/datapoint/loonacount", "/ping",
             "/switch/hellollona", "/aiJoyinside/key", "/tools/ip2region"):
        out, ctype = _json_bytes(_ack())
        return 200, {"Content-Type": ctype}, out

    out, ctype = _json_bytes(_ack())
    return 200, {"Content-Type": ctype}, out

