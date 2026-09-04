from src.detection.error_detection import _event_type_tables
from src.llm.schemas import InferredTimestampOutput
from src.llm.tasks._base import DetectionResult, DetectionTask
from src.llm.tasks._event_context import neighbor_events_ctx


class IncorrectEventTime(DetectionTask):
    """LLM-only detector for implausible ``ocel_time`` values.

    Rule detectors don't fire here — a syntactically-valid timestamp that
    violates the object's lifecycle order (or falls outside its window
    entirely) still parses cleanly as a datetime. The LLM judges the
    anchor's ``ocel_time`` against the ordered timestamps of every other
    event touching the same objects; when the anchor is inconsistent, it
    proposes a corrected timestamp using the neighbors' format verbatim.

    Reuses ``InferredTimestampOutput`` and the ``neighbor_events_ctx``
    context builder from :mod:`missing_event_timestamp`.
    """

    issue_key = "incorrect_event_time"
    family = "temporal"
    kind = "event"
    OutputModel = InferredTimestampOutput

    TASK = """
        This event's `ocel_time` is present and syntactically well-formed,
        but may be implausible given the lifecycles of the objects it
        touches. Judge whether the anchor sits sensibly in the ordered
        timeline of its neighbors — or whether it clearly precedes /
        follows events that logically bracket it. If inconsistent, propose
        a corrected timestamp; otherwise return null.
    """

    INPUTS = """
        - violation.ocel_id       the id of the event being checked
        - violation.event_type    the event's type (e.g. `pay order`)
        - violation.actual_value  the CURRENT `ocel_time` under scrutiny
        - violation.target_table  concrete per-type sub-table where
                                  `ocel_time` lives (e.g. `event_PayOrder`);
                                  FYI only
        - related_objects         list of {ocel_object_id, ocel_type,
                                  ocel_qualifier} — the objects this event
                                  touches
        - neighbor_events         list of {ocel_id, ocel_type, ocel_time,
                                  qualifier, ocel_object_id} — every other
                                  event touching those same objects, sorted
                                  by timestamp
        - expected_format         one representative timestamp string from a
                                  neighbor; use this format verbatim if you
                                  propose a corrected value
    """

    METHOD = """
        1. Read the anchor's `event_type` and locate its typical position
           in the object lifecycle: for order processes, `place order` <
           `confirm order` < `pay order` < `pick item` < `send package`.
        2. Read `actual_value` and compare it against the bracketing
           neighbors: what events immediately precede and follow this
           anchor in the per-object sequences?
        3. Flag as incorrect ONLY when the anchor clearly violates the
           lifecycle order OR clearly falls outside the object's window.
           Small out-of-order differences (seconds apart, minor drift)
           should NOT be flagged — this is a soft check for gross errors,
           not a lint. Also do NOT flag when the neighbor set is empty
           or contains no timestamps.
        4. When you do flag, propose a corrected `inferred_timestamp` that
           respects every bracketing pair across all touched objects.
           Match `expected_format` verbatim (same separator, same
           precision, no timezone).
        5. Return `inferred_timestamp: null` when the anchor is plausible
           given the neighbors, or when the neighbors give no bracketing
           signal at all.
    """

    EXAMPLES = """
        violation = {ocel_id: 'pay_o-990010', event_type: 'pay order',
                     actual_value: '2020-01-01 00:00:00'}
        neighbor_events = [
          {ocel_type: 'place order',  ocel_time: '2023-04-03 12:00:00'},
          {ocel_type: 'confirm order',ocel_time: '2023-04-03 12:05:00'},
          {ocel_type: 'pick item',    ocel_time: '2023-04-03 14:00:00'},
          {ocel_type: 'send package', ocel_time: '2023-04-03 16:00:00'},
        ]
        expected_format = '2023-04-03 12:00:00'
        → {"inferred_timestamp": "2023-04-03 13:00:00",
           "rationale": "anchor's 2020 timestamp is years before place_order (2023-04-03); pay_order sits between confirm_order and pick_item",
           "confidence": 0.95}

        violation = {ocel_id: 'pay_o-990011', event_type: 'pay order',
                     actual_value: '2023-04-03 13:00:00'}
        neighbor_events = [
          {ocel_type: 'place order',  ocel_time: '2023-04-03 12:00:00'},
          {ocel_type: 'pick item',    ocel_time: '2023-04-03 14:00:00'},
        ]
        → {"inferred_timestamp": null,
           "rationale": "13:00 sits cleanly between place_order (12:00) and pick_item (14:00)",
           "confidence": 0.9}
    """

    def anchor(self, row: dict) -> tuple[str | None, str | None]:
        # No object anchor; skip _attach_anchor / _attach_events (event tasks
        # never attach events anyway, but returning (None, None) also skips
        # the per-type table lookup for the event itself since it's not the
        # object anchor the base helpers expect).
        return (None, None)

    def build_context(self, conn, row, *, use_hints=True):
        ctx: dict = {"issue_key": self.issue_key, "violation": dict(row)}
        event_id = row.get("ocel_id")
        if event_id:
            ctx.update(neighbor_events_ctx(conn, event_id))
        if use_hints:
            self._attach_exploration_hints(conn, ctx, row)
        self.extend_context(conn, ctx, row)
        return ctx

    def parse_detection(self, row: dict, payload: dict) -> DetectionResult:
        rationale = str(payload.get("rationale", "") or "").strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        inferred = payload.get("inferred_timestamp")
        current = row.get("actual_value")
        # Not flagged when the model returns null, or proposes the current
        # value (equivalent to null for our purposes).
        if not inferred or str(inferred) == str(current):
            return DetectionResult(
                flagged=False, rationale=rationale, confidence=confidence,
                suggested_value=None,
            )
        return DetectionResult(
            flagged=True, rationale=rationale, confidence=confidence,
            suggested_value=inferred,
        )

    def suppressed_target(self, row: dict) -> dict | None:
        # Fall back to resolving target_table from event_type when the row
        # (constructed by the candidate source) didn't already carry it —
        # this makes the task robust to being invoked from other paths too.
        target_table = row.get("target_table")
        ocel_id = row.get("ocel_id")
        if not (target_table and ocel_id):
            return None
        return {
            "target_table": target_table,
            "target_pk": {"ocel_id": ocel_id},
            "column": "ocel_time",
            "old_value": row.get("actual_value"),
        }
