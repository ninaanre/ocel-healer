from src.detection.error_detection import _column_info
from src.llm.actions import ActionResult, object_attribute_target
from src.llm.schemas import InferredValueOutput
from src.llm.sql_utils import table_for_type
from src.llm.tasks._base import ResolutionTask


# Attribute columns that typically carry a human-readable entity name.
# Used to resolve `anchor_entity.name` for domain-knowledge lookups.
NAME_COLUMNS: tuple[str, ...] = ("name", "title", "product_name", "label")

# Dataset-agnostic by design: no qualifier names are hardcoded here (e.g.
# this log's "comprises"/"contains" won't exist verbatim in another log, and
# may not even be English). Every outgoing object_object relation from the
# anchor is inspected; a target is only included when its own per-type table
# happens to carry the SAME attribute column -- that structural fact alone
# decides relevance, not the qualifier's spelling. The qualifier name is
# still passed through as context so the model itself can judge whether the
# relation reads as a part-whole one (verified for this log: a package's
# weight is the exact sum of its `contains`-linked items' weight).
def _attach_components(conn, ctx: dict, anchor_id: str | None, column: str | None) -> None:
    """If other objects relate to the anchor and carry the SAME attribute,
    attach them (grouped by qualifier) plus each group's sum -- lets the
    model derive e.g. a whole's value from its parts instead of guessing."""
    if not anchor_id or not column:
        return
    parts = conn.execute(
        "SELECT ocel_target_id, ocel_qualifier FROM object_object WHERE ocel_source_id = ?",
        (anchor_id,),
    ).fetchall()
    if not parts:
        return

    groups: dict[str, list[dict]] = {}
    sums: dict[str, float] = {}
    complete: dict[str, bool] = {}
    for part_id, qualifier in parts:
        part_type_row = conn.execute(
            "SELECT ocel_type FROM object WHERE ocel_id = ?", (part_id,)
        ).fetchone()
        if not part_type_row:
            continue
        table = table_for_type(conn, part_type_row[0])
        if not table:
            continue
        cols = {c for c, _ in _column_info(conn, table)}
        if column not in cols:
            continue  # this relation's targets don't carry the attribute -- irrelevant
        val_row = conn.execute(
            f'SELECT "{column}" FROM "{table}" WHERE ocel_id = ? AND ocel_changed_field IS NULL',
            (part_id,),
        ).fetchone()
        value = val_row[0] if val_row else None
        groups.setdefault(qualifier, []).append(
            {"ocel_id": part_id, "ocel_type": part_type_row[0], column: value}
        )
        complete.setdefault(qualifier, True)
        if value is None:
            complete[qualifier] = False
        else:
            sums[qualifier] = sums.get(qualifier, 0.0) + value

    if groups:
        ctx["related_by_qualifier"] = groups
        ctx["related_sum_by_qualifier"] = {
            q: round(s, 6) for q, s in sums.items() if complete.get(q)
        }


class MissingAttributeValue(ResolutionTask):
    issue_key = "missing_attribute_value"
    family = "attribute"
    OutputModel = InferredValueOutput
    min_confidence = 0.0  # always attempt repair; model is instructed to always guess

    TASK = """
        An object row has a NULL or empty value for the attribute named in
        `violation.attribute_name`. Infer the most likely value. This task
        requires a concrete answer — never return null.
    """

    INPUTS = """
        - violation.attribute_name   the column whose value is missing
                                     (older rows may carry it as
                                     `violation.attribute`)
        - object.attributes          the anchor's other attributes — they
                                     often correlate with the missing one
                                     (e.g. `country` implies `currency`)
        - events                     up to 8 events touching the anchor
        - peer_objects               up to 5 peers of the same type, full
                                     attribute rows — the pattern here
                                     tells you the typical shape, unit,
                                     and format
        - related_by_qualifier       present only when other objects relate
                                     to the anchor AND carry this SAME
                                     attribute, grouped by the relation's
                                     qualifier — e.g. a package's `contains`
                                     group lists its items with their own
                                     weight
        - related_sum_by_qualifier   sum of each group's attribute value,
                                     precomputed — only for groups where
                                     every member has a value
        - anchor_entity              summary of the anchor: id, name (when
                                     resolvable), object_type, missing_attribute
        - exploration_hints          optional log-specific knowledge from the
                                     exploration phase (observed value
                                     vocabulary, repair hints, id semantics)
    """

    METHOD = """
        1. Read `peer_objects` first. They fix the value's type, unit,
           casing, and format. Match them.
        2. If `related_by_qualifier` is present, judge each group's
           qualifier name: does it read as a whole-part relation (the
           anchor is made of / holds these objects)? If so, and
           `related_sum_by_qualifier` has an entry for that qualifier,
           the anchor's value is very likely that sum — prefer it over a
           guess. A qualifier that reads as a role or reference (e.g. a
           salesperson, a shipper) is NOT a part-whole relation — ignore it
           even if it happens to share this attribute name.
        3. Look at the anchor's other attributes and events for
           correlations (country → currency, activity `pay_in_eur` →
           `EUR`, etc.).
        4. If `exploration_hints` are present, follow them:
           - `exploration_hints.attribute.known_values` are values this
             column is OBSERVED to take in this log (the list may be
             incomplete when many rows are missing). Strongly prefer
             returning one of them verbatim: map the evidence onto the
             closest listed value — e.g. known_values ["Gold", "Silver"]
             and the evidence says "silver tier" → answer "Silver".
           - `exploration_hints.attribute.repair_hint` states the
             log-specific way to infer this attribute;
             `domain_knowledge_applicable` tells you whether real-world
             knowledge may be used.
           - If `exploration_hints.object_type.id_is_entity_name` is true,
             the anchor's ocel_id (also in `anchor_entity.name`) is the
             entity's real-world name — identify the entity and recall the
             factual attribute value from your knowledge.
        5. When local context is silent, fall back to stable real-world
           knowledge about the entity named in `anchor_entity.name` (a
           product's typical weight, a country's currency, …). Match the
           unit used by the peers.
        6. Never return null. If evidence is weak, still return your best
           concrete guess and lower `confidence` accordingly (e.g. 0.3).
    """

    EXAMPLES = """
        violation.attribute_name = 'currency'
        peer_objects = [{currency: 'EUR'}, {currency: 'EUR'}, {currency: 'EUR'}]
        events = [{activity: 'pay_in_eur'}]
        → {"inferred_value": "EUR",
           "rationale": "all 3 peers use 'EUR' and activity 'pay_in_eur' confirms it",
           "confidence": 0.95}

        violation.attribute_name = 'weight'
        related_by_qualifier = {"contains": [{ocel_id: 'i-1', weight: 2.0}, {ocel_id: 'i-2', weight: 3.5}]}
        related_sum_by_qualifier = {"contains": 5.5}
        → {"inferred_value": 5.5,
           "rationale": "'contains' reads as a part-whole relation; the anchor's weight is the sum of its contained items' weight",
           "confidence": 0.9}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

        attrs = ctx.get("object", {}).get("attributes", {})
        anchor_id = ctx.get("object", {}).get("ocel_id")
        object_type = row.get("object_type")

        _attach_components(conn, ctx, anchor_id, target)

        # Try to resolve a human-readable name from the anchor's attributes.
        name = next(
            (
                str(v)
                for k, v in attrs.items()
                if k.lower() in {c.lower() for c in NAME_COLUMNS}
                and v not in (None, "")
            ),
            None,
        )

        # When exploration established that this type's ocel_id carries the
        # entity's real-world name, surface it as the name for lookups.
        hints = ctx.get("exploration_hints", {})
        if not name and hints.get("object_type", {}).get("id_is_entity_name"):
            name = str(anchor_id) if anchor_id else None

        ctx["anchor_entity"] = {
            "name": name,
            "object_id": anchor_id,
            "object_type": object_type,
            "missing_attribute": target,
        }

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_value")
        if new is None:
            # Pydantic should have rejected this — belt and braces.
            return ActionResult.decline(
                "LLM returned null despite instructions to always guess"
            )
        target = object_attribute_target(row)
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
        return object_attribute_target(row)
