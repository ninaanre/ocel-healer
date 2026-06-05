from src.llm.actions import ActionResult, object_attribute_target
from src.llm.tasks._base import IssueTask


class MissingAttributeValue(IssueTask):
    issue_key = "missing_attribute_value"

    PROMPT = """\
        <task>
        An object row has a NULL or empty value for the attribute named in
        `violation.attribute_name`. Infer the most likely value.
        </task>

        <inputs>
          - violation.attribute_name   the attribute column whose value is missing
                                       (older rows may carry it as `violation.attribute`)
          - object.attributes          the anchor's other attributes (often correlate
                                       with the missing one — e.g. country implies currency)
          - events                     up to 8 events touching the anchor; activity
                                       names and qualifiers can pin down the value
          - peer_objects               up to 5 other objects of the same type with
                                       their full attribute rows — use these to learn
                                       the typical shape, format, and value distribution
        </inputs>

        <method>
          1. Look at `peer_objects[*][attribute_name]` to see what well-formed values
             of this attribute look like (data type, units, casing).
          2. Use the anchor's other attributes to narrow: many attributes correlate
             (country↔currency, product_id↔category, etc.).
          3. Use `events` activity names and qualifiers as a tiebreaker
             (e.g. activity 'pay_in_eur' on the anchor implies currency='EUR').
          4. Match the data type, units, and formatting of the peer values exactly.
        </method>

        <example>
          violation.attribute_name='currency'
          peer_objects=[{currency:'EUR'}, {currency:'EUR'}, {currency:'EUR'}]
          events=[{activity:'pay_in_eur'}]
          → {"inferred_value": "EUR", "rationale": "all 3 peers use 'EUR' and activity 'pay_in_eur' confirms it", "confidence": 0.95}
        </example>

        <output>
        JSON: {"inferred_value": any|null, "rationale": str, "confidence": number}
        </output>
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
