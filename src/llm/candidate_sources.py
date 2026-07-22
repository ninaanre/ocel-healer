"""Per-issue-key candidate-row sources for the LLM detection sweep.

The dashboard's LLM sweep (see ``drill_llm_sweep`` in ``src/dashboard.py``) is
scoped by a type dropdown. But different detection tasks want different row
shapes as candidates:

- ``incorrect_object_type``            → one row per object of the chosen
                                         object type.
- ``incorrect_attribute_value``        → one row per (object, attribute)
                                         pair for that object type.
- ``incorrect_event_attribute_value``  → one row per (event, attribute)
                                         pair for a chosen event type.
- ``missing_object_attribute``         → one row per object TYPE (the
                                         chosen type). The LLM is called
                                         once per type; the returned list
                                         of suggestions is fanned out into
                                         N proposal cards in the sweep loop.
- ``missing_event_attribute``          → one row per event TYPE (same
                                         pattern, event-side).

Rather than smearing that dispatch across the sweep cell, every LLM-detected
issue registers its own row source here and the sweep just asks
``candidate_rows(issue_key, sqlite_path, chosen_type)``.
"""

from __future__ import annotations

from collections.abc import Callable

from src.detection.error_detection import (
    _column_info,
    _connect,
    _event_type_tables,
    _object_type_tables,
    _OCEL_RESERVED,
    iter_type_correct_attr_values,
    iter_type_correct_event_attr_values,
)


RowSource = Callable[[str, str], list[dict]]


def _candidates_incorrect_object_type(sqlite_path: str, chosen_type: str) -> list[dict]:
    """One row per object of the chosen type. Legacy shape matching the
    original hard-coded sweep in the dashboard."""
    with _connect(sqlite_path) as conn:
        ids = conn.execute(
            "SELECT ocel_id FROM object "
            "WHERE ocel_type = ? AND ocel_id IS NOT NULL "
            "ORDER BY ocel_id",
            (chosen_type,),
        ).fetchall()
    return [
        {"ocel_id": oid, "ocel_type": chosen_type, "issue": "incorrect_object_type"}
        for (oid,) in ids
    ]


def _candidates_incorrect_event_type(sqlite_path: str, chosen_type: str) -> list[dict]:
    """One row per event of the chosen type. Direct event-side mirror of
    :func:`_candidates_incorrect_object_type`."""
    with _connect(sqlite_path) as conn:
        ids = conn.execute(
            "SELECT ocel_id FROM event "
            "WHERE ocel_type = ? AND ocel_id IS NOT NULL "
            "ORDER BY ocel_id",
            (chosen_type,),
        ).fetchall()
    return [
        {
            "ocel_id": oid,
            "ocel_type": chosen_type,
            "event_type": chosen_type,
            "issue": "incorrect_event_type",
        }
        for (oid,) in ids
    ]


def _candidates_incorrect_event_time(sqlite_path: str, chosen_type: str) -> list[dict]:
    """One row per event of the chosen event type. Each row carries the
    concrete ``target_table`` (the per-type ``event_<Map>`` sub-table where
    ``ocel_time`` actually lives) and the current ``actual_value`` so the
    downstream fix path can UPDATE the right row without re-resolving the
    schema."""
    with _connect(sqlite_path) as conn:
        type_map = dict(_event_type_tables(conn))
        table = type_map.get(chosen_type)
        if not table:
            return []
        rows = conn.execute(
            f'SELECT ocel_id, ocel_time FROM "{table}" '
            f"WHERE ocel_id IS NOT NULL ORDER BY ocel_id"
        ).fetchall()
    return [
        {
            "ocel_id": oid,
            "event_type": chosen_type,
            "target_table": table,
            "actual_value": ocel_time,
            "issue": "incorrect_event_time",
        }
        for (oid, ocel_time) in rows
    ]


def _candidates_incorrect_attribute_value(sqlite_path: str, chosen_type: str) -> list[dict]:
    """One row per (object of chosen type, attribute) pair that already passes
    both rule detectors (non-null AND type-correct). This is the set of cells
    that could be semantically implausible without the rule detectors having
    caught them."""
    return iter_type_correct_attr_values(sqlite_path, object_type=chosen_type)


def _candidates_incorrect_event_attribute_value(sqlite_path: str, chosen_type: str) -> list[dict]:
    """Event-side counterpart of ``_candidates_incorrect_attribute_value``.
    One row per (event of chosen type, attribute) pair that passes both
    event-attribute rule detectors."""
    return iter_type_correct_event_attr_values(sqlite_path, event_type=chosen_type)


def _candidates_missing_object_attribute(sqlite_path: str, chosen_type: str) -> list[dict]:
    """One row per object type. The row carries the type's declared
    attributes so the sweep can decorate the parse step and downstream
    proposals with peer context without re-querying.

    Guards: returns ``[]`` if the chosen type has no per-type sub-table
    (e.g. the user picked a type that only appears in the top-level
    `object` table without an `object_map_type` entry). This surfaces as
    "0 candidates" in the sweep progress bar rather than a crash.
    """
    with _connect(sqlite_path) as conn:
        type_map = dict(_object_type_tables(conn))
        table = type_map.get(chosen_type)
        if not table:
            return []
        declared = [
            c for c, _ in _column_info(conn, table) if c not in _OCEL_RESERVED
        ]
    return [
        {
            "ocel_type": chosen_type,
            "object_type": chosen_type,
            "_resolved_target_table": table,
            # Snapshot the declared columns onto the row for the parse-time
            # duplicate guard; underscore-prefixed so it doesn't leak into
            # the prompt renderer's JSON block.
            "_declared_attributes": declared,
            "issue": "missing_object_attribute",
        }
    ]


def _candidates_missing_event_attribute(sqlite_path: str, chosen_type: str) -> list[dict]:
    """Event-side counterpart. One row per event type."""
    with _connect(sqlite_path) as conn:
        type_map = dict(_event_type_tables(conn))
        table = type_map.get(chosen_type)
        if not table:
            return []
        declared = [
            c for c, _ in _column_info(conn, table) if c not in _OCEL_RESERVED
        ]
    return [
        {
            "ocel_type": chosen_type,
            "event_type": chosen_type,
            "_resolved_target_table": table,
            "_declared_attributes": declared,
            "issue": "missing_event_attribute",
        }
    ]


_SOURCES: dict[str, RowSource] = {
    "incorrect_object_type":            _candidates_incorrect_object_type,
    "incorrect_event_type":             _candidates_incorrect_event_type,
    "incorrect_event_time":             _candidates_incorrect_event_time,
    "incorrect_attribute_value":        _candidates_incorrect_attribute_value,
    "incorrect_event_attribute_value":  _candidates_incorrect_event_attribute_value,
    "missing_object_attribute":         _candidates_missing_object_attribute,
    "missing_event_attribute":          _candidates_missing_event_attribute,
}


def candidate_rows(issue_key: str, sqlite_path: str, chosen_type: str) -> list[dict]:
    """Return the candidate rows the LLM sweep should judge for ``issue_key``,
    scoped to ``chosen_type``. Returns [] for unknown keys — the dashboard
    surfaces this as "0 candidates" rather than crashing the sweep."""
    src = _SOURCES.get(issue_key)
    if src is None:
        return []
    return src(sqlite_path, chosen_type)


def candidate_noun(issue_key: str) -> str:
    """Human-readable noun for the sweep progress bar (``Judging N X…``).
    Falls back to ``candidate`` for unregistered keys."""
    return {
        "incorrect_object_type":            "object",
        "incorrect_event_type":             "event",
        "incorrect_event_time":             "event time",
        "incorrect_attribute_value":        "attribute value",
        "incorrect_event_attribute_value":  "event attribute value",
        "missing_object_attribute":         "type schema",
        "missing_event_attribute":          "event type schema",
    }.get(issue_key, "candidate")


def candidate_kind(issue_key: str) -> str:
    """Which type dropdown scopes this issue's LLM sweep: ``"object"`` for
    object-side issues (the default), ``"event"`` for event-side ones. The
    dashboard consults this when building the type picker so the user
    picks from event types when drilling into event-attribute issues."""
    return {
        "incorrect_event_type":             "event",
        "incorrect_event_time":             "event",
        "incorrect_event_attribute_value":  "event",
        "missing_event_attribute":          "event",
    }.get(issue_key, "object")


__all__ = ["candidate_rows", "candidate_noun", "candidate_kind"]
