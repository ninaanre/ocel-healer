from src.llm.actions import ActionResult
from src.llm.tasks._base import ResolutionTask


class DuplicateObjectsOnIds(ResolutionTask):
    issue_key = "duplicate_objects_on_ids"

    PROMPT = """\
        <task>
        Multiple rows in the `object` table share the same `ocel_id` but differ
        on `ocel_type`. Decide which single row is canonical and which rows
        should be deleted. The shared id stays — only one type survives.
        </task>

        <inputs>
          - violation.ocel_ids       comma-separated list of duplicate ocel_ids
                                     (typically the same id repeated)
          - duplicate_rows           list of {ocel_id, ocel_type} for each duplicate row
          - object                   the anchor record (one of the duplicates)
          - events                   up to 8 events touching this ocel_id; their
                                     activities and qualifiers indicate the right type
        </inputs>

        <method>
          1. Prefer rows with a non-null, non-empty `ocel_type`. A row with an
             explicit type is almost always the canonical one.
          2. If multiple rows have a type, prefer the one whose type is
             consistent with the activities and qualifiers in `events`.
          3. Set `canonical_id` to the shared `ocel_id` (the value all duplicates
             share) — that id stays in the table.
          4. Set `ids_to_delete` to a flat list of `ocel_id` strings, one entry
             per row that should be removed. Because all duplicates share the
             same id, these entries are typically identical strings — that's
             expected; the downstream `DELETE FROM object WHERE ocel_id IN (...)`
             uses them as-is.
        </method>

        <example>
          duplicate_rows=[{ocel_id:'o-1', ocel_type:'customer'}, {ocel_id:'o-1', ocel_type:null}]
          events=[{activity:'place_order', qualifier:'customer'}]
          → {"canonical_id": "o-1",
             "ids_to_delete": ["o-1"],
             "rationale": "kept the row with ocel_type='customer'; events qualify this id as a customer. Marked the NULL-type row for removal.",
             "confidence": 0.9}
        </example>

        <output>
        JSON: {"canonical_id": str, "ids_to_delete": [str], "rationale": str, "confidence": number}
        </output>
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
        # No clean single-column override target: a duplicate-ids resolution
        # is a multi-row DELETE, not an UPDATE. The SQL is surfaced in the
        # rationale so the user can run it manually.
        canonical = payload.get("canonical_id")
        rationale = (payload.get("rationale") or "").strip()
        if not canonical:
            reason = rationale or "no reason provided"
            return ActionResult.unrouted(
                f"LLM could not determine a canonical object ID: {reason}"
            )
        ids_to_delete = payload.get("ids_to_delete") or []
        ids_str = ", ".join(repr(i) for i in ids_to_delete)
        return ActionResult.unrouted(
            f"{rationale}  |  Canonical ID: {canonical!r}.  "
            f"Suggested DELETE: DELETE FROM object WHERE ocel_id IN ({ids_str}).",
            target_table="object",
        )
