from src.llm.actions import ActionResult, object_attribute_target
from src.llm.dataset_hints import DatasetHints
from src.llm.schemas import InferredValueOutput
from src.llm.tasks._base import ResolutionTask


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
        - attribute_hint             optional per-log hint from the dataset
                                     hints file (if set, follow it)
    """

    METHOD = """
        1. Read `peer_objects` first. They fix the value's type, unit,
           casing, and format. Match them.
        2. Look at the anchor's other attributes and events for
           correlations (country → currency, activity `pay_in_eur` →
           `EUR`, etc.).
        3. If `attribute_hint` is present, follow the guidance it carries
           — the log's maintainer set it deliberately.
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

    def extend_context(self, conn, ctx: dict, row: dict, *, hints: DatasetHints) -> None:
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
                if k.lower() in {c.lower() for c in hints.name_columns}
                and v not in (None, "")
            ),
            None,
        )

        # Consult the dataset hints for a per-attribute rule.
        attr_hint = hints.hint_for(target, object_type)
        if attr_hint is not None:
            if not name and attr_hint.id_is_name and anchor_id:
                name = str(anchor_id)
            if attr_hint.guidance:
                ctx["attribute_hint"] = attr_hint.guidance

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
