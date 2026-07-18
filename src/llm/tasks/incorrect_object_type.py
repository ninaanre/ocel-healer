from src.llm.schemas import InferredTypeOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class IncorrectObjectType(DetectionTask):
    issue_key = "incorrect_object_type"
    family = "type"
    OutputModel = InferredTypeOutput

    TASK = """
        An object row has a non-empty `ocel_type`, but that type may be incorrect.
        Decide whether the current type is consistent with the object's id,
        the events touching it, and its attribute row. Flag a mismatch only
        when the evidence points to a different type; otherwise return null.
    """

    INPUTS = """
        - violation.ocel_id       the id of the object being checked
        - violation.ocel_type     the object's CURRENT type (the value to validate)
        - object.attributes       the anchor's existing attributes (may be empty)
        - events                  up to 8 events touching this object, each with
                                  the qualifier under which the event references it
        - candidate_types         the closed list of valid object types
    """

    METHOD = """
        1. Compare the current `ocel_type` against three signals, most to least
           informative:
             - The `ocel_id`. An id keyword (e.g. `customer`, `order`, `product`)
               that contradicts the current type is STRONG on its own — do NOT
               downgrade it to "weak" just because events and attributes are
               sparse.
             - Event activities + qualifiers (e.g. `place_order` with qualifier
               `customer` implies the object is a customer).
             - Attribute names / shapes (`email`, `country` → customer;
               `sku`, `price` → product).
        2. If the evidence is consistent with the current type, return
           `inferred_type: null`. Default to null only when the id is opaque
           (UUID or numeric with no type keyword) AND events and attributes
           are absent or neutral.
        3. When the evidence contradicts the current type, return the one
           value from `candidate_types` that best fits the evidence.
        4. Never return the current `ocel_type` as `inferred_type` — if you
           agree with it, return null.
    """

    EXAMPLES = """
        violation = {ocel_id: 'O42', ocel_type: 'product'}
        events = [{activity: 'place_order', qualifier: 'customer'},
                  {activity: 'ship_order', qualifier: 'customer'}]
        object.attributes = {email: 'a@b.com', country: 'DE'}
        candidate_types = ['customer', 'order', 'product']
        → {"inferred_type": "customer",
           "rationale": "qualifier 'customer' on both events plus email/country attributes contradict the current 'product' tag",
           "confidence": 0.95}

        violation = {ocel_id: 'O7', ocel_type: 'order'}
        events = [{activity: 'place_order', qualifier: 'order'}]
        candidate_types = ['customer', 'order', 'product']
        → {"inferred_type": null,
           "rationale": "events qualify this object as 'order', matching the current type",
           "confidence": 0.95}

        violation = {ocel_id: 'product124', ocel_type: 'order'}
        events = []
        object.attributes = {}
        candidate_types = ['customer', 'order', 'product']
        → {"inferred_type": "product",
           "rationale": "ID 'product124' contains keyword 'product' which contradicts 'order'; no events or attributes but the id signal is sufficient",
           "confidence": 0.85}
    """

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggested = payload.get("inferred_type")
        # Null / empty / "same as current" → not flagged.
        if not suggested or suggested == row.get("ocel_type"):
            return DetectionResult(
                flagged=False, rationale=rationale, confidence=confidence,
                suggested_value=None,
            )
        return DetectionResult(
            flagged=True, rationale=rationale, confidence=confidence,
            suggested_value=suggested,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return {
            "target_table": "object",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
