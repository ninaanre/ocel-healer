from src.detection.error_detection import (
    _OCEL_RESERVED,
    _column_info,
    _object_type_tables,
)
from src.llm.actions import ActionResult
from src.llm.dataset_hints import DatasetHints
from src.llm.schemas import InferredObjectOutput
from src.llm.sql_utils import table_for_type
from src.llm.tasks._base import ResolutionTask


class MissingObject(ResolutionTask):
    """An `event_object` row references an object id that doesn't exist in
    the `object` table, but the id looks legitimate (matches a known type
    prefix or the referring qualifier hints at a known type). Propose a
    complete new object row: the missing top-level entry plus an
    initial-state row in the per-type sub-table.
    """

    issue_key = "missing_object"
    family = "relation"
    OutputModel = InferredObjectOutput
    min_confidence = 0.0  # low-confidence proposals still get shown for review

    TASK = """
        An event references an object that is missing from the database. The
        object's id and qualifier suggest a known ocel_type. Propose:
          - `ocel_type`: the type the missing object belongs to (must be one
            of `candidate_types`), or null if the evidence is too weak.
          - `attributes`: a shallow dict of column-name → value for the
            per-type sub-table's initial-state row. Match the shape and
            format of the `peer_objects`. Empty when the type has no
            per-object attributes.
        The resolver will INSERT one row into `object` and one initial-state
        row into `object_<Type>` from your answer.
    """

    INPUTS = """
        - violation.ocel_object_id         the missing object id (as it
                                           appears in event_object)
        - violation.inferred_type_from_prefix  best-effort type inferred
                                           from the id's prefix (or the
                                           referring qualifier)
        - violation.ocel_qualifier         the qualifier the referring event
                                           uses; often names the role
        - related_event                    the event doing the referring:
                                           its ocel_id and ocel_type
        - candidate_types                  closed list of valid object types
        - peer_columns                     the exact column names the
                                           `attributes` dict should carry
                                           (may be empty)
        - peer_objects                     up to 5 rows from the inferred
                                           type's sub-table — the shape you
                                           must match
    """

    METHOD = """
        1. `inferred_type_from_prefix` is usually correct. Only override it
           when the qualifier or the referring event's type contradicts it
           (e.g. an id shaped like `orders:o-…` but referenced under
           qualifier `product` from a `pick item` event is suspicious).
        2. Use `peer_columns` as the exact key set for `attributes`. If
           `peer_columns` is empty, produce `attributes: {}`.
        3. For each attribute, produce a value in the same format, unit,
           and casing as the peers. Prefer values common among the peers
           when the id/qualifier gives no per-attribute hint.
        4. Return `ocel_type: null` only when the evidence is genuinely
           opaque (no prefix, no qualifier hint, no matching peer shape).
        5. Do not include the reserved OCEL columns (`ocel_id`,
           `ocel_time`, `ocel_changed_field`, `ocel_type`) in `attributes` —
           the resolver fills those in.
    """

    EXAMPLES = """
        violation = {ocel_object_id: 'orders:o-991000',
                     inferred_type_from_prefix: 'orders',
                     ocel_qualifier: 'order'}
        related_event = {ocel_id: 'place_order:990001', ocel_type: 'place order'}
        peer_columns = ['price']
        peer_objects = [{ocel_id: 'o-990001', price: 42.5}, ...]
        → {"ocel_type": "orders",
           "attributes": {"price": 42.5},
           "rationale": "id prefix and qualifier both say 'orders'; matched price shape from peers",
           "confidence": 0.85}

        violation = {ocel_object_id: 'products:MysteryGadget',
                     inferred_type_from_prefix: 'products',
                     ocel_qualifier: 'product'}
        peer_columns = ['weight', 'price']
        peer_objects = [{ocel_id: 'iPhone 8', weight: 0.148, price: 599.0}, ...]
        → {"ocel_type": "products",
           "attributes": {"weight": 0.3, "price": 199.0},
           "rationale": "matched products' weight/price shape; picked mid-range values as no external signal",
           "confidence": 0.4}
    """

    # Key used to smuggle the resolved per-type sub-table from build_context
    # (which has a live conn) to parse_payload (which doesn't). Stashed on
    # the `row` dict; the leading underscore signals internal-use-only so
    # the LLM prompt renderer ignores it.
    _TABLE_HINT_KEY = "_resolved_target_table"

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        return (
            row.get("ocel_object_id"),
            row.get("inferred_type_from_prefix"),
        )

    def build_context(self, conn, row, *, hints=None):
        hints = hints or DatasetHints.empty()
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]

        # Resolve the per-type sub-table now and stash on the row so
        # parse_payload can build the insert plan without a fresh conn.
        _, anchor_type = self.anchor(row)
        target_table = table_for_type(conn, anchor_type)
        row[self._TABLE_HINT_KEY] = target_table
        if target_table:
            ctx["peer_columns"] = [c for c, _ in _column_info(conn, target_table)]

        # Peers of the inferred type — the shape the new object should match.
        self._attach_peers(conn, ctx, row)

        # Referring event, for context.
        event_id = row.get("ocel_event_id")
        if event_id:
            got = conn.execute(
                "SELECT ocel_id, ocel_type FROM event WHERE ocel_id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
            if got:
                ctx["related_event"] = {"ocel_id": got[0], "ocel_type": got[1]}

        if hints.data_semantics:
            ctx["data_semantics"] = hints.data_semantics
        self._call_extend_context(conn, ctx, row, hints)
        return ctx

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        ocel_type = payload.get("ocel_type")
        if not ocel_type:
            return ActionResult.decline(
                rationale or "LLM could not infer an object type for the missing referent."
            )

        target_table = row.get(self._TABLE_HINT_KEY)
        if not target_table:
            return ActionResult.unrouted(
                f"Cannot resolve per-type sub-table for ocel_type={ocel_type!r}",
                target_table="object",
            )

        anchor_id = row.get("ocel_object_id")
        attrs = payload.get("attributes") or {}
        clean_attrs = {k: v for k, v in attrs.items() if k not in _OCEL_RESERVED}

        inserts = [
            {"table": "object", "columns": {"ocel_id": anchor_id, "ocel_type": ocel_type}},
            {"table": target_table, "columns": {
                "ocel_id": anchor_id,
                "ocel_time": None,
                "ocel_changed_field": None,
                **clean_attrs,
            }},
        ]
        return ActionResult.insert(inserts=inserts, reason=rationale)

    def suppressed_target(self, row: dict) -> dict | None:
        # INSERT actions have no single-column override target.
        return None
