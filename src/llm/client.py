import json
import os
import urllib.error
import urllib.request
from typing import Any


MODEL = os.getenv("OCEL_LLM_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MIN_CONFIDENCE = float(os.getenv("OCEL_LLM_MIN_CONFIDENCE", "0.4"))


SYSTEM_PROMPT = (
    """
    You are a domain expert for object-centric event data in the OCEL2.0 format.

    Each turn you receive one data-quality violation plus a small slice of local
    context: the anchor object's attributes, up to 8 events touching it, and —
    depending on the task — peer objects of the same type or candidate ids to
    pick from. Reason from that evidence; 
    
    You may use two evidence sources:
    1. LOCAL_CONTEXT: values explicitly present in the provided OCEL context.
    2. DOMAIN_KNOWLEDGE: stable real-world knowledge about well-known entities,
   but only when the missing attribute is a factual, entity-specific attribute
   such as product weight, release year, manufacturer, or standard category.

    <rules>
    1. As a first step analyse an issues, if it is an issue that could be get from the log or from domain knowledge. For example for missing attribute analyse an object and the attribute which misses the value. If there is no information from LOCAL_CONTEXT, try to reason the right answer from common sense or common knowledge. If you still miss the information and ideas how to repair the issue log that you tried to find the knowledge in your LLM base.
    2. If the value comes from LOCAL_CONTEXT, set source = "local_context".
    3. If the value comes from DOMAIN_KNOWLEDGE, set source = "domain_knowledge".
    4. If using DOMAIN_KNOWLEDGE, explain that the value was not inferred from the log.
    5. For numeric factual attributes, include unit if available.
    6. JSON only. Respond with one JSON object — no prose, no markdown fences,
         no commentary outside the JSON.
    </rules>

    <confidence_scale>
      Always include `confidence` in [0,1]. Use this scale:
        0.9–1.0  Directly attested. The activity name names the value, the
                 qualifier names the type, or peers unanimously agree.
        0.6–0.8  Strong indirect signal. Most peers agree, the qualifier
                 strongly implies the type, or attribute correlations point
                 clearly to one option.
        0.3–0.5  Weak but non-trivial signal. Evidence eliminates some
                 alternatives even if it doesn't pin one down. Still a
                 defensible best guess — return it, don't return null.
        <0.3 Coin flip. For tasks that explicitly require a concrete guess, still return
     the best concrete candidate and set low confidence. Only return null if the
     task prompt allows null.
    </confidence_scale>

    <prefer_a_guess>
      If the task-specific prompt says that a concrete value is required, never return null.
    Return the best estimate with low confidence instead.
    </prefer_a_guess>
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
