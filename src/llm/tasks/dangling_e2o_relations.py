from src.llm.actions import ActionResult, relation_swap_target
from src.llm.tasks._base import IssueTask


_E2O_SIDES = {
    "event": {"column": "ocel_event_id", "pk": ["ocel_object_id", "ocel_qualifier"]},
    "object": {"column": "ocel_object_id", "pk": ["ocel_event_id", "ocel_qualifier"]},
}


class DanglingE2ORelations(IssueTask):
    issue_key = "dangling_e2o_relations"

    PROMPT = """\
        An event_object relation references an event or object that does not exist.
        Pick the most likely intended referent from the candidate list -- `candidate_objects`
        if `violation.missing_side` is 'object', otherwise `candidate_events` -- or null.
        Use the known end's type, attributes (in `object`), and `violation.ocel_qualifier` to narrow candidates.
        Return a verbatim id from the list; never invent one. Return null only when no candidate is plausible,
        and put the specific reason in `rationale`.

        Return JSON: {"inferred_referent": str|null, "rationale": str, "confidence": number}.
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
