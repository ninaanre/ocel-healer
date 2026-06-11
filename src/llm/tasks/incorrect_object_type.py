from src.llm.actions import ActionResult
from src.llm.tasks._base import IssueTask


class IncorrectObjectType(IssueTask):
    issue_key = "incorrect_object_type"

    PROMPT = """\
        <task>
        An object row in the `object` table has a non-empty `ocel_type`, but
        that type may be wrong for the object. Decide whether the current
        type is consistent with the events touching the object and the
        attributes carried on the object's per-type row. Only flag a mismatch
        when the evidence clearly points to a different type.
        </task>

        <inputs>
          - violation.ocel_id        the id of the object whose type is being checked
          - violation.ocel_type      the object's CURRENT type (the value to validate)
          - object.attributes        the anchor object's existing attributes (may be empty)
          - events                   up to 8 events touching this object, each with
                                     the qualifier under which the event references it
          - candidate_types          the closed list of valid object types
        </inputs>

        <method>
          1. Compare the current `ocel_type` against the strongest signals:
             - Activity names + qualifiers in `events` (e.g. activity 'place_order'
               with qualifier 'customer' implies the customer type).
             - Attribute names/shapes in `object.attributes` (e.g. `email`,
               `country` → customer; `sku`, `price` → product).
          2. If the current type is consistent with these signals, OR the
             evidence is weak/ambiguous, return `inferred_type: null`.
             Defaulting to null is the safe choice -- only override when
             contradicting evidence is strong.
          3. Only when the evidence clearly contradicts the current type,
             return one value from `candidate_types` that fits the evidence
             better. Never invent a type that is not in `candidate_types`.
          4. Never return the current `ocel_type` value as `inferred_type`
             -- if you agree with it, return null.
        </method>

        <example>
          violation={ocel_id:'O42', ocel_type:'product'}
          events=[{activity:'place_order', qualifier:'customer'}, {activity:'ship_order', qualifier:'customer'}]
          object.attributes={email:'a@b.com', country:'DE'}
          candidate_types=['customer', 'order', 'product']
          → {"inferred_type": "customer", "rationale": "qualifier 'customer' on both events and email/country attributes contradict the current 'product' tag", "confidence": 0.95}
        </example>

        <example>
          violation={ocel_id:'O7', ocel_type:'order'}
          events=[{activity:'place_order', qualifier:'order'}]
          candidate_types=['customer', 'order', 'product']
          → {"inferred_type": null, "rationale": "events qualify this object as 'order', matching the current type", "confidence": 0.95}
        </example>

        <output>
        JSON: {"inferred_type": str|null, "rationale": str, "confidence": number}
        </output>
    """

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_type")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(
                f"LLM agrees the existing type is correct (or evidence too weak): {reason}"
            )
        if new == row.get("ocel_type"):
            return ActionResult.decline(
                "LLM returned the existing type -- no change needed."
            )
        return ActionResult.update(
            target_table="object",
            target_pk={"ocel_id": row["ocel_id"]},
            column="ocel_type",
            old_value=row.get("ocel_type"),
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return {
            "target_table": "object",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
