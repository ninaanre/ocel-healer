import json

from src.detection.error_detection import _connect
from src.llm import actions
from src.llm.client import MODEL, call_ollama, ollama_ready
from src.llm.actions import apply_repair
from src.llm.tasks import get_task


def suggest_repair(issue_key: str, row: dict, sqlite_path: str) -> dict: # TODO: maybe move this function somewehre else
    """Ask the LLM how to repair `row`. Returns an action dict (kind='noop' if unsure)."""
    task = get_task(issue_key)
    if task is None:
        return actions.unknown_issue_noop(issue_key)
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row)
    user_prompt = (
        task.prompt
        + "\n\nContext:\n```json\n"
        + json.dumps(ctx, default=str, indent=2)
        + "\n```"
    )
    payload = call_ollama(user_prompt)
    return actions.from_task_result(task, row, payload)


__all__ = ["MODEL", "ollama_ready", "suggest_repair", "apply_repair"]
