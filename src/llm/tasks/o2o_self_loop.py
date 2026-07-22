from src.llm.actions import ActionResult
from src.llm.schemas import TaskOutput
from src.llm.tasks._base import ResolutionTask


class O2OSelfLoop(ResolutionTask):
    """Rows in `object_object` where source and target are the same
    object id — an object linked to itself under a qualifier, which is
    structurally illegal for O2O.

    Deterministic fix: delete the offending row(s) outright via the
    ``delete_all`` action kind. No dedupe survivor is kept; a self-loop
    row simply shouldn't exist.
    """

    issue_key = "o2o_self_loop"
    family = "duplicate"
    OutputModel = TaskOutput
    min_confidence = 0.0

    TASK = """
        An `object_object` row references the same object as both
        source and target. The fix deletes the row — no decision from
        the LLM is required.
    """

    INPUTS = """
        - violation.ocel_source_id   the object id (same as target)
        - violation.ocel_target_id   the object id (same as source)
        - violation.ocel_qualifier   qualifier on the self-referential row
    """

    METHOD = """
        Nothing to decide — the applier deletes the offending row.
    """

    EXAMPLES = ""

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        return ActionResult.delete_all(
            target_table="object_object",
            target_pk={
                "ocel_source_id": row["ocel_source_id"],
                "ocel_target_id": row["ocel_target_id"],
                "ocel_qualifier": row["ocel_qualifier"],
            },
            reason=(
                f"O2O self-loop on object {row['ocel_source_id']!r} "
                f"(qualifier={row['ocel_qualifier']!r}); deleting the row."
            ),
        )
