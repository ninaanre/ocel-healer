from src.llm.actions import ActionResult, object_attribute_target
from src.llm.schemas import InferredValueOutput
from src.llm.tasks._base import ResolutionTask


# Attribute columns that typically carry a human-readable entity name.
# Used to resolve `anchor_entity.name` for domain-knowledge lookups.
NAME_COLUMNS: tuple[str, ...] = ("name", "title", "product_name", "label")


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
        - anchor_entity              summary of the anchor: id, name (when
                                     resolvable), object_type, missing_attribute
        - exploration_hints          optional log-specific knowledge from the
                                     exploration phase (observed value
                                     vocabulary, repair hints, id semantics)
    """

    METHOD = """
        1. Read `peer_objects` first. They fix the value's type, unit,
           casing, and format. Match them.
        2. Look at the anchor's other attributes and events for
           correlations (country → currency, activity `pay_in_eur` →
           `EUR`, etc.).
        3. If `exploration_hints` are present, follow them:
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
        4. When local context is silent, fall back to stable real-world
           knowledge about the entity named in `anchor_entity.name` (a
           product's typical weight, a country's currency, …). Match the
           unit used by the peers.
        5. Never return null. If evidence is weak, still return your best
           concrete guess and lower `confidence` accordingly (e.g. 0.3).
    """

    EXAMPLES = """
        violation.attribute_name = 'currency'
        peer_objects = [{currency: 'EUR'}, {currency: 'EUR'}, {currency: 'EUR'}]
        events = [{activity: 'pay_in_eur'}]
        → {"inferred_value": "EUR",
           "rationale": "all 3 peers use 'EUR' and activity 'pay_in_eur' confirms it",
           "confidence": 0.95}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        target = row.get("attribute_name") or row.get("attribute")
        self._attach_peers(conn, ctx, row, target_col=target)

        attrs = ctx.get("object", {}).get("attributes", {})
        anchor_id = ctx.get("object", {}).get("ocel_id")
        object_type = row.get("object_type")

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
