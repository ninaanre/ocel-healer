import json
import os
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ValidationError


MODEL = os.getenv("OCEL_LLM_MODEL", "qwen2.5:7b")
LLM_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MIN_CONFIDENCE = float(os.getenv("OCEL_LLM_MIN_CONFIDENCE", "0.4"))


class LLMOutputInvalid(Exception):
    """Raised when the LLM's reply cannot be parsed into the task's OutputModel
    after one retry. `raw_replies` holds the raw text returned by each attempt
    for debugging; `last_error` is the final validation error."""

    def __init__(self, raw_replies: list[str], last_error: str):
        self.raw_replies = raw_replies
        self.last_error = last_error
        super().__init__(
            f"LLM output invalid after {len(raw_replies)} attempt(s): {last_error}"
        )


_active_model: str = MODEL


def set_active_model(name: str) -> None:
    """Override the model used for all subsequent LLM calls (dashboard use)."""
    global _active_model
    _active_model = name


def llm_ready() -> tuple[bool, list[str]]:
    """Return (reachable, available_models). Never raises."""
    try:
        with urllib.request.urlopen(f"{LLM_HOST}/api/tags", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False, []
    return True, [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def _post_chat(messages: list[dict[str, str]]) -> str:
    """Send one chat-completions request; return the raw assistant text."""
    body = json.dumps({
        "model": _active_model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LLM_HOST}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"].strip()


def _strip_fences(text: str) -> str:
    """Some models still wrap the JSON in ``` fences despite response_format."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def call_task(
    system_prompt: str,
    user_prompt: str,
    output_model: type[BaseModel],
) -> dict[str, Any]:
    """One LLM call validated against `output_model`, with one retry-on-invalid.

    The first turn sends `(system, user)`. On JSON-decode or pydantic
    validation failure, the second turn appends the raw reply and a
    reminder naming the error; the model gets one chance to fix it.
    Raises `LLMOutputInvalid` if the second turn also fails.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw_replies: list[str] = []

    for attempt in range(2):
        raw = _post_chat(messages)
        raw_replies.append(raw)
        cleaned = _strip_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            error = f"reply is not valid JSON: {e.msg}"
        else:
            try:
                validated = output_model.model_validate(parsed)
                # Return a plain dict so `from_task_result` keeps its
                # payload.get(...) shape without touching pydantic.
                return validated.model_dump()
            except ValidationError as e:
                # Compact error string — one line per bad field.
                error = "; ".join(
                    f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                    for err in e.errors()
                )
        if attempt == 0:
            # Second turn: replay the bad reply + naming the error.
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "Your previous reply did not match the required <output_schema>: "
                    f"{error}. Reply again with exactly one JSON object matching "
                    "the schema — no prose, no fences."
                ),
            })

    raise LLMOutputInvalid(raw_replies, error)


# Backwards-compatible: some older call sites (if any remain) call
# `call_llm(user_prompt)`. Kept as a thin wrapper that skips validation.
def call_llm(user_prompt: str, system_prompt: str = "") -> dict[str, Any]:
    """Legacy: one JSON-mode call without validation. Prefer `call_task`."""
    raw = _post_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    return json.loads(_strip_fences(raw))
