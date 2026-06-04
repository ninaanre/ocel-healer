import sqlite3

from src.detection.error_detection import _object_type_tables


def quote(name: str) -> str:
    """Escape a SQLite identifier (table or column name)."""
    return '"' + name.replace('"', '""') + '"'


def table_for_type(conn: sqlite3.Connection, ocel_type: str | None) -> str | None:
    """Map an OCEL object type to its per-type attribute table, or None."""
    if not ocel_type:
        return None
    for t, table in _object_type_tables(conn):
        if t == ocel_type:
            return table
    return None
