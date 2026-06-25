from src.llm.actions import ActionResult, object_attribute_target
from src.llm.tasks._base import ResolutionTask


class MissingAttributeValue(ResolutionTask):
    issue_key = "missing_attribute_value"
    min_confidence = 0.0  # always attempt repair; model is instructed to always guess

    PROMPT = """\
        <task>
        An object row has a NULL or empty value for the attribute named in
        `violation.attribute_name`. Infer the most likely value.
        </task>

        <inputs>
          - violation.attribute_name   the attribute column whose value is missing
                                       (older rows may carry it as `violation.attribute`)
          - object.attributes          the anchor's other attributes (often correlate
                                       with the missing one — e.g. country implies currency)
          - events                     up to 8 events touching the anchor; activity
                                       names and qualifiers can pin down the value
          - peer_objects               up to 5 other objects of the same type with
                                       their full attribute rows — use these to learn
                                       the typical shape, format, and value distribution
        </inputs>

        <method>
          Evidence priority:
          1. LOCAL_CONTEXT: peer_objects with the same attribute set the expected value, format,
             and unit. Anchor attributes and events provide additional signals.
          2. EXPLORATION_REPORT (if present in the prompt):
             - If the report identifies `ocel_id` as a semantic name (product, person, place),
               use it as the basis for a domain knowledge lookup.
             - If the report marks an attribute as derivable from stable domain knowledge,
               you may use that knowledge.
             - If the report warns the attribute is ambiguous or process-specific,
               prefer local context only and return a low-confidence estimate.
          3. DOMAIN_KNOWLEDGE: only if EXPLORATION_REPORT permits it for this attribute/type,
             or if the object is a clearly recognisable real-world entity with a stable,
             factual attribute (e.g. product weight, release year, manufacturer).

          For all attributes:
            Use `peer_objects` to learn the typical value, data type, units, and format.
            Use the anchor's other attributes and events as additional signals.
            Use `anchor_entity.name` or `anchor_entity.object_id` if they carry semantic meaning.
        </method>

        <example>
          violation.attribute_name='currency'
          peer_objects=[{currency:'EUR'}, {currency:'EUR'}, {currency:'EUR'}]
          events=[{activity:'pay_in_eur'}]
          → {"inferred_value": "EUR", "rationale": "all 3 peers use 'EUR' and activity 'pay_in_eur' confirms it", "confidence": 0.95}
        </example>

        <output>
        JSON: {"inferred_value": <your best guess>, "rationale": str, "confidence": number}

        `inferred_value` must ALWAYS be a concrete value — never null.
        If local context and domain knowledge are both weak, still return your best
        estimate based on the object's name and other attributes, and set confidence
        accordingly (e.g. 0.3). A low-confidence guess is always better than null.

        If `anchor_entity.name` is missing, check whether `anchor_entity.object_id`
        carries a semantic name (product, person, place) — the EXPLORATION_REPORT
        will indicate this if it applies.
        </output>
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        self._attach_peers(conn, ctx, row)
        attrs = ctx.get("object", {}).get("attributes", {})
        anchor_id = ctx.get("object", {}).get("ocel_id")

        name = next(
            (
                str(v)
                for k, v in attrs.items()
                if k.lower() in ("name", "title", "product_name", "label")
                and v not in (None, "")
            ),
            None,
        )

        # In the order-management dataset, product names are encoded as ocel_id.
        if not name and (row.get("object_type") or "").lower() in ("products", "product"):
            name = str(anchor_id) if anchor_id else None

        ctx["anchor_entity"] = {
            "name": name,
            "object_id": anchor_id,
            "object_type": row.get("object_type"),
            "missing_attribute": row.get("attribute") or row.get("attribute_name"),
        }
        ctx["data_semantics"] = {
            "encoding": "delta",
            "explanation": (
                "Each object-type table stores the initial object state (ocel_changed_field IS NULL) "
                "plus one row per attribute change. NULL values in change rows are normal — "
                "only the changed attribute is required. The violation you are repairing is "
                "from an initial-state row, so the NULL IS genuinely missing."
            ),
        }

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_value")
        if new is None:
            # Model ignored the instruction — treat as unrecoverable for this row
            return ActionResult.decline("LLM returned null despite instructions to always guess")
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
