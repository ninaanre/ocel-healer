from src.llm.actions import ActionResult, relation_swap_target
from src.llm.tasks._base import IssueTask


_E2O_SIDES = {
    "event": {"column": "ocel_event_id", "pk": ["ocel_object_id", "ocel_qualifier"]},
    "object": {"column": "ocel_object_id", "pk": ["ocel_event_id", "ocel_qualifier"]},
}


class DanglingE2ORelationship(IssueTask):
    issue_key = "dangling_e2o_relationship"

    PROMPT = """\
        <task>
        An `event_object` relation references an event or object that does not
        exist. Pick the most likely intended referent for the missing side.
        </task>

        <inputs>
          - violation.missing_side       'object' or 'event' — which end is missing
          - violation.ocel_qualifier     names the relationship (often implies the
                                         expected type of the missing side)
          - object                       the known end's id, type, and attributes
          - events                       up to 8 events touching the known object
                                         (limited use here, but check for context)
          - candidate_objects            up to 200 {ocel_id, ocel_type} — present
                                         when missing_side='object'
          - candidate_events             up to 200 {ocel_id, ocel_type} — present
                                         when missing_side='event'
        </inputs>

        <method>
          1. Read `violation.ocel_qualifier`; it usually names a role (e.g.
             'customer', 'item') that pins the expected type of the missing end.
          2. Filter the candidate list to entries whose `ocel_type` matches that
             expected type.
          3. Among the survivors, prefer ids that share a naming prefix or
             convention with the known end's id.
          4. Return a verbatim `ocel_id` from the candidate list.
        </method>

        <example>
          violation.missing_side='object', violation.ocel_qualifier='customer'
          object={ocel_id:'e-42', ocel_type:'place_order'}
          candidate_objects=[{ocel_id:'c-1', ocel_type:'customer'}, {ocel_id:'p-9', ocel_type:'product'}]
          → {"inferred_referent": "c-1", "rationale": "qualifier 'customer' selects ocel_type='customer'; only c-1 matches", "confidence": 0.9}
        </example>

        <output>
        JSON: {"inferred_referent": str|null, "rationale": str, "confidence": number}
        </output>
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        side = row.get("missing_side")
        if side == "object":
            ctx["candidate_objects"] = [
                {"ocel_id": r[0], "ocel_type": r[1]}
                for r in conn.execute(
                    "SELECT ocel_id, ocel_type FROM object "
                    "WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]
        else:
            ctx["candidate_events"] = [
                {"ocel_id": r[0], "ocel_type": r[1]}
                for r in conn.execute(
                    "SELECT ocel_id, ocel_type FROM event "
                    "WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_referent")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer a referent: {reason}")
        target = relation_swap_target(row, table="event_object", sides=_E2O_SIDES)
        if target is None:
            return ActionResult.unrouted("Both ends of the E2O relation missing; cannot patch.")
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return relation_swap_target(row, table="event_object", sides=_E2O_SIDES)
