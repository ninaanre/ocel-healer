from src.exploration.hint_selector import all_type_summaries
from src.llm.actions import ActionResult
from src.llm.tasks._base import ResolutionTask


class MissingObjectType(ResolutionTask):
    issue_key = "missing_object_type"

    def select_hints(self, profile: dict, guide: dict | None, row: dict) -> dict:
        # The row has no usable object_type — the task needs the overview of
        # ALL types (what they represent + their id templates) to pick one.
        return {"all_object_types": all_type_summaries(profile, guide)}

    PROMPT = """\
        <task>
        An object row in the `object` table has a NULL or empty `ocel_type`.
        Infer the most likely type for this object from its id, events and attributes.
        </task>

        <inputs>
          - violation.ocel_id        the id of the object whose type is missing
          - object.attributes        the anchor object's existing attributes (may be empty)
          - events                   up to 8 events touching this object, each with
                                     the qualifier under which the event references it
          - candidate_types          the closed list of valid object types — pick
                                     exactly one of these strings
        </inputs>

        <method>
          0. Read `violation.ocel_id` carefully — this is often the single strongest signal.
             - A human full name (e.g. "Maria Garcia", "John Smith") → a person type from
               `candidate_types`, NEVER a physical-object or document type.
             - If `exploration_hints.all_object_types` is present, match the id against each
               type's `id_template` (digit runs shown as `#`): a matching template is strong
               evidence for that type.
          1. Read the qualifiers in `events`. The qualifier describes the ROLE this object
             plays in the event — NOT the subject of the event.
             Example: qualifier 'borrower' on activity 'lend book' means this object IS the
             borrower (a person), NOT the book being lent.
             A qualifier naming a human role → the matching person type in `candidate_types`.
          2. Attribute names in `object.attributes` reinforce the choice: person-like
             attributes (e.g. `email`, `role`) → a person type; goods-like attributes
             (e.g. `price`, `weight`) → a goods type.
          3. Pick exactly one value from `candidate_types`. If multiple fit,
             choose the one whose name best matches the id + qualifiers seen.
        </method>

        <example>
          violation.ocel_id='Maria Garcia'
          events=[{activity:'lend book', qualifier:'librarian'}, {activity:'return book', qualifier:'librarian'}]
          candidate_types=['members', 'books', 'librarians', 'loans']
          → {"inferred_type": "librarians", "rationale": "'Maria Garcia' is a human name; qualifier 'librarian' is the role this person plays — they lend books, they are not a book", "confidence": 0.95}
        </example>

        <output>
        JSON: {"inferred_type": str|null, "rationale": str, "confidence": number}
        </output>
    """

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_type")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer an object type: {reason}")
        return ActionResult.update(
            target_table="object",
            target_pk={"ocel_id": row["ocel_id"]},
            column="ocel_type",
            old_value=row.get("ocel_type"),
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return {
            "target_table": "object",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
