"""Shared context builder for event-anchored temporal tasks.

`missing_event_timestamp` needs the objects touched by the anchor event and
the timestamps of every OTHER event touching those same objects — a
lifecycle window the LLM can use to bracket the anchor's missing time.

`ocel_time` lives on per-type event sub-tables (`event_<CamelCase>`), so
the sampler joins through `event_map_type` to reach them.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.detection.error_detection import _event_type_tables


# Sort key that keeps None values last without crashing on string comparison.
def _ts_key(v: Any) -> tuple[int, str]:
    if v is None:
        return (1, "")
    return (0, str(v))


def neighbor_events_ctx(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    max_neighbors: int = 10,
) -> dict[str, Any]:
    """Gather the temporal context around one event.

    Returns a dict with:
      - related_objects: list of {ocel_object_id, ocel_type, ocel_qualifier}
      - neighbor_events: list of {ocel_id, ocel_type, ocel_time, qualifier,
                                  ocel_object_id}, sorted by parsed timestamp
                        (nulls last). Excludes the anchor event.
      - expected_format: first non-null neighbor `ocel_time` verbatim (or None).

    The neighbor list is capped at `max_neighbors`. When more neighbors are
    available, we prefer a balanced slice around the anchor's own timestamp
    (if known) — half preceding, half following — falling back to the first
    N in sorted order.
    """
    ctx: dict[str, Any] = {}

    # 1. Related objects for the anchor event.
    related = conn.execute(
        "SELECT ocel_object_id, ocel_qualifier FROM event_object "
        "WHERE ocel_event_id = ?",
        (event_id,),
    ).fetchall()
    if not related:
        return ctx

    object_ids = [r[0] for r in related]
    qualifier_by_object = {r[0]: r[1] for r in related}

    # Object types (for display only).
    placeholders = ", ".join("?" * len(object_ids))
    obj_types = dict(conn.execute(
        f"SELECT ocel_id, ocel_type FROM object WHERE ocel_id IN ({placeholders})",
        object_ids,
    ).fetchall())

    ctx["related_objects"] = [
        {
            "ocel_object_id": oid,
            "ocel_type": obj_types.get(oid),
            "ocel_qualifier": qualifier_by_object.get(oid),
        }
        for oid in object_ids
    ]

    # 2. Neighbor events. Union across every event_<Type> table, joined via
    # event_object on the anchor's related objects, filtered to exclude the
    # anchor itself. We reach the per-type table for each event via
    # event_map_type.
    type_map = dict(_event_type_tables(conn))  # ocel_type -> event_<Type> table

    # Fetch every event touching any related object, plus its type (from
    # `event` table) and qualifier (from event_object).
    neighbor_rows = conn.execute(
        f"""
        SELECT DISTINCT
            e.ocel_id,
            e.ocel_type,
            eo.ocel_qualifier,
            eo.ocel_object_id
        FROM event_object eo
        JOIN event e ON e.ocel_id = eo.ocel_event_id
        WHERE eo.ocel_object_id IN ({placeholders})
          AND eo.ocel_event_id != ?
        """,
        (*object_ids, event_id),
    ).fetchall()

    if not neighbor_rows:
        ctx["neighbor_events"] = []
        return ctx

    # Attach ocel_time by joining through the per-type sub-table. Group
    # neighbors by event_type so we hit each sub-table once.
    by_type: dict[str, list[tuple]] = {}
    for row in neighbor_rows:
        by_type.setdefault(row[1], []).append(row)

    with_time: list[dict[str, Any]] = []
    for etype, rows in by_type.items():
        table = type_map.get(etype)
        if not table:
            # Unknown event type — emit without ocel_time.
            for eid, et, qual, oid in rows:
                with_time.append({
                    "ocel_id": eid, "ocel_type": et, "ocel_time": None,
                    "qualifier": qual, "ocel_object_id": oid,
                })
            continue
        ids = [r[0] for r in rows]
        ph = ", ".join("?" * len(ids))
        times = dict(conn.execute(
            f'SELECT ocel_id, ocel_time FROM "{table}" WHERE ocel_id IN ({ph})',
            ids,
        ).fetchall())
        for eid, et, qual, oid in rows:
            with_time.append({
                "ocel_id": eid, "ocel_type": et,
                "ocel_time": times.get(eid),
                "qualifier": qual, "ocel_object_id": oid,
            })

    with_time.sort(key=lambda e: _ts_key(e["ocel_time"]))

    # Balanced slice around the anchor's own timestamp when we can find it.
    anchor_time = None
    for etype, table in type_map.items():
        got = conn.execute(
            f'SELECT ocel_time FROM "{table}" WHERE ocel_id = ? LIMIT 1',
            (event_id,),
        ).fetchone()
        if got and got[0] is not None:
            anchor_time = got[0]
            break

    if len(with_time) > max_neighbors:
        if anchor_time is not None:
            preceding = [e for e in with_time if e["ocel_time"] and str(e["ocel_time"]) <= str(anchor_time)]
            following = [e for e in with_time if e["ocel_time"] and str(e["ocel_time"]) > str(anchor_time)]
            half = max_neighbors // 2
            slice_ = preceding[-half:] + following[:max_neighbors - half]
            # Top up if one side was short.
            if len(slice_) < max_neighbors:
                extras = [e for e in with_time if e not in slice_][: max_neighbors - len(slice_)]
                slice_.extend(extras)
            with_time = slice_
        else:
            with_time = with_time[:max_neighbors]

    ctx["neighbor_events"] = with_time
    # Format hint: first non-null neighbor timestamp, verbatim.
    for e in with_time:
        if e.get("ocel_time"):
            ctx["expected_format"] = e["ocel_time"]
            break
    return ctx
