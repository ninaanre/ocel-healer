from src.llm.actions import ActionResult
from src.llm.tasks._base import ResolutionTask


class MissingObjectType(ResolutionTask):
    issue_key = "missing_object_type"

    PROMPT = """\
        <task>
        An object row in the `object` table has a NULL or empty `ocel_type`.
        Infer the most likely type for this object from its events and attributes.
        </task>

        <inputs>
          - violation.ocel_id        the id of the object whose type is missing
          - object.attributes        the anchor object's existing attributes (may be empty)
          - events                   up to 8 events touching this object, each with
                                     the qualifier under which the event references it
          - candidate_types          the closed list of valid object types — pick
                                     exactly one of these strings
        </inputs>

        <method>
          1. Activity names + qualifiers in `events` are the strongest signal
             (e.g. activity 'place_order' with qualifier 'customer' implies the
             customer type). Look there first.
          2. Attribute names and shapes in `object.attributes` are the next
             signal (e.g. `email`, `country` → customer; `sku`, `price` → product).
          3. Pick exactly one value from `candidate_types`. If multiple fit,
             choose the one whose name best matches the activities/qualifiers seen.
        </method>

        <example>
          events=[{activity:'place_order', qualifier:'customer'}, {activity:'ship_order', qualifier:'customer'}]
          candidate_types=['customer', 'order', 'product']
          → {"inferred_type": "customer", "rationale": "qualifier 'customer' on both events directly names the type", "confidence": 0.95}
        </example>

        <output>
        JSON: {"inferred_type": str|null, "rationale": str, "confidence": number}
        </output>
    """

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_type")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer an object type: {reason}")
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
