from src.llm.actions import ActionResult, object_attribute_target
from src.llm.tasks._base import IssueTask


class MissingAttributes(IssueTask):
    issue_key = "missing_attributes"

    PROMPT = """\
        An object row has a missing (NULL or empty) value for the attribute named
        in `violation.attribute` (or `violation.attribute_name`). Infer the most likely value.

        Reasoning recipe:
          1. `peer_objects` shows up to 5 other objects of the same type with their
            full attribute rows -- use these to learn the typical shape, format, and
            value distribution of the missing attribute.
          2. The anchor object's other (non-missing) attributes are in `object.attributes`.
            They often correlate with the missing one (e.g. country implies currency, product_id implies category).
          3. `events` lists activities touching this object; activity names and qualifiers
            can pin down the value (e.g. activity 'pay_in_eur' implies currency='EUR').
          4. Match the data type, units, and formatting of the peer values exactly. Do not
            invent ids, codes, or names that are not supported by the evidence.
          5. Return null only when peers and events together give no signal for this attribute,
            and put the specific reason in `rationale` (e.g. 'all peer values are distinct free-text
            and no event activity narrows them').

        Return JSON: {"inferred_value": any|null, "rationale": str, "confidence": number}.
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        self._attach_peers(conn, ctx, row)

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_value")
        if new is None:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer a value: {reason}")
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
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return object_attribute_target(row)
