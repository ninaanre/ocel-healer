from src.llm.actions import ActionResult, object_attribute_target
from src.llm.tasks._base import IssueTask


class IncorrectDatatypes(IssueTask):
    issue_key = "incorrect_datatypes"

    PROMPT = """\
        Coerce `violation.actual_value` so that it matches the SQL affinity in `violation.expected_type`.

        IMPORTANT: `violation.actual_value` is a Python `repr()` of the original cell. A wrapping pair
        of single quotes means the cell currently holds a string -- strip those quotes before reasoning
        about the underlying value. `violation.actual_python_type` tells you the current Python type.

        SQL affinity -> target Python type:
          INTEGER                         -> int
          REAL / FLOA / DOUB / NUMERIC /  -> float
            DECIMAL
          TEXT / CHAR / CLOB              -> str
          BLOB                            -> bytes

        Worked examples (actual_value, expected_type) -> coerced_value:
          ('42', INTEGER)         -> 42
          ('3.14', REAL)          -> 3.14
          ('true', INTEGER)       -> 1
          ('false', INTEGER)      -> 0
          (42, TEXT)              -> "42"
          ('2024-01-01', TEXT)    -> "2024-01-01"
          ('banana', INTEGER)     -> null   # no meaning-preserving coercion

        Prefer a coercion whenever one preserves the value's meaning, even at modest confidence.
        Use `peer_objects` to confirm what correctly-typed values look like for this attribute.
        Return null ONLY when no coercion preserves the meaning -- and in that case, set
        `rationale` to the specific reason coercion is impossible.

        Return JSON: {"coerced_value": any|null, "rationale": str, "confidence": number}.
    """

    def extend_context(self, conn, ctx: dict, row: dict) -> None:
        self._attach_peers(conn, ctx, row)

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("coerced_value")
        if new is None:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to coerce: {reason}")
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
