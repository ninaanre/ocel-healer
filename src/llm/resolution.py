import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

from src.detection.error_detection import _connect
from src.llm import actions
from src.llm.client import LLMOutputInvalid, call_llm, call_task
from src.llm.dataset_hints import load_cached as _load_hints_cached
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


def suggest_repair(issue_key: str, row: dict, sqlite_path: str) -> dict:
    """Ask the LLM how to repair `row`. Returns an action dict (kind='noop' if unsure)."""
    task = get_task(issue_key)
    if task is None:
        return actions.unknown_issue_noop(issue_key)
    hints = _load_hints_cached(sqlite_path)
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row, hints=hints)
    try:
        payload = _call_task_or_legacy(task, ctx)
    except LLMOutputInvalid as e:
        return actions.malformed_output_noop(task, row, str(e))
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
    hints = _load_hints_cached(sqlite_path)
    with _connect(sqlite_path) as conn:
        ctx = task.build_context(conn, row, hints=hints)
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
    """Run detect_with_llm over an iterable of candidate rows, concurrently.

    Concurrency is bounded by env var OCEL_LLM_MAX_WORKERS (default 8). LLM
    semantics match the sequential path exactly -- each row still goes
    through one `detect_with_llm` call. Per-row exceptions surface as
    `flagged=False, rationale="detection failed: ..."` so a single bad row
    doesn't sink the whole sweep.

    Return list is in input order. `on_progress(i, total, row, verdict)`
    fires in *completion* order (i is monotonic 1..total; rows arrive as
    workers finish), serialized under an internal lock so callbacks may
    safely mutate shared state.
    """
    rows_list = list(rows)
    total = len(rows_list)
    if total == 0:
        return []

    max_workers = max(1, int(os.getenv("OCEL_LLM_MAX_WORKERS", "8")))
    max_workers = min(max_workers, total)

    results: list[tuple[dict, DetectionResult] | None] = [None] * total
    progress_lock = threading.Lock()
    completed = [0]  # boxed so the closure can mutate it under the lock

    def _worker(row: dict) -> DetectionResult:
        try:
            return detect_with_llm(issue_key, row, sqlite_path)
        except Exception as e:  # noqa: BLE001 -- surface any failure as a noop verdict
            return DetectionResult(
                flagged=False,
                rationale=f"detection failed: {e}",
                confidence=0.0,
                suggested_value=None,
            )

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ocel-llm") as pool:
        future_to_idx = {
            pool.submit(_worker, row): idx for idx, row in enumerate(rows_list)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            row = rows_list[idx]
            verdict = fut.result()  # never raises -- _worker catches everything
            results[idx] = (row, verdict)
            if on_progress is not None:
                with progress_lock:
                    completed[0] += 1
                    on_progress(completed[0], total, row, verdict)

    return [r for r in results]  # every slot filled; type is now list[tuple[...]]
