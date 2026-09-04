from src.detection.error_detection import _event_type_tables
from src.llm.schemas import InferredTypeOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class IncorrectEventType(DetectionTask):
    """Event-side mirror of :class:`IncorrectObjectType`.

    Checks whether an event's ``ocel_type`` is consistent with:
      - the activity keyword in its ``ocel_id`` (if any),
      - the types of the objects it touches through ``event_object``,
      - the qualifiers under which it references those objects.

    Flags a mismatch only when the evidence points to a different type from
    ``candidate_types``; otherwise returns ``inferred_type: null``.
    """

    issue_key = "incorrect_event_type"
    family = "type"
    kind = "event"
    OutputModel = InferredTypeOutput

    TASK = """
        An event row has a non-empty `ocel_type`, but that type may be incorrect.
        Decide whether the current type is consistent with the event's id, the
        objects it touches, and the qualifiers it uses. Flag a mismatch only
        when the evidence points to a different type; otherwise return null.
    """

    INPUTS = """
        - violation.ocel_id       the id of the event being checked
        - violation.ocel_type     the event's CURRENT type (the value to validate)
        - event.attributes        the anchor's existing attributes (may be empty)
        - related_objects         list of {ocel_object_id, ocel_type,
                                  ocel_qualifier} — the objects this event
                                  touches; qualifiers name the role each
                                  object plays in this activity
        - candidate_types         the closed list of valid event types
    """

    METHOD = """
        1. Compare the current `ocel_type` against three signals, most to
           least informative:
             - The `ocel_id`. An activity keyword embedded in the id (e.g.
               `place_order`, `pay_order`, `pick_item`) that contradicts
               the current type is STRONG on its own — do NOT downgrade it
               to "weak" just because touched objects are sparse.
             - The touched objects' types + qualifiers in `related_objects`.
               For example, an event touching an `order` object with
               qualifier `order` is likely an order-lifecycle activity;
               one touching an `item` with qualifier `picked` suggests a
               pick activity.
             - The event's own attribute row.
        2. If the evidence is consistent with the current type, return
           `inferred_type: null`. Default to null only when the id is
           opaque (UUID or numeric with no activity keyword) AND touched
           objects are absent or neutral.
        3. When the evidence contradicts the current type, return the one
           value from `candidate_types` that best fits the evidence.
        4. Never return the current `ocel_type` as `inferred_type` — if you
           agree with it, return null.
    """

    EXAMPLES = """
        violation = {ocel_id: 'pay_order:990010', ocel_type: 'place order'}
        related_objects = [{ocel_type: 'order', ocel_qualifier: 'order'}]
        candidate_types = ['place order', 'pay order', 'pick item', 'send package']
        → {"inferred_type": "pay order",
           "rationale": "id keyword 'pay_order' contradicts the current 'place order' type; touched order object is consistent with either",
           "confidence": 0.95}

        violation = {ocel_id: 'e-7', ocel_type: 'pay order'}
        related_objects = [{ocel_type: 'order', ocel_qualifier: 'order'}]
        candidate_types = ['place order', 'pay order', 'pick item']
        → {"inferred_type": null,
           "rationale": "id is opaque; touched order object is consistent with the current 'pay order' type",
           "confidence": 0.8}
    """

    def build_context(self, conn, row, *, use_hints=True):
        # Copy the base flow (attach anchor event, hints, extend) but swap
        # in event-side candidate_types since `IssueTask.build_context`
        # hard-codes object_type_tables.
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _event_type_tables(conn)]
        anchor_id, anchor_type = self.anchor(row)
        if anchor_id:
            self._attach_anchor(conn, ctx, anchor_id, anchor_type)
        if use_hints:
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # Row shape (from ``_candidates_incorrect_event_type``) carries
        # ``ocel_id`` and ``ocel_type``; expose them as the (id, type)
        # anchor pair so ``_attach_anchor`` can locate the per-type sub-table.
        return row.get("ocel_id"), row.get("ocel_type")

    def extend_context(self, conn, ctx, row):
        # Related objects (through event_object) — the strongest event-type
        # signal after the id keyword.
        anchor_id = row.get("ocel_id")
        if not anchor_id:
            return
        related = conn.execute(
            "SELECT eo.ocel_object_id, o.ocel_type, eo.ocel_qualifier "
            "FROM event_object eo LEFT JOIN object o ON o.ocel_id = eo.ocel_object_id "
            "WHERE eo.ocel_event_id = ? LIMIT 8",
            (anchor_id,),
        ).fetchall()
        if related:
            ctx["related_objects"] = [
                {"ocel_object_id": oid, "ocel_type": otype, "ocel_qualifier": qual}
                for oid, otype, qual in related
            ]

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
        # Writes to the main `event` table's `ocel_type` — mirror of
        # `IncorrectObjectType.suppressed_target` targeting `object.ocel_type`.
        return {
            "target_table": "event",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
