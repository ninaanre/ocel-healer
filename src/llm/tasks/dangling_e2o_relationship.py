from src.llm.actions import ActionResult, relation_swap_target
from src.llm.dataset_hints import DatasetHints
from src.llm.sampling import sample_candidates
from src.llm.schemas import InferredReferentOutput
from src.llm.tasks._base import ResolutionTask


_E2O_SIDES = {
    "event": {"column": "ocel_event_id", "pk": ["ocel_object_id", "ocel_qualifier"]},
    "object": {"column": "ocel_object_id", "pk": ["ocel_event_id", "ocel_qualifier"]},
}


class DanglingE2ORelationship(ResolutionTask):
    issue_key = "dangling_e2o_relationship"
    family = "relation"
    OutputModel = InferredReferentOutput

    TASK = """
        An `event_object` relation references an event or object that does
        not exist. Pick the most likely intended referent for the missing
        side from the candidate list.
    """

    INPUTS = """
        - violation.missing_side       'object' or 'event' — which end is missing
        - violation.ocel_qualifier     names the relationship (usually
                                       implies the expected type of the
                                       missing side)
        - object                       the known end's id, type, attributes
        - events                       up to 8 events touching the known
                                       object (context only)
        - candidate_objects            up to 50 {ocel_id, ocel_type} — set
                                       when missing_side='object', pre-filtered
                                       by qualifier when possible
        - candidate_events             up to 50 {ocel_id, ocel_type} — set
                                       when missing_side='event'
    """

    METHOD = """
        1. Read `violation.ocel_qualifier` — it usually names a role that
           pins the expected `ocel_type` of the missing side (e.g.
           qualifier `customer` selects `ocel_type='customer'`).
        2. Filter the candidate list to entries whose `ocel_type` matches.
        3. Among survivors, prefer ids that share a naming prefix or
           convention with the known end's id.
        4. Return one `ocel_id` from the candidate list verbatim, or null
           when no candidate plausibly matches.
    """

    EXAMPLES = """
        violation.missing_side = 'object'
        violation.ocel_qualifier = 'customer'
        object = {ocel_id: 'e-42', ocel_type: 'place_order'}
        candidate_objects = [{ocel_id: 'c-1', ocel_type: 'customer'},
                             {ocel_id: 'p-9', ocel_type: 'product'}]
        → {"inferred_referent": "c-1",
           "rationale": "qualifier 'customer' selects ocel_type='customer'; only c-1 matches",
           "confidence": 0.9}
    """

    def extend_context(self, conn, ctx: dict, row: dict, *, hints: DatasetHints) -> None:
        anchor_id, _ = self.anchor(row)
        expected_type = row.get("ocel_qualifier")
        side = row.get("missing_side")
        if side == "object":
            ctx["candidate_objects"] = sample_candidates(
                conn,
                anchor_id=anchor_id,
                expected_type=expected_type,
                kind="object",
                k=50,
            )
        else:
            ctx["candidate_events"] = sample_candidates(
                conn,
                anchor_id=anchor_id,
                expected_type=None,  # events don't share the object-type registry
                kind="event",
                k=50,
            )

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
