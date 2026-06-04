from src.llm.actions import ActionResult
from src.llm.tasks._base import IssueTask


class MissingObjectTypes(IssueTask):
    issue_key = "missing_object_types"

    PROMPT = """\
        An object row in the `object` table has a NULL or empty `ocel_type`. Infer the most likely type for this object.

        Reasoning recipe:
          1. The object's id is in `violation.ocel_id`. Its existing attributes (if any were stored
            in a per-type table under that id) are in `object.attributes`.
          2. `events` lists up to 8 events touching this object together with the qualifier under 
            which the event references it. Activity names and qualifiers (e.g. 'place_order' +
            'customer' strongly imply Customer) are the strongest signal.
          3. Pick exactly one value from `candidate_types` -- a verbatim string, never a fabrication.
            If multiple candidates fit, pick the one whose name best matches the activities/qualifiers seen.
          4. Return null only when no candidate is a plausible fit, and put the specific reason in
            `rationale` (e.g. 'no events touch this object and attribute set is empty -- no signal to disambiguate').

        Return JSON: {"inferred_type": str|null, "rationale": str, "confidence": number}.
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
