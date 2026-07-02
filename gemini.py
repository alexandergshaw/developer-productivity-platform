"""Gemini proxy for the workbench chat view (stdlib-only, like everything here).

The browser POSTs ``{"messages": [{"role": ..., "content": ...}], "model": ...}``
to ``/api/chat``; this module forwards it to Google's generateContent endpoint.
The API key stays server-side: set ``GEMINI_API_KEY`` in the environment
(get one at https://aistudio.google.com/apikey) and restart the server.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"
ALLOWED_MODELS = ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite")

USAGE = {
    "service": "detcode-chat",
    "usage": 'POST a JSON body like {"messages": [{"role": "user", "content": "hi"}]}',
    "models": list(ALLOWED_MODELS),
}


def chat(request: dict) -> dict:
    """One chat completion. Returns {"ok": True, "output": text, "model": id}
    or {"ok": False, "error": reason} — never raises."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return {
            "ok": False,
            "error": "GEMINI_API_KEY is not set — export it and restart the server "
            "(get a key at https://aistudio.google.com/apikey)",
        }
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "error": "messages must be a non-empty list"}
    model = request.get("model") or DEFAULT_MODEL
    if model not in ALLOWED_MODELS:
        return {"ok": False, "error": f"unknown model {model!r} (allowed: {', '.join(ALLOWED_MODELS)})"}

    system_parts: list[dict] = []
    contents: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        role = message.get("role")
        if role == "system":
            system_parts.append({"text": text})
        else:
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
            )
    if not contents:
        return {"ok": False, "error": "no user messages to send"}

    payload: dict = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    req = urllib.request.Request(
        f"{API_ROOT}/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = json.loads(err.read().decode("utf-8"))["error"]["message"]
        except Exception:
            pass
        return {"ok": False, "error": f"Gemini API error {err.code}: {detail or err.reason}"}
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return {"ok": False, "error": f"could not reach the Gemini API: {err}"}

    try:
        candidate = data["candidates"][0]
        text = "".join(
            part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
        )
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": "unexpected response shape from the Gemini API"}
    if not text:
        reason = candidate.get("finishReason", "no text returned")
        return {"ok": False, "error": f"Gemini returned an empty response ({reason})"}
    return {"ok": True, "output": text, "model": model}
