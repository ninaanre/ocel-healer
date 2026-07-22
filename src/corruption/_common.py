"""Shared helpers used across the corruption injectors.

Path constants, schema tweaks (PK removal), and a couple of low-level
inject helpers (`_null_type_for`, `_clone_object_row`,
`inject_missing_attribute_value`) that multiple issue modules build on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

DEFAULT_CLEAN_PATH = str(_DATA_DIR / "order-management-clean.sqlite")
DEFAULT_FULL_PATH = str(_DATA_DIR / "order-management-full.sqlite")     # level='all' output


def _default_dst_for_level(level: str) -> str:
    if level == "all":
        return DEFAULT_FULL_PATH
    return str(_DATA_DIR / f"order-management-{level}.sqlite")


def _remove_object_primary_key(conn: sqlite3.Connection) -> None:
    """Recreate the object table without PRIMARY KEY so duplicate ocel_id rows
    can be inserted (needed by every duplicate_objects_on_ids flavor).
    Idempotent: no-op if the PK is already absent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='object'"
    ).fetchone()
    if row is None or "PRIMARY KEY" not in (row[0] or "").upper():
        return
    conn.execute("CREATE TABLE object_tmp (ocel_id TEXT, ocel_type TEXT)")
    conn.execute("INSERT INTO object_tmp SELECT ocel_id, ocel_type FROM object")
    conn.execute("DROP TABLE object")
    conn.execute("ALTER TABLE object_tmp RENAME TO object")


def _null_type_for(
    conn: sqlite3.Connection, ocel_id: str, *, set_to: str | None
) -> str | None:
    n = conn.execute(
        "UPDATE object SET ocel_type = ? WHERE ocel_id = ? AND ocel_type IS NOT NULL "
        "AND (ocel_type != '' OR ? IS NULL)",
        (set_to, ocel_id, set_to),
    ).rowcount
    return ocel_id if n > 0 else None


def _clone_object_row(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    clone_id: str,
    ocel_type: str,
    table: str,
) -> str | None:
    row = conn.execute(
        f'SELECT * FROM "{table}" WHERE ocel_id = ? AND ocel_changed_field IS NULL '
        "LIMIT 1",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    cols = [
        desc[0]
        for desc in conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description
    ]
    conn.execute("INSERT INTO object VALUES (?, ?)", (clone_id, ocel_type))
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(
        f'INSERT INTO "{table}" VALUES ({placeholders})',
        (clone_id, *row[1:]),
    )
    return clone_id


def inject_missing_attribute_value(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    *,
    count: int = 1,
    ocel_ids: list[str] | None = None,
    changed_field_col: str = "ocel_changed_field",
) -> list[str]:
    """Missing attribute value: NULL `column` in `count` initial-state rows of `table`.

    Skips change-delta rows (where ``ocel_changed_field IS NOT NULL``) so the
    resulting NULLs are genuine missing values.
    """
    has_changed_field = conn.execute(
        f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = ?",
        (changed_field_col,),
    ).fetchone()[0]
    where = (
        f'WHERE "{changed_field_col}" IS NULL AND "{column}" IS NOT NULL'
        if has_changed_field
        else f'WHERE "{column}" IS NOT NULL'
    )
    if ocel_ids:
        placeholders = ", ".join("?" * len(ocel_ids))
        rows = conn.execute(
            f'SELECT ocel_id FROM "{table}" {where} AND ocel_id IN ({placeholders})',
            ocel_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f'SELECT ocel_id FROM "{table}" {where} LIMIT ?', (count,)
        ).fetchall()
    affected = [r[0] for r in rows]
    for ocel_id in affected:
        conn.execute(f'UPDATE "{table}" SET "{column}" = NULL WHERE ocel_id = ?', (ocel_id,))
    return affected
