import sqlite3
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TYPE_CHECKING

from pydantic import BaseModel

from src.detection.error_detection import _column_info
from src.exploration.db_profiler import schema_fingerprint
from src.exploration.hint_selector import select_hints
from src.exploration.report_store import load_guide, load_profile
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
    `anchor`, `select_hints` and `suppressed_target`. Importing the module
    self-registers via `__init_subclass__`.

    Dataset knowledge comes from the exploration artifacts (deterministic
    profile + optional LLM guide) and is attached as
    `ctx["exploration_hints"]` -- see `_attach_exploration_hints`. There is
    no hand-written hints file.
    """

    issue_key: ClassVar[str] = ""

    # Task-family key used to select a family persona (see personas.py).
    # One of "type", "attribute", "relation", "duplicate", "temporal".
    family: ClassVar[str] = ""

    # Which side of the OCEL log this task's anchor lives on: ``"object"``
    # (the default — every legacy task) or ``"event"``. Controls which
    # per-type table :meth:`_attach_anchor` and :meth:`_attach_peers` read,
    # and whether :meth:`_attach_events` fires at all (event anchors have
    # no "events touching them" — they ARE the event).
    kind: ClassVar[str] = "object"

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
        use_hints: bool = True,
    ) -> dict:
        """Default context: violation + candidate_types + (anchor object + events)
        + exploration hints when fresh artifacts exist for this database.

        `use_hints=False` skips the exploration lookup — the evaluation uses
        it to compare repair quality with and without exploration knowledge."""
        from src.detection.error_detection import _object_type_tables

        ctx: dict[str, Any] = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]
        anchor_id, anchor_type = self.anchor(row)
        if anchor_id:
            self._attach_anchor(conn, ctx, anchor_id, anchor_type)
            # Only object anchors have "events touching them". For event
            # anchors the concept is meaningless (the anchor is the event).
            if self.kind == "object":
                self._attach_events(conn, ctx, anchor_id)
        # Hints go in before extend_context so task-specific context can use them.
        if use_hints:
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        """The (id, type) the local-context block is built around."""
        return (
            row.get("ocel_id") or row.get("ocel_object_id") or row.get("ocel_source_id"),
            row.get("object_type") or row.get("source_type"),
        )

    def extend_context(self, conn: sqlite3.Connection, ctx: dict, row: dict) -> None:
        """Hook for task-specific context (peers, candidates, duplicates).

        Exploration knowledge, when available, is already attached as
        `ctx["exploration_hints"]` by the time this runs.
        """
        return

    def select_hints(self, profile: dict, guide: dict | None, row: dict) -> dict:
        """Which exploration slice this task wants in its context. Facts come
        from the profile, interpretations from the (optional) guide. Default:
        the generic row-driven selection (object type / attribute / qualifier).
        Tasks needing a different view override this."""
        return select_hints(profile, guide, row)

    def _attach_exploration_hints(
        self, conn: sqlite3.Connection, ctx: dict, row: dict
    ) -> None:
        """Attach `exploration_hints` when fresh exploration artifacts exist.

        The deterministic profile is required; the LLM guide is optional —
        facts-only hints are still valuable when guide sections failed.
        Best-effort by design: missing/stale artifacts or any error simply
        mean no hints — repair must work exactly as before without them.
        """
        try:
            db_file = next(
                (f for _, name, f in conn.execute("PRAGMA database_list") if name == "main"),
                None,
            )
            if not db_file:
                return
            # Artifacts live next to the data: <db_dir>/exploration/<db_stem>/
            base_dir = Path(db_file).parent / "exploration"
            profile = load_profile(db_file, base_dir)
            if profile is None:
                return
            current = schema_fingerprint(conn)
            if profile.get("schema_fingerprint") != current:
                return
            guide = load_guide(db_file, base_dir)
            if guide is not None and guide.get("source_fingerprint") != current:
                guide = None  # stale guide: keep the facts, drop the prose
            hints = self.select_hints(profile, guide, row)
            if hints:
                ctx["exploration_hints"] = hints
        except Exception:  # noqa: BLE001 — hints are optional, never fatal
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
        table = table_for_type(conn, anchor_type, kind=self.kind)
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
        # Slot name is ``object`` for object anchors, ``event`` for event
        # anchors — every legacy task reads ``ctx["object"]`` so switching
        # by kind keeps the object side untouched while giving event tasks
        # their own slot.
        slot = "event" if self.kind == "event" else "object"
        ctx[slot] = {"ocel_id": anchor_id, "ocel_type": anchor_type, "attributes": attrs}

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
        """Attach ``peer_objects``: up to 5 peers of the same type, full attribute rows.

        Delegates to :func:`sampling.sample_peers` for deterministic +
        representative selection. When ``target_col`` is given, stratifies so
        at least half the peers have a non-null value in that column. The
        peers come from the object-type table for object tasks and from
        the event-type table for event tasks (via ``self.kind``); the ctx
        slot is always ``peer_objects`` — event tasks reuse the same slot
        rather than introducing a parallel ``peer_events`` so downstream
        prompt / rendering machinery stays uniform.
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
            kind=self.kind,
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
