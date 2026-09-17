"""LLM detector for `missing_event_attribute`.

Event-side counterpart of `missing_object_attribute`. Given an event
type, its already-declared columns, and every peer event type's schema,
the LLM proposes attribute names the schema ought to carry but doesn't.

Note: the exploration guide currently has no `event_types` section
(see `src/exploration/explorer_agent.py` — only `object_types` gets an
LLM-produced description). We therefore feed the model just SQL-derived
schema info plus peer event schemas. This is enough for common misses
(a "Ship Order" event without `shipping_address`, a "Pay Order" event
without `payment_method`) because peer event schemas encode the domain
implicitly.

Resolution on confirm — an `alter_add_column` action adds the suggested
attribute to the per-type event sub-table with a NULL default; the
existing `missing_event_attribute_value` detector picks up the new
NULLs on the next sweep.
"""

from __future__ import annotations

from src.detection.error_detection import (
    _column_info,
    _event_type_tables,
    _OCEL_RESERVED,
)
from src.llm.actions import ActionResult
from src.llm.schemas import SuggestedAttributesOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class MissingEventAttribute(DetectionTask):
    issue_key = "missing_event_attribute"
    family = "attribute"
    kind = "event"
    OutputModel = SuggestedAttributesOutput

    _TABLE_HINT_KEY = "_resolved_target_table"

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        return (None, None)

    def build_context(self, conn, row, *, use_hints=True):
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        type_table_pairs = _event_type_tables(conn)
        type_map = {t: table for t, table in type_table_pairs}

        chosen_type = row.get("ocel_type") or row.get("event_type")
        target_table = type_map.get(chosen_type) if chosen_type else None
        row[self._TABLE_HINT_KEY] = target_table

        if target_table:
            declared = [
                c for c, _ in _column_info(conn, target_table)
                if c not in _OCEL_RESERVED
            ]
            ctx["current_type"] = {
                "ocel_type": chosen_type,
                "table": target_table,
                "declared_attributes": declared,
            }

        # Peer event schemas — every OTHER event type with its declared
        # columns. Encodes the domain enough for the model to spot
        # "every fulfilment step records a warehouse_id except this one".
        peers: dict[str, list[str]] = {}
        for t, table in type_table_pairs:
            if t == chosen_type:
                continue
            peers[t] = [
                c for c, _ in _column_info(conn, table) if c not in _OCEL_RESERVED
            ]
        ctx["peer_event_types"] = peers

        if use_hints:
            # Best-effort — the guide has no event_types section today, so
            # this attaches (at most) qualifier context. Not required for
            # the task to succeed.
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    TASK = """
        An event TYPE may be missing one or more attributes that the
        activity would normally record. Compare the type's declared
        attributes with what its peer event types carry, and propose
        attribute NAMES that ought to exist on this event but don't.
        Return an empty list when the schema looks complete.
    """

    INPUTS = """
        - current_type.ocel_type           the event type under review
        - current_type.declared_attributes columns already declared (never
                                           re-suggest)
        - peer_event_types                 {event_type: [columns…]} for every
                                           OTHER event type in the log
    """

    METHOD = """
        1. Read `current_type.declared_attributes` first. Any attribute you
           suggest MUST NOT be in that list (case-insensitively).
        2. Compare against `peer_event_types`. Attributes shared by most
           peer events (e.g. `user_id`, `location`, `duration`) that this
           type lacks are strong candidates.
        3. Cross-reference the event type's NAME/activity for domain hints:
           a "Ship …" event without `carrier` or `tracking_number`, a
           "Pay …" event without `amount` or `payment_method`, a
           "Approve …" event without an approver id.
        4. Do NOT propose reserved / structural columns
           (`ocel_id`, `ocel_type`, `ocel_time`, anything with an `ocel_`
           prefix — `ocel_time` is the canonical timestamp).
        5. Prefer AT MOST 5 suggestions. Return an empty list when
           nothing plausibly belongs.
        6. For each suggestion set `affinity` to a SQLite storage class
           (`TEXT`, `INTEGER`, `REAL`, or `BLOB`). Default to `TEXT`.
    """

    EXAMPLES = """
        current_type = {ocel_type: 'Ship Order',
                        declared_attributes: ['warehouse']}
        peer_event_types = {'Pay Order': ['amount', 'method'],
                            'Place Order': ['channel']}
        → {"suggested_attributes": [
             {"name": "tracking_number",
              "rationale": "Shipping events normally record a carrier tracking id; not present.",
              "confidence": 0.85,
              "affinity": "TEXT"},
             {"name": "carrier",
              "rationale": "Peer 'Pay Order' records 'method'; the shipping counterpart is the carrier.",
              "confidence": 0.75,
              "affinity": "TEXT"}],
           "rationale": "Two shipping-specific fields look plausibly missing.",
           "confidence": 0.8}

        current_type = {ocel_type: 'Complete',
                        declared_attributes: ['user_id']}
        peer_event_types = {'Start': ['user_id']}
        → {"suggested_attributes": [],
           "rationale": "Symmetric with peer 'Start'; no obvious omission.",
           "confidence": 0.85}
    """

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggestions = payload.get("suggested_attributes") or []
        declared_lc = {
            c.lower()
            for c in (row.get("_declared_attributes") or [])
        }
        clean: list[dict] = []
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name.lower() in declared_lc:
                continue
            if name.lower().startswith("ocel_"):
                continue
            clean.append({
                "name": name,
                "rationale": str(item.get("rationale") or "").strip(),
                "confidence": float(item.get("confidence") or 0.0),
                "affinity": (item.get("affinity") or "TEXT"),
            })
        if not clean:
            return DetectionResult(
                flagged=False, rationale=rationale, confidence=confidence,
                suggested_value=None,
            )
        return DetectionResult(
            flagged=True, rationale=rationale, confidence=confidence,
            suggested_value=clean,
        )

    def action_for_flag(self, row: dict, verdict: DetectionResult) -> ActionResult:
        target_table = row.get(self._TABLE_HINT_KEY) or row.get("_resolved_target_table")
        if not target_table:
            # Fallback: reconstruct via event_map_type at apply time. We can't
            # touch sqlite here, so fall through to the sub-table name pattern
            # and let the apply-side resolver tolerate case differences.
            chosen_type = row.get("ocel_type") or row.get("event_type")
            if chosen_type:
                target_table = f"event_{chosen_type}"
        if not target_table:
            return ActionResult.unrouted(
                "missing_event_attribute flag has no resolvable target table."
            )

        column = row.get("attribute") or row.get("attribute_name")
        if not column and isinstance(verdict.suggested_value, dict):
            column = verdict.suggested_value.get("name")
        if not column:
            return ActionResult.unrouted(
                "missing_event_attribute flag has no attribute name."
            )

        affinity = "TEXT"
        stashed = row.get("_detected_suggestion")
        if isinstance(stashed, dict) and stashed.get("affinity"):
            affinity = str(stashed["affinity"])
        elif isinstance(verdict.suggested_value, dict):
            affinity = str(verdict.suggested_value.get("affinity") or "TEXT")
        return ActionResult.alter_add_column(
            target_table=target_table,
            column=str(column),
            affinity=affinity,
            reason=verdict.rationale or f"Add missing attribute {column!r}.",
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return None
