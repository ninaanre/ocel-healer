from src.detection.error_detection import _column_info
from src.llm.actions import ActionResult, object_attribute_target
from src.llm.dataset_hints import DatasetHints
from src.llm.schemas import CanonicalValueOutput
from src.llm.sql_utils import quote, table_for_type
from src.llm.tasks._base import ResolutionTask


class DuplicateObjectsOnAttributes(ResolutionTask):
    issue_key = "duplicate_objects_on_attributes"
    family = "duplicate"
    OutputModel = CanonicalValueOutput

    TASK = """
        Two or more objects of the same type share an identical attribute
        fingerprint but differ on `ocel_id`. Pick the canonical value for
        the conflicting attribute on the anchor object.
    """

    INPUTS = """
        - violation.attribute_name      the column in conflict (older rows
                                        may carry it as `attribute`)
        - duplicate_attribute_values    the candidate values to choose from
        - object_attributes             the anchor's full attribute row — use
                                        its other columns as formatting /
                                        casing / units cues
        - events                        up to 8 events touching the anchor;
                                        their activities and qualifiers are
                                        tiebreakers
    """

    METHOD = """
        1. Compare the candidates in `duplicate_attribute_values`. Prefer
           the one whose formatting, casing, and units match the anchor's
           other attributes.
        2. Use `events` activities and qualifiers as tiebreakers (e.g.
           activity `pay_in_eur` implies `EUR` over `USD`).
        3. The chosen value must be one of `duplicate_attribute_values`
           verbatim.
    """

    EXAMPLES = """
        violation.attribute_name = 'currency'
        duplicate_attribute_values = ['EUR', 'USD']
        object_attributes = {country: 'DE', amount: '100.00'}
        events = [{activity: 'pay_in_eur'}]
        → {"canonical_value": "EUR",
           "rationale": "country='DE' and activity 'pay_in_eur' both point to EUR over USD",
           "confidence": 0.9}
    """

    def extend_context(self, conn, ctx: dict, row: dict, *, hints: DatasetHints) -> None:
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
