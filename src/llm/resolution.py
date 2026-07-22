import json
from typing import Callable, Iterable

from src.detection.error_detection import _connect
from src.llm import actions
from src.llm.client import LLMOutputInvalid, call_llm, call_task
from src.llm.personas import BASE_PERSONA, compose as compose_persona
from src.llm.tasks import get_task
from src.llm.tasks._base import DetectionResult, DetectionTask, IssueTask


def _build_messages(task: IssueTask, ctx: dict) -> tuple[str, str]:
    """Return (system, user). System = base + family persona; user = task prompt + context."""
    system = compose_persona(task.family) if task.family else BASE_PERSONA
    user = (
        task.prompt
        + "\n\nContext:\n```json\n"
        + json.dumps(ctx, default=str, indent=2)
        + "\n```"
    )
    return system, user


def _call_task_or_legacy(task: IssueTask, ctx: dict) -> dict:
    """Route through `call_task` (validated + retry) if the task declares an
    OutputModel; otherwise fall back to the legacy unvalidated `call_llm`.

    Once every task is migrated, drop this branch and always use `call_task`.
    """
    system, user = _build_messages(task, ctx)
    if task.OutputModel is not None:
        return call_task(system, user, task.OutputModel)
    return call_llm(user, system_prompt=system)


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
    try:
        payload = _call_task_or_legacy(task, ctx)
    except LLMOutputInvalid as e:
        # Give the task a chance to fire a deterministic branch (e.g.
        # DuplicateObjectsOnIds pure same-type case) that consumes only
        # the detector row. from_task_result is confidence-gated, so we
        # inject a max-confidence stub payload plus the LLM error on the
        # rationale — the deterministic branch inside parse_payload
        # ignores the payload contents anyway. If parse_payload has no
        # deterministic branch it returns decline/unrouted (rendered as
        # a noop with the LLM error surfaced).
        try:
            stub = {"confidence": 1.0,
                    "rationale": f"LLM output invalid: {e}"}
            return actions.from_task_result(task, row, stub)
        except Exception:  # noqa: BLE001 -- parse_payload itself failed
            return actions.malformed_output_noop(task, row, str(e))
    return actions.from_task_result(task, row, payload)


def detect_with_llm(
    issue_key: str, row: dict, sqlite_path: str, *, use_hints: bool = True
) -> DetectionResult:
    """Ask the LLM to *detect* whether `row` is a real violation.

    Mirrors `suggest_repair` but stops at the LLM's verdict instead of
    constructing an ActionResult. Only valid for DetectionTask issue keys --
    raises ValueError otherwise so call sites stay self-documenting.

    `use_hints=False` builds the context without exploration hints — used by
    the evaluation to measure the hints' effect, same as `suggest_repair`."""
    task = get_task(issue_key)
    if task is None:
        raise ValueError(f"No LLM task registered for {issue_key!r}.")
    if not isinstance(task, DetectionTask):
        raise ValueError(
            f"Task {issue_key!r} is a resolution task, not a detection task. "
            f"Use suggest_repair instead."
        )
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row, use_hints=use_hints)
    try:
        payload = _call_task_or_legacy(task, ctx)
    except LLMOutputInvalid as e:
        return DetectionResult(
            flagged=False,
            rationale=f"LLM output invalid: {e}",
            confidence=0.0,
            suggested_value=None,
        )
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
