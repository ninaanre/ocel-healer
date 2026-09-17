from src.llm.actions import object_attribute_target
from src.llm.schemas import ImplausibleValueOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class IncorrectAttributeValue(DetectionTask):
    """LLM detection for type-correct but semantically implausible attribute values.

    Runs on rows that already passed both `missing_attribute_value` (value is
    not NULL / empty) and `incorrect_attribute_datatype` (value's Python type
    matches the column's SQLite affinity). The LLM's job is to decide whether
    the value makes sense for its attribute — flagging things like negative
    prices, out-of-vocab enum labels, impossible dates, or magnitudes that
    fall far outside the peer distribution. Deterministic type/format wrongness
    is out of scope; that's the rule-based sibling's job.
    """

    issue_key = "incorrect_attribute_value"
    family = "attribute"
    OutputModel = ImplausibleValueOutput

    TASK = """
        An object row has an attribute value with the right SQL type and no
        NULL, but the value itself may be semantically wrong for the attribute.
        Decide whether the value is plausible given the attribute's name /
        meaning and the peer distribution. Flag only when the evidence is
        specific — a value that contradicts what the attribute records — and
        return null when the value looks fine or is merely unusual.
    """

    INPUTS = """
        - violation.object_type    the anchor object's type
        - violation.ocel_id        the anchor object's id
        - violation.attribute      the attribute (column) name being checked
        - violation.expected_type  the column's SQL affinity (already satisfied)
        - violation.actual_value   the value currently in the cell
        - object.attributes        the anchor's other attributes for context
        - peer_objects             up to 5 same-type peers with their values
                                   for this attribute — the reference cohort
    """

    METHOD = """
        1. Read the attribute name and the anchor's other attributes to
           infer what the column records (a price, a country code, an age,
           a status enum, etc.).
        2. Compare `actual_value` against `peer_objects[*][attribute]`.
           A value that matches the peer distribution's shape (sign,
           magnitude, vocabulary) is plausible even when unusual.
        3. Flag ONLY when the value contradicts the attribute's meaning:
             - a magnitude with the wrong sign (negative price, negative age),
             - a value outside a closed peer vocabulary (an out-of-vocab
               country code / status / enum label),
             - a date that cannot correspond to what the column records
               (a birthdate in the future, an event timestamp before the
               log's known window),
             - a value orders of magnitude off from every peer.
        4. Default to `suggested_value: null` whenever the peers are absent
           or the attribute's semantics are unclear from the name alone.
           False positives are costly — silence is preferred over a guess.
        5. When flagging, propose the corrected value in `suggested_value`.
           It must match the column's `expected_type` affinity and must
           differ from `actual_value` (returning the current value is
           equivalent to null).
    """

    EXAMPLES = """
        violation = {attribute: 'price', actual_value: -19.99, expected_type: 'REAL'}
        peer_objects = [{price: 12.0}, {price: 8.5}, {price: 24.99}]
        → {"suggested_value": 19.99,
           "rationale": "prices in the peer cohort are positive; negative price contradicts the attribute's meaning",
           "confidence": 0.9}

        violation = {attribute: 'country', actual_value: 'XZ', expected_type: 'TEXT'}
        peer_objects = [{country: 'DE'}, {country: 'US'}, {country: 'DE'}, {country: 'FR'}]
        → {"suggested_value": null,
           "rationale": "'XZ' is not in the peer vocabulary but the correct value is not inferable from context",
           "confidence": 0.3}

        violation = {attribute: 'quantity', actual_value: 3, expected_type: 'INTEGER'}
        peer_objects = [{quantity: 1}, {quantity: 5}, {quantity: 2}]
        → {"suggested_value": null,
           "rationale": "quantity 3 matches the peer distribution's shape",
           "confidence": 0.95}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        # Peers stratified on the column being judged so the cohort actually
        # constrains the plausibility check.
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggested = payload.get("suggested_value")
        # Null / same-as-current → not flagged. Type coercion isn't safe here
        # (`"19.99" == 19.99` is False even when they mean the same thing),
        # but treating identical scalars as "no change" is enough to filter
        # LLMs that echo the input.
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
        return object_attribute_target(row)
