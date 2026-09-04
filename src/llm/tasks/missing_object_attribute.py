"""LLM detector for `missing_object_attribute`.

Semantics — **expected-but-not-declared**. Given an object type, its
already-declared columns, and cross-type context (the exploration guide's
domain description + peer object types' schemas), the LLM proposes
attribute names the schema is *missing*. An empty list means "no
suggestions"; a non-empty list becomes N proposal cards in the drill-in,
one per suggested attribute.

Resolution on confirm — an `alter_add_column` action adds the suggested
attribute to the per-type sub-table with a NULL default. The pre-existing
`missing_attribute_value` detector then picks up the new NULLs on the
next sweep and lets the user fill them with its (already-implemented)
resolution task. Clean two-phase separation.
"""

from __future__ import annotations

from src.detection.error_detection import (
    _column_info,
    _object_type_tables,
    _OCEL_RESERVED,
)
from src.exploration.hint_selector import all_type_summaries
from src.llm.actions import ActionResult
from src.llm.schemas import SuggestedAttributesOutput
from src.llm.tasks._base import DetectionResult, DetectionTask


class MissingObjectAttribute(DetectionTask):
    issue_key = "missing_object_attribute"
    family = "attribute"
    kind = "object"
    OutputModel = SuggestedAttributesOutput

    # Stashed on the candidate row by candidate_sources so build_context
    # can find the resolved sub-table without re-querying. Leading
    # underscore signals internal-use-only so the prompt renderer ignores it.
    _TABLE_HINT_KEY = "_resolved_target_table"

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # No object anchor — we reason about the TYPE, not an instance. The
        # base-class anchor helpers no-op with (None, None).
        return (None, None)

    def select_hints(self, profile: dict, guide: dict | None, row: dict) -> dict:
        # Give the model the full type overview: to judge whether Order is
        # missing `total_amount`, it needs to see that Product has `price`
        # and Shipment has `weight`.
        return {"all_object_types": all_type_summaries(profile, guide)}

    def build_context(self, conn, row, *, use_hints=True):
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        type_table_pairs = _object_type_tables(conn)
        type_map = {t: table for t, table in type_table_pairs}

        chosen_type = row.get("ocel_type") or row.get("object_type")
        target_table = type_map.get(chosen_type) if chosen_type else None
        row[self._TABLE_HINT_KEY] = target_table

        # Currently-declared columns for the type under review — the LLM
        # must NOT re-suggest any of these. Reserved OCEL2 columns are
        # excluded because they are structural (ocel_id / ocel_type).
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

        # Peer schemas — every other object type with its declared columns
        # (excluding reserved). Lets the LLM spot "Product has `price` and
        # `sku` but Order has no monetary field".
        peers: dict[str, list[str]] = {}
        for t, table in type_table_pairs:
            if t == chosen_type:
                continue
            peers[t] = [
                c for c, _ in _column_info(conn, table) if c not in _OCEL_RESERVED
            ]
        ctx["peer_types"] = peers

        if use_hints:
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    TASK = """
        An object TYPE may be missing one or more attributes that the domain
        would normally record. Look at the type's currently-declared
        attributes, its peers' attributes, and any domain hints, and propose
        attribute NAMES that ought to exist on this type but don't. Return
        an empty list when the schema looks complete.
    """

    INPUTS = """
        - current_type.ocel_type          the type under review
        - current_type.declared_attributes  columns already declared on its
                                           per-type sub-table (never re-suggest)
        - peer_types                       {type: [columns…]} for every OTHER
                                           object type in the log
        - exploration_hints.all_object_types  compact overview of every type
                                           (id template, "represents" note) so
                                           you know what the type is FOR
    """

    METHOD = """
        1. Read `current_type.declared_attributes` first. Any attribute you
           suggest MUST NOT be in that list — case-insensitively.
        2. Compare against `peer_types`. Attributes that most peers have
           and this type lacks are strong candidates (e.g. every entity type
           has `created_at` but this one doesn't).
        3. Cross-reference `exploration_hints.all_object_types[current_type].
           represents` for the type's business meaning. An Order without a
           monetary total, a Customer without contact info, a Product without
           a price are canonical omissions.
        4. Do NOT propose reserved / structural columns
           (`ocel_id`, `ocel_type`, anything with an `ocel_` prefix).
        5. Do NOT propose columns that are just renames of existing ones
           (e.g. `customer_id` when `customer_ref` is already declared).
        6. Prefer AT MOST 5 suggestions. Fewer is better than noise. Return
           an empty list when nothing plausibly belongs.
        7. For each suggestion set `affinity` to a SQLite storage class
           (`TEXT`, `INTEGER`, `REAL`, or `BLOB`). Default to `TEXT` when
           unsure — the fix path treats unknown affinities as TEXT anyway.
    """

    EXAMPLES = """
        current_type = {ocel_type: 'order',
                        declared_attributes: ['customer_ref', 'created_at']}
        peer_types = {'product': ['sku', 'price', 'name'],
                      'customer': ['email', 'country']}
        exploration_hints.all_object_types = {
          'order': {represents: 'A purchase order placed by a customer'},
          'product': {...}, 'customer': {...}}
        → {"suggested_attributes": [
             {"name": "total_amount",
              "rationale": "Orders carry a monetary total in every domain we've seen; peer 'product' declares 'price' but 'order' has no monetary field.",
              "confidence": 0.9,
              "affinity": "REAL"},
             {"name": "status",
              "rationale": "Orders progress through states (placed→shipped→delivered); no status column exists.",
              "confidence": 0.7,
              "affinity": "TEXT"}],
           "rationale": "Two attributes look plausibly missing; skipped a third candidate because it may duplicate 'customer_ref'.",
           "confidence": 0.85}

        current_type = {ocel_type: 'product',
                        declared_attributes: ['sku', 'name', 'price', 'weight']}
        peer_types = {...}
        → {"suggested_attributes": [],
           "rationale": "Product schema already carries identifier, label, monetary value and physical property — no obvious omission.",
           "confidence": 0.9}
    """

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggestions = payload.get("suggested_attributes") or []
        # Filter out anything already declared (belt-and-braces guard on the
        # LLM prompt) and reserved names. Case-insensitive on declared cols.
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
            # The full list rides on suggested_value — the sweep cell fans it
            # out into one proposal card per entry. flag_key uses the item's
            # `name` as the disambiguator so each card is independent.
            suggested_value=clean,
        )

    def action_for_flag(self, row: dict, verdict: DetectionResult) -> ActionResult:
        # By the time we get here the sweep-side fan-out has already split
        # the list into one flag per attribute — so `row["attribute"]` is
        # the single suggested column name and `verdict.suggested_value`
        # is that same single dict (see drill_llm_sweep fan-out).
        target_table = row.get(self._TABLE_HINT_KEY) or row.get("_resolved_target_table")
        if not target_table:
            # Best-effort fallback — the dashboard's sweep sets it, but
            # apply-time might not have it if the flag was persisted across
            # sessions. Reconstruct via ocel_type + type-table conventions.
            chosen_type = row.get("ocel_type") or row.get("object_type")
            if chosen_type:
                target_table = f"object_{chosen_type}"
        if not target_table:
            return ActionResult.unrouted(
                "missing_object_attribute flag has no resolvable target table."
            )

        # `attribute` distinguisher comes from flag_key — see dashboard.py.
        column = row.get("attribute") or row.get("attribute_name")
        if not column and isinstance(verdict.suggested_value, dict):
            column = verdict.suggested_value.get("name")
        if not column:
            return ActionResult.unrouted(
                "missing_object_attribute flag has no attribute name."
            )

        # Affinity resolution order: the stashed suggestion (from the
        # original detection sweep, always accurate for the confirmed
        # attribute) → the fresh verdict (if this is a Detection→fix
        # bridge round-trip) → TEXT default. The stashed path matters
        # when the user re-runs `suggest_repair` on a confirmed flag:
        # the fresh LLM call may not include the same attribute name and
        # would otherwise fall back to TEXT.
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
        # No override target — a schema addition has no single-row column to
        # rescue. Returning None makes noops non-overridable, which is the
        # correct affordance for a schema change.
        return None
