# TODO: refine & fix this file!

import sqlite3
import textwrap
from abc import ABC, abstractmethod
from typing import Any, ClassVar, TYPE_CHECKING

from src.detection.error_detection import _column_info, _object_type_tables
from src.llm.sql_utils import quote, table_for_type

if TYPE_CHECKING:
    from src.llm.actions import ActionResult


class IssueTask(ABC):
    """Base class for one repair task per OCEL2 issue type.

    Subclasses set `issue_key` and `PROMPT`, implement `parse_payload`, and
    optionally override `extend_context`, `anchor`, and `suppressed_target`.
    Importing the module self-registers via `__init_subclass__`.
    """

    issue_key: ClassVar[str] = ""
    PROMPT: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "issue_key", ""):
            # Lazy import avoids a circular dependency with tasks/__init__.py.
            from src.llm.tasks import REGISTRY
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
        attribute rows. Used by missing_attribute_value / wrong_attribute_datatype
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
