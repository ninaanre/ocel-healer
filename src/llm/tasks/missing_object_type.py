from src.llm.actions import ActionResult
from src.llm.tasks._base import ResolutionTask


class MissingObjectType(ResolutionTask):
    issue_key = "missing_object_type"

    PROMPT = """\
        <task>
        An object row in the `object` table has a NULL or empty `ocel_type`.
        Infer the most likely type for this object from its id, events and attributes.
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
          0. Read `violation.ocel_id` carefully — this is often the single strongest signal.
             - A human full name (e.g. "Alessandro Berti", "Jane Smith") → employee or customer,
               NEVER a physical object type such as package, item, or order.
             - An identifier like "p-123456" → package; "i-880001" → item; "o-990001" → order.
          1. Read the qualifiers in `events`. The qualifier describes the ROLE this object
             plays in the event — NOT the subject of the event.
             Example: qualifier 'shipper' on activity 'send package' means this object IS the
             shipper (a person), NOT the package being shipped.
             Qualifiers like 'shipper', 'handler', 'picker', 'worker' → person type (employee).
             Qualifier 'customer' → customer type.
          2. Attribute names in `object.attributes` reinforce: `email`/`country` → customer;
             `role`/`department` → employee; `price`/`weight` → product or item.
          3. Pick exactly one value from `candidate_types`. If multiple fit,
             choose the one whose name best matches the id + qualifiers seen.
        </method>

        <example>
          violation.ocel_id='Alessandro Berti'
          events=[{activity:'send package', qualifier:'shipper'}, {activity:'deliver package', qualifier:'shipper'}]
          candidate_types=['employees', 'customers', 'packages', 'items', 'orders']
          → {"inferred_type": "employees", "rationale": "'Alessandro Berti' is a human name; qualifier 'shipper' is the role this person plays — they ship packages, they are not a package", "confidence": 0.95}
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
