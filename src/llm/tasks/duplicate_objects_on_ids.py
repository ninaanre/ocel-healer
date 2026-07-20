from src.llm.actions import ActionResult
from src.llm.schemas import DuplicateResolutionOutput
from src.llm.tasks._base import ResolutionTask


class DuplicateObjectsOnIds(ResolutionTask):
    issue_key = "duplicate_objects_on_ids"
    family = "duplicate"
    OutputModel = DuplicateResolutionOutput
    min_confidence = 0.0  # deterministic path ignores LLM confidence

    TASK = """
        Multiple rows in the `object` table share the same `ocel_id` but
        differ on `ocel_type`. Decide which single row is canonical and
        which rows should be deleted. The shared id stays — only one type
        survives.
    """

    INPUTS = """
        - violation.ocel_ids     comma-separated list of duplicate ocel_ids
                                 (typically the same id repeated)
        - duplicate_rows         list of {ocel_id, ocel_type} for each
                                 duplicate row
        - object                 the anchor record (one of the duplicates)
        - events                 up to 8 events touching this ocel_id;
                                 their activities and qualifiers indicate
                                 the right type
    """

    METHOD = """
        1. Prefer rows with a non-null, non-empty `ocel_type`. A row with
           an explicit type is almost always canonical.
        2. If several rows carry a type, prefer the one whose type is
           consistent with the activities and qualifiers in `events`.
        3. Set `canonical_id` to the shared `ocel_id` (the value all
           duplicates share) — that id stays in the table.
        4. Set `ids_to_delete` to a flat list of `ocel_id` strings, one
           entry per row to remove. Because all duplicates share the same
           id, entries are typically identical strings — that's expected;
           the downstream `DELETE FROM object WHERE ocel_id IN (...)` uses
           them as-is.
    """

    EXAMPLES = """
        duplicate_rows = [{ocel_id: 'o-1', ocel_type: 'customer'},
                          {ocel_id: 'o-1', ocel_type: null}]
        events = [{activity: 'place_order', qualifier: 'customer'}]
        → {"canonical_id": "o-1",
           "ids_to_delete": ["o-1"],
           "rationale": "kept the row with ocel_type='customer'; events qualify this id as a customer. Marked the NULL-type row for removal.",
           "confidence": 0.9}
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        dup_ids = row.get("ocel_ids", "")
        ids = [i.strip() for i in str(dup_ids).split(",") if i.strip()]
        if not ids:
            return
        placeholders = ", ".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT ocel_id, ocel_type FROM object WHERE ocel_id IN ({placeholders})",
            ids,
        ).fetchall()
        ctx["duplicate_rows"] = [{"ocel_id": r[0], "ocel_type": r[1]} for r in rows]

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        ocel_id = row.get("ocel_id")
        # ocel_types is the de-duped, comma-joined list from the detector.
        unique_types = [
            t.strip() for t in (row.get("ocel_types") or "").split(",")
            if t.strip() and t.strip().lower() != "none"
        ]

        # Pure N6(a): all duplicates share the same type → deterministic delete.
        if ocel_id and len(unique_types) == 1:
            return ActionResult.delete(
                target_table="object",
                target_pk={"ocel_id": ocel_id},
                reason=(
                    f"All duplicate rows for '{ocel_id}' share type '{unique_types[0]}'. "
                    "Keeping one row (MIN rowid), deleting the rest."
                ),
            )

        # Types differ → surface LLM suggestion as unrouted noop for manual review.
        canonical = payload.get("canonical_id")
        rationale = (payload.get("rationale") or "").strip()
        if not canonical:
            return ActionResult.unrouted(
                rationale or "LLM could not determine a canonical object ID",
                target_table="object",
            )
        ids_to_delete = payload.get("ids_to_delete") or []
        ids_str = ", ".join(repr(i) for i in ids_to_delete)
        return ActionResult.unrouted(
            f"{rationale}  |  Canonical ID: {canonical!r}.  "
            f"Suggested DELETE: DELETE FROM object WHERE ocel_id IN ({ids_str}).",
            target_table="object",
        )
