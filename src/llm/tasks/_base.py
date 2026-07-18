import inspect
import sqlite3
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, TYPE_CHECKING

from pydantic import BaseModel

from src.detection.error_detection import _column_info
from src.llm.dataset_hints import DatasetHints
from src.llm.sampling import sample_peers
from src.llm.sql_utils import quote, table_for_type
from src.llm.tasks._template import render_task_prompt

if TYPE_CHECKING:
    from src.llm.actions import ActionResult


REGISTRY: dict[str, "IssueTask"] = {}


@dataclass
class DetectionResult:
    """LLM verdict for a *detection* task.

    `flagged=True` means the LLM judged the candidate as a real violation.
    `suggested_value` is the LLM's proposed correct value when applicable
    (e.g. the inferred correct ocel_type) -- it travels with the flag so
    callers can either show it or discard it. `flagged=False` means
    "looks fine / unsure"; the dashboard drops these silently.
    """
    flagged: bool
    rationale: str = ""
    confidence: float = 0.0
    suggested_value: Any = None


class IssueTask(ABC):
    """Base class for one task per issue type.

    Two flavours subclass this:
      - ResolutionTask: the violation is already known (deterministic detector
        found it); the LLM only proposes a fix. parse_payload returns an
        ActionResult.
      - DetectionTask: the LLM is the one deciding whether a violation exists.
        parse_detection returns a DetectionResult; parse_payload bridges to
        an ActionResult so the existing suggest_repair / apply_repair path
        still works for fix-time calls.

    Subclasses set `issue_key`, `family`, `OutputModel`, and the four
    section strings TASK / INPUTS / METHOD / EXAMPLES. They implement the
    appropriate parse method and optionally override `extend_context`,
    `anchor`, and `suppressed_target`. Importing the module self-registers
    via `__init_subclass__`.
    """

    issue_key: ClassVar[str] = ""

    # Task-family key used to select a family persona (see personas.py).
    # One of "type", "attribute", "relation", "duplicate", "temporal".
    family: ClassVar[str] = ""

    # Pydantic model the LLM's reply is validated against.
    OutputModel: ClassVar[type[BaseModel] | None] = None

    # Prompt sections. Each is a short string; the template splices them
    # into the seven-section skeleton (see tasks/_template.py).
    TASK: ClassVar[str] = ""
    INPUTS: ClassVar[str] = ""
    METHOD: ClassVar[str] = ""
    EXAMPLES: ClassVar[str] = ""

    # Legacy: pre-template tasks store the whole prompt in one string.
    # New tasks leave this empty and set TASK/INPUTS/METHOD/EXAMPLES instead.
    PROMPT: ClassVar[str] = ""

    min_confidence: ClassVar[float | None] = None  # None → use global MIN_CONFIDENCE

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only register concrete tasks (those that set an issue_key). The
        # intermediate base classes ResolutionTask / DetectionTask leave
        # issue_key="" and skip registration.
        if getattr(cls, "issue_key", ""):
            REGISTRY[cls.issue_key] = cls()

    @property
    def prompt(self) -> str:
        if self.OutputModel is not None and (self.TASK or self.METHOD):
            return render_task_prompt(
                task=self.TASK,
                inputs=self.INPUTS,
                method=self.METHOD,
                examples=self.EXAMPLES,
                output_model=self.OutputModel,
            )
        # Legacy path: fall back to the monolithic PROMPT string.
        if self.PROMPT:
            return textwrap.dedent(self.PROMPT).strip()
        raise NotImplementedError(
            f"{type(self).__name__} must set OutputModel + TASK/INPUTS/METHOD/EXAMPLES "
            "(new-style) or PROMPT (legacy) to render its prompt."
        )

    def build_context(
        self,
        conn: sqlite3.Connection,
        row: dict,
        *,
        hints: DatasetHints | None = None,
    ) -> dict:
        """Default context: violation + candidate_types + (anchor object + events)."""
        from src.detection.error_detection import _object_type_tables

        hints = hints or DatasetHints.empty()
        ctx: dict[str, Any] = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]
        anchor_id, anchor_type = self.anchor(row)
        if anchor_id:
            self._attach_anchor(conn, ctx, anchor_id, anchor_type)
            self._attach_events(conn, ctx, anchor_id)
        if hints.data_semantics:
            ctx["data_semantics"] = hints.data_semantics
        self._call_extend_context(conn, ctx, row, hints)
        return ctx

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        """The (id, type) the local-context block is built around."""
        return (
            row.get("ocel_id") or row.get("ocel_object_id") or row.get("ocel_source_id"),
            row.get("object_type") or row.get("source_type"),
        )

    def extend_context(
        self,
        conn: sqlite3.Connection,
        ctx: dict,
        row: dict,
        *,
        hints: DatasetHints,
    ) -> None:
        """Hook for task-specific context (peers, candidates, duplicates).

        New-style tasks accept `hints=` (keyword). Legacy tasks with the
        old signature `extend_context(conn, ctx, row)` still work — see
        `_call_extend_context` for the shim.
        """
        return

    def _call_extend_context(
        self,
        conn: sqlite3.Connection,
        ctx: dict,
        row: dict,
        hints: DatasetHints,
    ) -> None:
        """Bridge new-style (hints kw) and legacy (no hints) extend_context signatures."""
        sig = inspect.signature(self.extend_context)
        if "hints" in sig.parameters:
            self.extend_context(conn, ctx, row, hints=hints)
        else:
            self.extend_context(conn, ctx, row)

    @abstractmethod
    def parse_payload(self, row: dict, payload: dict) -> "ActionResult":
        """Translate the LLM JSON payload into an ActionResult."""

    def suppressed_target(self, row: dict) -> dict | None:
        """Routable override target when the LLM declines / gate trips. Default: None."""
        return None

    # --- private context helpers ------------------------------------------

    def _attach_anchor(
        self,
        conn: sqlite3.Connection,
        ctx: dict,
        anchor_id: str,
        anchor_type: str | None,
    ) -> None:
        attrs: dict[str, Any] = {}
        table = table_for_type(conn, anchor_type)
        if table:
            cols = [c for c, _ in _column_info(conn, table)]
            if cols:
                quoted = ", ".join(quote(c) for c in cols)
                row_data = conn.execute(
                    f"SELECT {quoted} FROM {quote(table)} WHERE ocel_id = ? LIMIT 1",
                    (anchor_id,),
                ).fetchone()
                if row_data:
                    attrs = dict(zip(cols, row_data))
        ctx["object"] = {"ocel_id": anchor_id, "ocel_type": anchor_type, "attributes": attrs}

    def _attach_events(self, conn: sqlite3.Connection, ctx: dict, anchor_id: str) -> None:
        ctx["events"] = [
            {"ocel_id": eid, "ocel_type": etype, "qualifier": qual}
            for eid, etype, qual in conn.execute(
                "SELECT e.ocel_id, e.ocel_type, eo.ocel_qualifier "
                "FROM event e JOIN event_object eo ON eo.ocel_event_id = e.ocel_id "
                "WHERE eo.ocel_object_id = ? LIMIT 8",
                (anchor_id,),
            ).fetchall()
        ]

    def _attach_peers(
        self,
        conn: sqlite3.Connection,
        ctx: dict,
        row: dict,
        *,
        target_col: str | None = None,
    ) -> None:
        """Attach `peer_objects`: up to 5 peers of the same type, full attribute rows.

        Delegates to `sampling.sample_peers` for deterministic + representative
        selection. When `target_col` is given, stratifies so at least half the
        peers have a non-null value in that column.
        """
        anchor_id, anchor_type = self.anchor(row)
        if not anchor_id:
            return
        peers = sample_peers(
            conn,
            row,
            anchor_id=anchor_id,
            anchor_type=anchor_type,
            k=5,
            target_col=target_col,
        )
        if peers:
            ctx["peer_objects"] = peers


# --- Intermediate base classes -------------------------------------------
# Concrete tasks subclass one of these two -- never IssueTask directly.

class ResolutionTask(IssueTask):
    """LLM-as-fix-proposer.

    The violation is already known (a deterministic Polars detector found
    it); the LLM's job is to suggest the corrected value. Subclasses
    implement `parse_payload` to return an `ActionResult`. Used for the 6
    classic detectors: missing_object_type, missing_attribute_value,
    incorrect_attribute_datatype, dangling_*, duplicate_*.
    """
    # No new methods -- the contract is exactly IssueTask.parse_payload.
    # Existing as a named subclass purely for clarity / isinstance checks.


class DetectionTask(IssueTask):
    """LLM-as-detector.

    The LLM judges *whether* a candidate is a real violation. Subclasses
    implement `parse_detection` returning a `DetectionResult`; the base
    class bridges that into `parse_payload` so the existing
    suggest_repair / apply_repair plumbing keeps working at fix-time.
    """

    @abstractmethod
    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        """Translate the LLM JSON payload into a DetectionResult."""

    # Bridge: when something asks the detection task for an ActionResult
    # (e.g. existing suggest_repair callers), reuse the LLM's verdict.
    # `flagged=True` + a suggested_value -> update; otherwise -> decline.
    def parse_payload(self, row: dict, payload: dict) -> "ActionResult":
        from src.llm.actions import ActionResult

        verdict = self.parse_detection(row, payload)
        if not verdict.flagged or verdict.suggested_value is None:
            reason = (verdict.rationale or "no reason provided").strip()
            return ActionResult.decline(
                f"LLM did not flag this row as a violation: {reason}"
            )
        return self.action_for_flag(row, verdict)

    def action_for_flag(self, row: dict, verdict: DetectionResult) -> "ActionResult":
        """How to write a confirmed flag back to the DB. Subclasses override
        when the bridge needs more than the default suppressed_target shape."""
        from src.llm.actions import ActionResult

        target = self.suppressed_target(row)
        if target is None:
            return ActionResult.unrouted(
                "DetectionTask flagged but has no routable target."
            )
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=verdict.suggested_value,
        )
