from src.llm.actions import ActionResult
from src.llm.schemas import TaskOutput
from src.llm.tasks._base import ResolutionTask


class DuplicateE2ORelations(ResolutionTask):
    """Duplicate rows in `event_object` sharing the same
    (event, object, qualifier) triple. Event-side mirror of
    :class:`DuplicateO2ORelations`.

    Deterministic dedupe: keep one row (MIN rowid), delete the rest.
    """

    issue_key = "duplicate_e2o_relations"
    family = "duplicate"
    OutputModel = TaskOutput
    min_confidence = 0.0

    TASK = """
        Rows in `event_object` share an identical (event, object,
        qualifier) triple. The fix deletes duplicates deterministically —
        no decision from the LLM is required.
    """

    INPUTS = """
        - violation.ocel_event_id    event id (shared by dupes)
        - violation.ocel_object_id   object id (shared by dupes)
        - violation.ocel_qualifier   relationship qualifier (shared)
        - violation.count            number of duplicate rows
    """

    METHOD = """
        Nothing to decide — the applier keeps one row (MIN rowid) and
        deletes the rest.
    """

    EXAMPLES = ""

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # No natural single-object anchor — the base default picks
        # ocel_object_id, which is fine but not used by parse_payload.
        return row.get("ocel_object_id"), None

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        return ActionResult.delete(
            target_table="event_object",
            target_pk={
                "ocel_event_id": row["ocel_event_id"],
                "ocel_object_id": row["ocel_object_id"],
                "ocel_qualifier": row["ocel_qualifier"],
            },
            reason=(
                f"Duplicate E2O relation "
                f"({row['ocel_event_id']} ↔ {row['ocel_object_id']}, "
                f"qualifier={row['ocel_qualifier']!r}); keeping one row "
                f"(MIN rowid), deleting the rest."
            ),
        )
