from src.llm.actions import event_attribute_target
from src.llm.schemas import ImplausibleValueOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class IncorrectEventAttributeValue(DetectionTask):
    """LLM detection for type-correct but semantically implausible event
    attribute values.

    Direct mirror of :class:`IncorrectAttributeValue` for events. Runs on
    rows that already passed both ``missing_event_attribute_value`` and
    ``incorrect_event_attribute_datatype``. The LLM's job is to decide
    whether the (type-correct, non-null) value makes sense for its
    attribute given the peer distribution — flagging things like a
    ``commit_message`` with the wrong casing convention, an
    ``author_name`` that looks like a machine id, or an ``amount`` that
    is orders of magnitude off from peers.
    """

    issue_key = "incorrect_event_attribute_value"
    family = "attribute"
    kind = "event"
    OutputModel = ImplausibleValueOutput

    TASK = """
        An event row has an attribute value with the right SQL type and
        no NULL, but the value itself may be semantically wrong for the
        attribute. Decide whether the value is plausible given the
        attribute's name / meaning and the peer distribution. Flag only
        when the evidence is specific — a value that contradicts what
        the attribute records — and return null when the value looks
        fine or is merely unusual.
    """

    INPUTS = """
        - violation.event_type     the event's type
        - violation.ocel_id        the event id
        - violation.attribute      the attribute (column) name being checked
        - violation.expected_type  the column's SQL affinity (already satisfied)
        - violation.actual_value   the value currently in the cell
        - peer_objects             up to 5 peer events of the same type
                                   with their values for this attribute
                                   — the reference cohort. (The context
                                   builder reuses the object-side
                                   ``peer_objects`` slot; the peers here
                                   are events, keyed by their event type.)
    """

    METHOD = """
        1. Read the attribute name to infer what the column records (an
           activity label, a commit message, an amount, a status enum).
        2. Compare `actual_value` against `peer_objects[*][attribute]`.
           A value that matches the peer distribution's shape (casing,
           length, vocabulary, magnitude) is plausible even when unusual.
        3. Flag ONLY when the value contradicts the attribute's meaning:
             - a magnitude with the wrong sign or scale,
             - a value outside a closed peer vocabulary,
             - text with a clearly-different convention (all caps in a
               camel-case column, machine ids where every peer has a
               human name),
             - a date / timestamp incompatible with the log's window.
        4. Default to `suggested_value: null` whenever peers are absent
           or the attribute's semantics are unclear from the name alone.
        5. When flagging, propose the corrected value in `suggested_value`.
           It must match `expected_type`'s affinity and must differ from
           `actual_value` (returning the current value is equivalent to
           null).
    """

    EXAMPLES = """
        violation = {attribute: 'author_name', actual_value: 'x7f2b91c', expected_type: 'TEXT'}
        peer_objects = [{author_name: 'Ada Lovelace'}, {author_name: 'Grace Hopper'}]
        → {"suggested_value": null,
           "rationale": "'x7f2b91c' looks like a machine hash rather than a person name, but the correct name is not inferable from context",
           "confidence": 0.3}

        violation = {attribute: 'amount', actual_value: -50.0, expected_type: 'REAL'}
        peer_objects = [{amount: 12.5}, {amount: 8.0}, {amount: 24.99}]
        → {"suggested_value": 50.0,
           "rationale": "amounts in the peer cohort are positive; negative amount contradicts the attribute's meaning",
           "confidence": 0.9}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        # Peers stratified on the column being judged. The base class'
        # `kind = "event"` routes peer sampling through the event-type
        # table rather than the object-type table.
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # Anchor on the event: id + event_type. The base class' default
        # anchor() reads ``object_type``, which is absent from event rows.
        return (row.get("ocel_id"), row.get("event_type"))

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggested = payload.get("suggested_value")
        if suggested is None or suggested == row.get("actual_value"):
            return DetectionResult(
                flagged=False, rationale=rationale, confidence=confidence,
                suggested_value=None,
            )
        return DetectionResult(
            flagged=True, rationale=rationale, confidence=confidence,
            suggested_value=suggested,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return event_attribute_target(row)
