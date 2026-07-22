from src.llm.actions import ActionResult, event_attribute_target
from src.llm.schemas import InferredValueOutput
from src.llm.tasks._base import ResolutionTask


class MissingEventAttributeValue(ResolutionTask):
    """Infer a missing event-attribute value from context.

    Mirror of :class:`MissingAttributeValue` for events. The detector
    (``detect_missing_event_attribute_value``) flagged an event cell as
    NULL / whitespace-only; this task always returns a concrete guess so
    the fix path can proceed. Same contract as the object side: never
    return null; low-confidence guesses are surfaced with reduced
    ``confidence`` and gated by ``MIN_CONFIDENCE`` in the action layer.
    """

    issue_key = "missing_event_attribute_value"
    family = "attribute"
    kind = "event"
    OutputModel = InferredValueOutput
    min_confidence = 0.0  # always attempt repair; model is instructed to always guess

    TASK = """
        An event row has a NULL or empty value for the attribute named in
        `violation.attribute`. Infer the most likely value. This task
        requires a concrete answer — never return null.
    """

    INPUTS = """
        - violation.attribute       the column whose value is missing
        - violation.event_type      the event's type
        - violation.ocel_id         the event id
        - event.attributes          the anchor event's other attributes
                                    (often correlate with the missing one)
        - peer_objects              up to 5 peer events of the same type,
                                    full attribute rows — the pattern here
                                    tells you the typical shape and format
        - exploration_hints         optional log-specific knowledge from
                                    the exploration phase
    """

    METHOD = """
        1. Read `peer_objects` first — they fix the value's type, unit,
           casing, and format. Match them.
        2. Look at the anchor event's other attributes for correlations
           (activity name → typical author, etc.).
        3. If `exploration_hints` are present, follow them (see the
           object-side task for the full protocol).
        4. Never return null. If evidence is weak, still return your best
           concrete guess and lower `confidence` accordingly.
    """

    EXAMPLES = """
        violation.attribute = 'author_name'
        peer_objects = [{author_name: 'Ada Lovelace'}, {author_name: 'Grace Hopper'}]
        event.attributes = {commit_message: 'fix typo in README'}
        → {"inferred_value": "unknown",
           "rationale": "peers have human names but this row's context is silent",
           "confidence": 0.2}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        return (row.get("ocel_id"), row.get("event_type"))

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_value")
        if new is None:
            return ActionResult.decline(
                "LLM returned null despite instructions to always guess"
            )
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
