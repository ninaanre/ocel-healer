from src.llm.actions import ActionResult
from src.llm.schemas import InferredTypeOutput
from src.llm.tasks._base import ResolutionTask


class MissingObjectType(ResolutionTask):
    issue_key = "missing_object_type"
    family = "type"
    OutputModel = InferredTypeOutput

    TASK = """
        An object row in the `object` table has a NULL or empty `ocel_type`.
        Infer the most likely type for this object from its id, events, and
        attributes. Pick exactly one value from `candidate_types`.
    """

    INPUTS = """
        - violation.ocel_id   the id of the object whose type is missing
        - object.attributes   the anchor object's existing attributes (may be empty)
        - events              up to 8 events touching this object, each with the
                              qualifier under which the event references it
        - candidate_types     the closed list of valid object types — the
                              answer must be one of these strings
    """

    METHOD = """
        1. Read `violation.ocel_id` first — id keywords or human names are
           often the single strongest signal (e.g. "Alessandro Berti" → a
           person, `p-123456` → a package, `i-88001` → an item).
        2. Read the qualifiers in `events`. The qualifier names the ROLE
           the object plays, not the event's subject. Qualifiers like
           `shipper`, `handler`, `picker` mean this object IS the
           shipper/handler/picker (a person), not the package being handled.
        3. Attribute names in `object.attributes` reinforce: `email` /
           `country` → customer; `role` / `department` → employee;
           `price` / `weight` → product or item.
        4. If several types fit, prefer the one whose name best matches the
           id and qualifiers.
    """

    EXAMPLES = """
        violation.ocel_id = 'Alessandro Berti'
        events = [{activity: 'send package', qualifier: 'shipper'},
                  {activity: 'deliver package', qualifier: 'shipper'}]
        candidate_types = ['employees', 'customers', 'packages', 'items', 'orders']
        → {"inferred_type": "employees",
           "rationale": "'Alessandro Berti' is a human name; qualifier 'shipper' is the role this person plays — they ship packages, they are not a package",
           "confidence": 0.95}
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
