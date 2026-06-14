import sqlite3
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, TYPE_CHECKING

from src.detection.error_detection import _column_info, _object_type_tables
from src.llm.sql_utils import quote, table_for_type

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

    Subclasses set `issue_key` and `PROMPT`, implement the appropriate parse
    method, and optionally override `extend_context`, `anchor`, and
    `suppressed_target`. Importing the module self-registers via
    `__init_subclass__`.
    """

    issue_key: ClassVar[str] = ""
    PROMPT: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only register concrete tasks (those that set an issue_key). The
        # intermediate base classes ResolutionTask / DetectionTask leave
        # issue_key="" and skip registration.
        if getattr(cls, "issue_key", ""):
            REGISTRY[cls.issue_key] = cls()

    @property
    def prompt(self) -> str:
        return textwrap.dedent(self.PROMPT).strip()

    def build_context(self, conn: sqlite3.Connection, row: dict) -> dict:
        """Default context: violation + candidate_types + (anchor object + events)."""
        ctx: dict[str, Any] = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]
        anchor_id, anchor_type = self.anchor(row)
        if anchor_id:
            self._attach_anchor(conn, ctx, anchor_id, anchor_type)
            self._attach_events(conn, ctx, anchor_id)
        self.extend_context(conn, ctx, row)
        return ctx

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        """The (id, type) the local-context block is built around."""
        return (
            row.get("ocel_id") or row.get("ocel_object_id") or row.get("ocel_source_id"),
            row.get("object_type") or row.get("source_type"),
        )

    def extend_context(self, conn: sqlite3.Connection, ctx: dict, row: dict) -> None:
        """Hook for task-specific context (peers, candidates, duplicates)."""
        return

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

    def _attach_peers(self, conn: sqlite3.Connection, ctx: dict, row: dict) -> None:
        """Attach `peer_objects`: up to 5 other rows of the same type, full
        attribute rows. Used by missing_attribute_value / incorrect_attribute_datatype
        so the LLM can learn typical value shape/format."""
        anchor_id, anchor_type = self.anchor(row)
        if not anchor_id:
            return
        table = table_for_type(conn, anchor_type)
        if not table:
            return
        cols = [c for c, _ in _column_info(conn, table)]
        if not cols:
            return
        quoted = ", ".join(quote(c) for c in cols)
        peers = conn.execute(
            f"SELECT {quoted} FROM {quote(table)} WHERE ocel_id != ? LIMIT 5",
            (anchor_id,),
        ).fetchall()
        ctx["peer_objects"] = [dict(zip(cols, p)) for p in peers]


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
