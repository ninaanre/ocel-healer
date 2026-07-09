from src.llm.actions import ActionResult, relation_swap_target
from src.llm.dataset_hints import DatasetHints
from src.llm.sampling import sample_candidates
from src.llm.schemas import InferredReferentOutput
from src.llm.tasks._base import ResolutionTask


_O2O_SIDES = {
    "source": {"column": "ocel_source_id", "pk": ["ocel_target_id", "ocel_qualifier"]},
    "target": {"column": "ocel_target_id", "pk": ["ocel_source_id", "ocel_qualifier"]},
}


class DanglingO2ORelationship(ResolutionTask):
    issue_key = "dangling_o2o_relationship"
    family = "relation"
    OutputModel = InferredReferentOutput

    TASK = """
        An `object_object` relation references an object that does not exist
        in the `object` table. Pick the most likely intended referent for
        the missing end from the candidate list.
    """

    INPUTS = """
        - violation.missing_side    'source' or 'target' — which end is missing
        - violation.ocel_qualifier  names the relationship (often implies
                                    the type of the missing end, e.g.
                                    `belongs_to`, `part_of`, `parent`)
        - object                    the known end's id, type, attributes
        - candidate_objects         up to 50 {ocel_id, ocel_type} drawn
                                    from the `object` table
    """

    METHOD = """
        1. Use `violation.ocel_qualifier` together with the known end's
           type to infer the expected `ocel_type` of the missing end
           (e.g. an order that `belongs_to` implies a customer).
        2. Filter `candidate_objects` to entries whose type matches.
        3. Among survivors, prefer ids that share a naming prefix with
           the known end (e.g. `o-42` pairs with `c-42`).
        4. Return one `ocel_id` from `candidate_objects` verbatim, or null
           when no candidate plausibly matches.
    """

    EXAMPLES = """
        violation.missing_side = 'target'
        violation.ocel_qualifier = 'belongs_to'
        object = {ocel_id: 'o-42', ocel_type: 'order'}
        candidate_objects = [{ocel_id: 'c-42', ocel_type: 'customer'},
                             {ocel_id: 'p-1',  ocel_type: 'product'}]
        → {"inferred_referent": "c-42",
           "rationale": "'belongs_to' from an order implies customer; c-42 matches and shares the '-42' suffix",
           "confidence": 0.85}
    """

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # The known side is whichever end isn't missing.
        if row.get("missing_side") == "source":
            return row.get("ocel_target_id"), row.get("target_type")
        return row.get("ocel_source_id"), row.get("source_type")

    def extend_context(self, conn, ctx: dict, row: dict, *, hints: DatasetHints) -> None:
        anchor_id, _ = self.anchor(row)
        # We don't know the expected type from the qualifier alone here
        # (relations like 'belongs_to' don't name the target type), so
        # fall back to the random-plus-prefix mix.
        ctx["candidate_objects"] = sample_candidates(
            conn,
            anchor_id=anchor_id,
            expected_type=None,
            kind="object",
            k=50,
        )

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
