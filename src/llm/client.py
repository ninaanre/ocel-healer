import json
import os
import urllib.error
import urllib.request
from typing import Any


MODEL = os.getenv("OCEL_LLM_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MIN_CONFIDENCE = float(os.getenv("OCEL_LLM_MIN_CONFIDENCE", "0.5"))


SYSTEM_PROMPT = ( """
    You are a domain expert for object-centric event data in the OCEL2.0 format. 
    You receive one data-quality violation plus a small slice of local context 
    (the affected object's attributes, the events touching it, neighboring 
    objects, and a few peers of the same type). Reason from that and particular
    context knowledge of the underlying data, its contents and structure. However, 
    make assumptions clear in your replies and never invent ids, attributes, types, etc. that
    don't appear in the context. Always include a `confidence`
    in [0,1]. Reply with ONLY a JSON object — no prose, no markdown fences.
    """
)


def ollama_ready() -> tuple[bool, list[str]]:
    """Return (reachable, available_models). Never raises."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False, []
    return True, [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def call_ollama(user_prompt: str) -> dict[str, Any]:
    """One JSON-mode call to Ollama. Returns the parsed dict."""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        # Strip fenced wrappers just in case the model adds them.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text.strip())
