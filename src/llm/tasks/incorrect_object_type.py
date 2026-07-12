from src.exploration.hint_selector import all_type_summaries
from src.llm.tasks._base import DetectionResult, DetectionTask


class IncorrectObjectType(DetectionTask):
    issue_key = "incorrect_object_type"

    def select_hints(self, profile: dict, guide: dict | None, row: dict) -> dict:
        # The current type is under suspicion — validating it needs the
        # overview of ALL types and their id templates, not just its own slice.
        return {"all_object_types": all_type_summaries(profile, guide)}

    PROMPT = """\
        <task>
        An object row in the `object` table has a non-empty `ocel_type`, but
        that type may be wrong for the object. Decide whether the current
        type is consistent with the objects id, the events touching the object and the
        attributes carried on the object's per-type row. Flag a mismatch
        when the evidence points to a different type.
        </task>

        <inputs>
          - violation.ocel_id        the id of the object whose type is being checked
          - violation.ocel_type      the object's CURRENT type (the value to validate)
          - object.attributes        the anchor object's existing attributes (may be empty)
          - events                   up to 8 events touching this object, each with
                                     the qualifier under which the event references it
          - candidate_types          the closed list of valid object types
        </inputs>

        <method>
          1. Compare the current `ocel_type` against the strongest signals:
             - If `exploration_hints.all_object_types` is present, match the id against
               each type's `id_template` (digit runs shown as `#`): an id matching another
               type's template while contradicting the current type is strong evidence.
             - The ID of the object — treat this as a STRONG signal on its own.
              If the ID contains a word that names one of the `candidate_types`
              (or an obvious synonym of one) and that clearly contradicts the
              current `ocel_type`, this alone is sufficient evidence to flag a
              mismatch. Do NOT treat a contradicting ID as "weak" just because
              events or attributes are absent.
             - Activity names + qualifiers in `events`: the qualifier names the
               role the object plays, which usually pins its type.
             - Attribute names/shapes in `object.attributes`: person-like
               attributes (e.g. `email`) vs goods-like attributes (e.g. `price`).
          2. If the current type is consistent with all signals, return
            `inferred_type: null`. A contradicting ID keyword alone is NOT
            "weak/ambiguous" — it IS strong evidence. Only default to null
            when the ID is opaque (e.g. a UUID or numeric code with no type
            keyword) AND events and attributes are also absent or neutral.
          3. Only when the evidence clearly contradicts the current type,
             return one value from `candidate_types` that fits the evidence
             better. Never invent a type that is not in `candidate_types`.
          4. Never return the current `ocel_type` value as `inferred_type`
             -- if you agree with it, return null.
        </method>

        <example>
          violation={ocel_id:'M42', ocel_type:'books'}
          events=[{activity:'lend book', qualifier:'member'}, {activity:'return book', qualifier:'member'}]
          object.attributes={email:'a@b.com', joined:'2020-01-01'}
          candidate_types=['members', 'books', 'loans']
          → {"inferred_type": "members", "rationale": "qualifier 'member' on both events and the email attribute contradict the current 'books' tag", "confidence": 0.95}
        </example>

        <example>
          violation={ocel_id:'L7', ocel_type:'loans'}
          events=[{activity:'open loan', qualifier:'loan'}]
          candidate_types=['members', 'books', 'loans']
          → {"inferred_type": null, "rationale": "events qualify this object as 'loan', matching the current type", "confidence": 0.95}
        </example>

        <example>
          violation={ocel_id:'book124', ocel_type:'loans'}
          events=[]
          object.attributes={}
          candidate_types=['members', 'books', 'loans']
          → {"inferred_type": "books", "rationale": "ID 'book124' contains the word 'book' which contradicts the current 'loans' type; no events or attributes present but the ID signal is sufficient", "confidence": 0.85}
        </example>

        <output>
        JSON: {"inferred_type": str|null, "rationale": str, "confidence": number}
        </output>
    """

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        suggested = payload.get("inferred_type")
        # Treat null / empty / "same as current" as "not flagged".
        if not suggested or suggested == row.get("ocel_type"):
            return DetectionResult(
                flagged=False, rationale=rationale, confidence=confidence,
                suggested_value=None,
            )
        return DetectionResult(
            flagged=True, rationale=rationale, confidence=confidence,
            suggested_value=suggested,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return {
            "target_table": "object",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
