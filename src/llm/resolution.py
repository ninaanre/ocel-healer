import json
from typing import Callable, Iterable

from src.detection.error_detection import _connect
from src.llm import actions
from src.llm.client import call_llm
from src.llm.tasks import get_task
from src.llm.tasks._base import DetectionResult, DetectionTask


def _build_user_prompt(task, ctx: dict) -> str:
    return (
        task.prompt
        + "\n\nContext:\n```json\n"
        + json.dumps(ctx, default=str, indent=2)
        + "\n```"
    )


def suggest_repair(
    issue_key: str, row: dict, sqlite_path: str, *, use_hints: bool = True
) -> dict:
    """Ask the LLM how to repair `row`. Returns an action dict (kind='noop' if unsure).

    `use_hints=False` builds the context without exploration hints — used by
    the evaluation to measure the hints' effect."""
    task = get_task(issue_key)
    if task is None:
        return actions.unknown_issue_noop(issue_key)
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row, use_hints=use_hints)
    payload = call_llm(_build_user_prompt(task, ctx))
    return actions.from_task_result(task, row, payload)


def detect_with_llm(issue_key: str, row: dict, sqlite_path: str) -> DetectionResult:
    """Ask the LLM to *detect* whether `row` is a real violation.

    Mirrors `suggest_repair` but stops at the LLM's verdict instead of
    constructing an ActionResult. Only valid for DetectionTask issue keys --
    raises ValueError otherwise so call sites stay self-documenting.
    """
    task = get_task(issue_key)
    if task is None:
        raise ValueError(f"No LLM task registered for {issue_key!r}.")
    if not isinstance(task, DetectionTask):
        raise ValueError(
            f"Task {issue_key!r} is a resolution task, not a detection task. "
            f"Use suggest_repair instead."
        )
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row)
    payload = call_llm(_build_user_prompt(task, ctx))
    return task.parse_detection(row, payload)


def detect_all_with_llm(
    issue_key: str,
    rows: Iterable[dict],
    sqlite_path: str,
    *,
    on_progress: Callable[[int, int, dict, DetectionResult], None] | None = None,
) -> list[tuple[dict, DetectionResult]]:
    """Run detect_with_llm over an iterable of candidate rows.

    Returns one (row, verdict) pair per candidate. `on_progress(i, total, row,
    verdict)` is called after each LLM round-trip so the dashboard can render
    a progress indicator. Per-row exceptions are swallowed and surfaced as
    `flagged=False, rationale="<error>"` so a single bad row doesn't sink the
    whole sweep.
    """
    rows_list = list(rows)
    total = len(rows_list)
    out: list[tuple[dict, DetectionResult]] = []
    for i, row in enumerate(rows_list, 1):
        try:
            verdict = detect_with_llm(issue_key, row, sqlite_path)
        except Exception as e:  # noqa: BLE001 -- surface any failure as a noop verdict
            verdict = DetectionResult(
                flagged=False,
                rationale=f"detection failed: {e}",
                confidence=0.0,
                suggested_value=None,
            )
        out.append((row, verdict))
        if on_progress is not None:
            on_progress(i, total, row, verdict)
    return out
