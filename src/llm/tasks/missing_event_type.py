from src.detection.error_detection import _event_type_tables
from src.llm.actions import ActionResult
from src.llm.schemas import InferredTypeOutput
from src.llm.tasks._base import ResolutionTask


class MissingEventType(ResolutionTask):
    """An event row in the `event` table has a NULL / empty / whitespace
    `ocel_type`. Mirror of ``MissingObjectType`` on the event side.
    """

    issue_key = "missing_event_type"
    family = "type"
    OutputModel = InferredTypeOutput

    TASK = """
        An event row in the `event` table has a NULL or empty `ocel_type`.
        Infer the most likely type from the event's id, its timestamp
        (when known), and the objects it touches. Pick exactly one value
        from `candidate_types`.
    """

    INPUTS = """
        - violation.ocel_id       the id of the event whose type is missing
        - event.ocel_time         the event's timestamp (if discoverable
                                  from any per-type sub-table)
        - related_objects         up to 8 objects touched by this event,
                                  each with ocel_id, ocel_type, and the
                                  qualifier under which the event
                                  references them
        - candidate_types         the closed list of valid event types
    """

    METHOD = """
        1. Read `violation.ocel_id` first — many event ids embed the
           activity keyword (e.g. `place order:990001`, `pay order:…`,
           `confirm order:…`). An id keyword pins the type with high
           confidence.
        2. If the id is opaque, use `related_objects`:
             - Qualifier `order` on an `orders` object → an order-
               lifecycle event (place / confirm / pay).
             - Qualifier `packer` on a `packages` object → `create package`
               or `send package`.
             - Qualifier `shipper` on a `packages` object → `send package`
               (delivery-adjacent).
             - Qualifier `item` on an `items` object → `pick item` or
               related item events.
        3. When `event.ocel_time` is known and the same-object lifecycle
           has other timestamped events, prefer a type whose typical
           position in the lifecycle matches the anchor's time.
        4. Return exactly one value from `candidate_types` (canonical
           casing). Never return NULL — this task always guesses.
    """

    EXAMPLES = """
        violation.ocel_id = 'confirm order:990400'
        related_objects = [{ocel_type: 'orders', qualifier: 'order'}]
        candidate_types = ['place order', 'confirm order', 'pay order', ...]
        → {"inferred_type": "confirm order",
           "rationale": "id embeds 'confirm order' and touches an orders object with qualifier 'order'",
           "confidence": 0.95}

        violation.ocel_id = 'create_package:e-770001'
        event.ocel_time = '2023-04-04 08:00:00'
        related_objects = [{ocel_type: 'packages', qualifier: 'packer'}]
        candidate_types = ['create package', 'send package', ...]
        → {"inferred_type": "create package",
           "rationale": "id embeds 'create_package' and qualifier 'packer' fits create over send",
           "confidence": 0.9}
    """

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # Event anchor — skip the object-side _attach_anchor / _attach_events.
        return (None, None)

    def build_context(self, conn, row, *, use_hints=True):
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        ctx["candidate_types"] = [t for t, _ in _event_type_tables(conn)]

        event_id = row.get("ocel_id")
        if event_id:
            related = conn.execute(
                "SELECT o.ocel_id, o.ocel_type, eo.ocel_qualifier "
                "FROM event_object eo LEFT JOIN object o "
                "  ON o.ocel_id = eo.ocel_object_id "
                "WHERE eo.ocel_event_id = ? LIMIT 8",
                (event_id,),
            ).fetchall()
            if related:
                ctx["related_objects"] = [
                    {"ocel_id": r[0], "ocel_type": r[1], "qualifier": r[2]}
                    for r in related
                ]

            # `ocel_time` lives on the per-type sub-tables. With ocel_type
            # blank we don't know which sub-table to hit, so probe every
            # sub-table until one has this event id. Cheap: each table's
            # ocel_id is indexed and only one match will exist across the
            # union.
            for _, table in _event_type_tables(conn):
                got = conn.execute(
                    f'SELECT ocel_time FROM "{table}" WHERE ocel_id = ? LIMIT 1',
                    (event_id,),
                ).fetchone()
                if got:
                    ctx["event"] = {"ocel_id": event_id, "ocel_time": got[0]}
                    break

        if use_hints:
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    def parse_payload(self, row: dict, payload: dict) -> ActionResult:
        new = payload.get("inferred_type")
        if not new:
            reason = (payload.get("rationale") or "").strip() or "no reason provided"
            return ActionResult.decline(f"LLM declined to infer an event type: {reason}")
        return ActionResult.update(
            target_table="event",
            target_pk={"ocel_id": row["ocel_id"]},
            column="ocel_type",
            old_value=row.get("ocel_type"),
            new_value=new,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        return {
            "target_table": "event",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }
