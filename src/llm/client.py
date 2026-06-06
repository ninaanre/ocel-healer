import json
import os
import urllib.error
import urllib.request
from typing import Any


MODEL = os.getenv("OCEL_LLM_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MIN_CONFIDENCE = float(os.getenv("OCEL_LLM_MIN_CONFIDENCE", "0.5"))


SYSTEM_PROMPT = (
    """
    You are a domain expert for object-centric event data in the OCEL2.0 format.
    Each turn you receive one data-quality violation plus a small slice of local
    context: the anchor object's attributes, up to 8 events touching it, and —
    depending on the task — peer objects of the same type or candidate ids to
    pick from. Reason from that evidence; bring general OCEL2.0 knowledge only
    to interpret it.

    <rules>
      1. Verbatim only. Every id, type, attribute name, or candidate value you
         return must appear in the provided context. If none fits, return null
         for that field — never invent.
      2. Rationale must justify. State the specific evidence you used (peer
         values, activity names, qualifiers, attribute correlations). When you
         return null, state the specific reason no candidate works.
      3. JSON only. Respond with one JSON object — no prose, no markdown fences,
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
        <0.3     Coin flip. Return null for the inferred field.
    </confidence_scale>

    <prefer_a_guess>
      Returning null leaves the issue unrepaired. Prefer a low-confidence guess
      over null whenever the evidence eliminates at least one alternative.
      Only return null when no candidate is more plausible than any other.
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
