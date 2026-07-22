import sqlite3

from src.detection.error_detection import _event_type_tables, _object_type_tables


def quote(name: str) -> str:
    """Escape a SQLite identifier (table or column name)."""
    return '"' + name.replace('"', '""') + '"'


def table_for_type(
    conn: sqlite3.Connection, ocel_type: str | None, *, kind: str = "object",
) -> str | None:
    """Map an OCEL type to its per-type attribute table, or None.

    ``kind`` selects the map: ``"object"`` looks up ``_object_type_tables``,
    ``"event"`` looks up ``_event_type_tables``. Defaulting to object
    keeps every existing call site working unchanged.
    """
    if not ocel_type:
        return None
    lookup = _event_type_tables if kind == "event" else _object_type_tables
    for t, table in lookup(conn):
        if t == ocel_type:
            return table
    return None
