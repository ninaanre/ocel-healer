from src.llm.actions import ActionResult
from src.llm.tasks._base import IssueTask


class DuplicateObjectIds(IssueTask):
    issue_key = "duplicate_object_ids"

    PROMPT = """\
        Multiple rows in the `object` table share the same `ocel_id`. The duplicated rows are
        listed in `duplicate_rows` (each entry has `ocel_id` and `ocel_type`). Decide which
        single row is canonical and which should be deleted.

        Reasoning recipe:
          1. Prefer rows with a non-null, non-empty `ocel_type`. A row with an explicit type is
            almost always the canonical one.
          2. If multiple rows have a type, prefer the one whose type is consistent with the
            activities in `events` (events touching this ocel_id are listed under `events`).
          3. The canonical id itself is the shared value -- the choice is really about which
            TYPE to keep. Set `canonical_id` to that ocel_id, and list the duplicate rows that should
            be removed in `ids_to_delete` using their (ocel_id, ocel_type) tuple style is fine but a
            list of ocel_ids is what gets used by the suggested DELETE.
          4. Put the specific reason for your pick (and for any rejected alternatives) in `rationale`.
            Never invent an ocel_id that is not in `duplicate_rows`.

        Return JSON: {"canonical_id": str, "ids_to_delete": [str], "rationale": str, "confidence": number}.
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
