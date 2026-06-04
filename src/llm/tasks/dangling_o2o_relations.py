from src.llm.actions import ActionResult, relation_swap_target
from src.llm.tasks._base import IssueTask


_O2O_SIDES = {
    "source": {"column": "ocel_source_id", "pk": ["ocel_target_id", "ocel_qualifier"]},
    "target": {"column": "ocel_target_id", "pk": ["ocel_source_id", "ocel_qualifier"]},
}


class DanglingO2ORelations(IssueTask):
    issue_key = "dangling_o2o_relations"

    PROMPT = """\
        An object_object relation references an object that does not exist in the `object` table.
        Pick the most likely intended referent from `candidate_objects` (each entry has `ocel_id`
        and `ocel_type`), or null if no candidate is a plausible match.

        Reasoning recipe:
          1. `violation.missing_side` tells you which end is missing ('source' or 'target').
          2. The known end is described in `object` (its ocel_id, type, and attributes).
            Use its type and attributes plus `violation.ocel_qualifier` to narrow candidates --
            the qualifier names the relationship and often implies a plausible target type
            (e.g. 'belongs_to', 'part_of', 'parent').
          3. Filter `candidate_objects` to those whose `ocel_type` is plausible for that qualifier
            and the known end's type. Among the survivors, prefer ids that share a naming prefix
            or convention with the known end.
          4. Return the single best `ocel_id` -- a verbatim value from `candidate_objects`,
            never a fabrication. Return null only when no candidate is plausible, and put the specific
            reason in `rationale`.

        Return JSON: {"inferred_referent": str|null, "rationale": str, "confidence": number}.
    """

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # The known side is whichever end isn't missing.
        if row.get("missing_side") == "source":
            return row.get("ocel_target_id"), row.get("target_type")
        return row.get("ocel_source_id"), row.get("source_type")

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        ctx["candidate_objects"] = [
            {"ocel_id": r[0], "ocel_type": r[1]}
            for r in conn.execute(
                "SELECT ocel_id, ocel_type FROM object "
                "WHERE ocel_id IS NOT NULL LIMIT 200"
            ).fetchall()
        ]

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_referent")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer a referent: {reason}")
        target = relation_swap_target(row, table="object_object", sides=_O2O_SIDES)
        if target is None:
            return ActionResult.unrouted("Both ends of the O2O relation missing; cannot patch.")
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return relation_swap_target(row, table="object_object", sides=_O2O_SIDES)
