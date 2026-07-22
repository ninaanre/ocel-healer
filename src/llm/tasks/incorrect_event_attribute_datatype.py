from src.llm.actions import ActionResult, event_attribute_target
from src.llm.schemas import CoercedValueOutput
from src.llm.tasks._base import ResolutionTask


class IncorrectEventAttributeDatatype(ResolutionTask):
    """Coerce an event-attribute value whose SQL affinity is wrong.

    Direct mirror of :class:`IncorrectAttributeDatatype` for events. The
    rule detector (``detect_incorrect_event_attribute_datatype``) already
    identified the violating cell; this task's job is to propose a
    meaning-preserving coercion the fix path can apply.
    """

    issue_key = "incorrect_event_attribute_datatype"
    family = "attribute"
    kind = "event"
    OutputModel = CoercedValueOutput

    TASK = """
        Coerce `violation.actual_value` so its type matches the SQL affinity
        in `violation.expected_type`, preserving the value's meaning.
    """

    INPUTS = """
        - violation.actual_value         Python repr() of the original cell.
                                         Wrapping single quotes mean the cell
                                         is currently a string — strip them
                                         before reasoning.
        - violation.actual_python_type   the cell's current Python type
        - violation.expected_type        the column's SQL affinity (target)
        - peer_objects                   up to 5 peer events with correctly
                                         typed values for this column — use
                                         them to sanity-check your coercion
    """

    METHOD = """
        SQL affinity → target Python type:
          INTEGER                                → int
          REAL / FLOA / DOUB / NUMERIC / DECIMAL → float
          TEXT / CHAR / CLOB                     → str
          BLOB                                   → bytes

        1. Identify the current value (strip repr quotes if present).
        2. Apply the obvious meaning-preserving coercion: string-of-digits
           → int/float, bool → 0/1, anything → its str form.
        3. Cross-check against `peer_objects` — the coerced value should
           look like the peers.
        4. Prefer a coercion whenever one preserves meaning, even at modest
           confidence. Return `coerced_value: null` only when no coercion
           preserves meaning (e.g. 'banana' → INTEGER).
    """

    EXAMPLES = """
        (actual_value, expected_type) → coerced_value
          ('42',   INTEGER) → 42
          ('3.14', REAL)    → 3.14
          ('true', INTEGER) → 1
          (42,     TEXT)    → "42"
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        return (row.get("ocel_id"), row.get("event_type"))

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("coerced_value")
        if new is None:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to coerce: {reason}")
        target = event_attribute_target(row)
        if target is None:
            return ActionResult.unrouted(
                "Could not determine attribute column name from violation row."
            )
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return event_attribute_target(row)
