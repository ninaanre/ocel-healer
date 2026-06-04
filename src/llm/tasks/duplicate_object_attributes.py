from src.detection.error_detection import _column_info
from src.llm.actions import ActionResult, object_attribute_target
from src.llm.sql_utils import quote, table_for_type
from src.llm.tasks._base import IssueTask


class DuplicateObjectAttributes(IssueTask):
    issue_key = "duplicate_object_attributes"

    PROMPT = """\
        Two or more objects of the same type share an identical attribute fingerprint but
        have different `ocel_id`s. The duplicated values are listed in `duplicate_attribute_values`;
        the anchor object's full attribute row is in `object_attributes`.

        Reasoning recipe:
          1. Look at `violation.attribute_name` (or `violation.attribute`) to know which column has the conflicting value.
          2. Compare the candidate values in `duplicate_attribute_values`. Prefer the one that
            matches the formatting/casing/units of the anchor object's other attributes in `object_attributes`.
          3. Use `events` (activities touching the anchor object) as a tiebreaker -- e.g. an
            activity name often implies the correct value (currency, country, category).
          4. Return null only when the candidate values are equally plausible and no other
            attribute or event narrows them; put the specific reason in `rationale`. Never invent a value
            that is not in `duplicate_attribute_values`.

        Return JSON: {"canonical_value": any, "rationale": str, "confidence": number}.
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        dup_vals = row.get("attribute_values", "")
        ctx["duplicate_attribute_values"] = [
            v.strip() for v in str(dup_vals).split(",") if v.strip()
        ]
        # Also provide the full attribute row for the anchor object.
        anchor_id, anchor_type = self.anchor(row)
        table = table_for_type(conn, anchor_type)
        if not table or not anchor_id:
            return
        cols = [c for c, _ in _column_info(conn, table)]
        if not cols:
            return
        quoted = ", ".join(quote(c) for c in cols)
        r = conn.execute(
            f"SELECT {quoted} FROM {quote(table)} WHERE ocel_id = ? LIMIT 1",
            (anchor_id,),
        ).fetchone()
        if r:
            ctx["object_attributes"] = dict(zip(cols, r))

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        canonical_val = payload.get("canonical_value")
        if canonical_val is None:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(
                f"LLM could not determine a canonical attribute value: {reason}"
            )
        target = object_attribute_target(row)
        if target is None:
            return ActionResult.unrouted(
                "Could not determine attribute column name from violation row."
            )
        return ActionResult.update(
            target_table=target["target_table"],
            target_pk=target["target_pk"],
            column=target["column"],
            old_value=target["old_value"],
            new_value=canonical_val,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return object_attribute_target(row)
