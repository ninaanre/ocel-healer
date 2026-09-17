from src.detection.error_detection import _column_info
from src.llm.actions import ActionResult, event_attribute_target
from src.llm.schemas import CanonicalValueOutput
from src.llm.sql_utils import quote, table_for_type
from src.llm.tasks._base import ResolutionTask


class DuplicateEventsOnAttributes(ResolutionTask):
    """Event-side mirror of :class:`DuplicateObjectsOnAttributes`.

    Two or more events of the same type share an identical attribute
    fingerprint but differ on `ocel_id`. The LLM picks the canonical
    value for the conflicting attribute on the anchor event.
    """

    issue_key = "duplicate_events_on_attributes"
    family = "duplicate"
    kind = "event"
    OutputModel = CanonicalValueOutput

    TASK = """
        Two or more events of the same type share an identical attribute
        fingerprint but differ on `ocel_id`. Pick the canonical value for
        the conflicting attribute on the anchor event.
    """

    INPUTS = """
        - violation.attribute_name      the column in conflict (older rows
                                        may carry it as `attribute`)
        - duplicate_attribute_values    the candidate values to choose from
        - event_attributes              the anchor's full attribute row — use
                                        its other columns as formatting /
                                        casing / units cues
        - related_objects               the objects this event touches;
                                        their types and qualifiers are
                                        tiebreakers
    """

    METHOD = """
        1. Compare the candidates in `duplicate_attribute_values`. Prefer
           the one whose formatting, casing, and units match the anchor's
           other attributes.
        2. Use `related_objects` types and qualifiers as tiebreakers (e.g.
           an event touching an object of type `EUR-account` implies `EUR`
           over `USD`).
        3. The chosen value must be one of `duplicate_attribute_values`
           verbatim.
    """

    EXAMPLES = """
        violation.attribute_name = 'currency'
        duplicate_attribute_values = ['EUR', 'USD']
        event_attributes = {country: 'DE', amount: '100.00'}
        related_objects = [{ocel_type: 'EUR-account'}]
        → {"canonical_value": "EUR",
           "rationale": "country='DE' and related EUR-account object both point to EUR over USD",
           "confidence": 0.9}
    """

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # Duplicate-attribute rows carry `event_type` in the anchor slot
        # rather than `object_type`; override the default so `_attach_anchor`
        # can resolve the per-type event sub-table.
        # `ocel_ids` is a comma-joined list — pick the first for the anchor.
        ids = [i.strip() for i in str(row.get("ocel_ids", "")).split(",") if i.strip()]
        anchor_id = ids[0] if ids else None
        return anchor_id, row.get("event_type")

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        dup_vals = row.get("attribute_values", "")
        ctx["duplicate_attribute_values"] = [
            v.strip() for v in str(dup_vals).split(",") if v.strip()
        ]
        # Also provide the full attribute row for the anchor event.
        anchor_id, anchor_type = self.anchor(row)
        table = table_for_type(conn, anchor_type, kind="event")
        if not table or not anchor_id:
            return
        cols = [c for c, _ in _column_info(conn, table)]
        if not cols:
            return
        quoted = ", ".join(quote(c) for c in cols)
        r = conn.execute(
            f"SELECT {quoted} FROM {quote(table)} WHERE ocel_id = ? LIMIT 1",
            (anchor_id,),
        ).fetchone()
        if r:
            ctx["event_attributes"] = dict(zip(cols, r))
        # Related objects as a tiebreaker signal.
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

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        canonical_val = payload.get("canonical_value")
        if canonical_val is None:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(
                f"LLM could not determine a canonical attribute value: {reason}"
            )
        # `event_attribute_target` needs `ocel_id` on the row; fall back to
        # the anchor id when the detector emitted `ocel_ids` (a joined list).
        target_row = dict(row)
        if "ocel_id" not in target_row:
            ids = [i.strip() for i in str(row.get("ocel_ids", "")).split(",") if i.strip()]
            if ids:
                target_row["ocel_id"] = ids[0]
        target = event_attribute_target(target_row)
        if target is None:
            return ActionResult.unrouted(
                "Could not determine attribute column name from violation row."
            )
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=canonical_val,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        target_row = dict(row)
        if "ocel_id" not in target_row:
            ids = [i.strip() for i in str(row.get("ocel_ids", "")).split(",") if i.strip()]
            if ids:
                target_row["ocel_id"] = ids[0]
        return event_attribute_target(target_row)
