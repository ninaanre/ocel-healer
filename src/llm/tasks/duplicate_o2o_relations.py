from src.llm.actions import ActionResult
from src.llm.schemas import TaskOutput
from src.llm.tasks._base import ResolutionTask


class DuplicateO2ORelations(ResolutionTask):
    """Duplicate rows in `object_object` sharing the same
    (source, target, qualifier) triple.

    Deterministic dedupe: the fix always removes all-but-one row via
    ``DELETE … WHERE (source, target, qualifier) = ? AND rowid NOT IN
    (SELECT MIN(rowid) …)``. `min_confidence=0.0` and `parse_payload`
    ignores the LLM payload — the delete branch fires unconditionally.
    Prompt fields are kept short but populated so the fallback LLM path
    renders sensibly if it's ever invoked.
    """

    issue_key = "duplicate_o2o_relations"
    family = "duplicate"
    OutputModel = TaskOutput
    min_confidence = 0.0  # deterministic path ignores LLM confidence

    TASK = """
        Rows in `object_object` share an identical (source, target,
        qualifier) triple. The fix deletes duplicates deterministically —
        no decision from the LLM is required.
    """

    INPUTS = """
        - violation.ocel_source_id   source object id (shared by dupes)
        - violation.ocel_target_id   target object id (shared by dupes)
        - violation.ocel_qualifier   relationship qualifier (shared)
        - violation.count            number of duplicate rows
    """

    METHOD = """
        Nothing to decide — the applier keeps one row (MIN rowid) and
        deletes the rest.
    """

    EXAMPLES = ""

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        return ActionResult.delete(
            target_table="object_object",
            target_pk={
                "ocel_source_id": row["ocel_source_id"],
                "ocel_target_id": row["ocel_target_id"],
                "ocel_qualifier": row["ocel_qualifier"],
            },
            reason=(
                f"Duplicate O2O relation "
                f"({row['ocel_source_id']} → {row['ocel_target_id']}, "
                f"qualifier={row['ocel_qualifier']!r}); keeping one row "
                f"(MIN rowid), deleting the rest."
            ),
        )
