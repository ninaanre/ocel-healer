from src.llm.actions import ActionResult, relation_swap_target
from src.llm.tasks._base import IssueTask


_O2O_SIDES = {
    "source": {"column": "ocel_source_id", "pk": ["ocel_target_id", "ocel_qualifier"]},
    "target": {"column": "ocel_target_id", "pk": ["ocel_source_id", "ocel_qualifier"]},
}


class DanglingO2ORelationship(IssueTask):
    issue_key = "dangling_o2o_relationship"

    PROMPT = """\
        <task>
        An `object_object` relation references an object that does not exist
        in the `object` table. Pick the most likely intended referent for the
        missing end.
        </task>

        <inputs>
          - violation.missing_side    'source' or 'target' — which end is missing
          - violation.ocel_qualifier  names the relationship (often implies a
                                      plausible target type, e.g. 'belongs_to',
                                      'part_of', 'parent')
          - object                    the known end's id, type, and attributes
          - candidate_objects         up to 200 {ocel_id, ocel_type} drawn from
                                      the `object` table
        </inputs>

        <method>
          1. From `violation.ocel_qualifier` and the known end's type, infer
             the expected `ocel_type` of the missing end (e.g. an order
             'belongs_to' a customer).
          2. Filter `candidate_objects` to entries whose `ocel_type` matches
             that expected type.
          3. Among the survivors, prefer ids that share a naming prefix or
             convention with the known end (e.g. `o-42` paired with `c-42`).
          4. Return a single verbatim `ocel_id` from `candidate_objects`.
        </method>

        <example>
          violation.missing_side='target', violation.ocel_qualifier='belongs_to'
          object={ocel_id:'o-42', ocel_type:'order'}
          candidate_objects=[{ocel_id:'c-42', ocel_type:'customer'}, {ocel_id:'p-1', ocel_type:'product'}]
          → {"inferred_referent": "c-42", "rationale": "'belongs_to' from an order implies customer; c-42 matches and shares the '-42' suffix", "confidence": 0.85}
        </example>

        <output>
        JSON: {"inferred_referent": str|null, "rationale": str, "confidence": number}
        </output>
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
