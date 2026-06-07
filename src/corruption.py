import shutil
import sqlite3


def corrupt_database(src_path: str, dst_path: str) -> None:
    """Copy src to dst and inject all known error types."""
    shutil.copy2(src_path, dst_path)
    with sqlite3.connect(dst_path) as conn:
        _remove_object_primary_key(conn)
        n6a_id = inject_n6a(conn)
        inject_n6b(conn)
        inject_n2(conn, exclude_id=n6a_id)
        inject_n10_object(conn)
        inject_n10_event(conn)
        conn.commit()


def _remove_object_primary_key(conn: sqlite3.Connection) -> None:
    """Recreate the object table without PRIMARY KEY to allow duplicate ocel_id."""
    conn.execute("CREATE TABLE object_tmp (ocel_id TEXT, ocel_type TEXT)")
    conn.execute("INSERT INTO object_tmp SELECT ocel_id, ocel_type FROM object")
    conn.execute("DROP TABLE object")
    conn.execute("ALTER TABLE object_tmp RENAME TO object")


def inject_n6a(conn: sqlite3.Connection) -> str | None:
    """N6(a): Insert a fully duplicated object (same ocel_id and ocel_type)."""
    row = conn.execute(
        "SELECT ocel_id, ocel_type FROM object WHERE ocel_type IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO object VALUES (?, ?)", row)
    return row[0]


def inject_n6b(conn: sqlite3.Connection) -> str | None:
    """N6(b): Insert a new object with the same attributes as an existing one but a different ID."""
    types = conn.execute(
        "SELECT DISTINCT ocel_type FROM object WHERE ocel_type IS NOT NULL AND ocel_type != ''"
    ).fetchall()
    for (ot,) in types:
        table = f"object_{ot}"
        try:
            cols = [
                desc[0]
                for desc in conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description
            ]
            row = conn.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
            if row is None:
                continue
            new_id = f"{ot}:CLONE_9999"
            conn.execute("INSERT INTO object VALUES (?, ?)", (new_id, ot))
            placeholders = ", ".join(["?"] * len(cols))
            conn.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', (new_id, *row[1:]))
            return new_id
        except Exception:
            continue
    return None


def inject_n2(conn: sqlite3.Connection, exclude_id: str | None = None) -> str | None:
    """N2: Set ocel_type to NULL for one object (different from exclude_id)."""
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type IS NOT NULL AND ocel_id != ? "
        "GROUP BY ocel_id HAVING COUNT(*) = 1 LIMIT 1",
        (exclude_id or "",),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE object SET ocel_type = NULL WHERE ocel_id = ? AND ocel_type IS NOT NULL",
        (row[0],),
    )
    return row[0]


def inject_n10_object(conn: sqlite3.Connection) -> str | None:
    """N10: Insert an E2O relation referencing a non-existent object."""
    event = conn.execute("SELECT ocel_id FROM event LIMIT 1").fetchone()
    if event is None:
        return None
    fake_object_id = "FAKE_OBJECT:99999"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (event[0], fake_object_id, "unknown"),
    )
    return fake_object_id


def inject_n3a_missing_attribute(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    *,
    count: int = 1,
    changed_field_col: str = "ocel_changed_field",
) -> list[str]:
    """N3a: Set `column` to NULL in `count` initial-state rows of `table`.

    Only targets rows where changed_field_col IS NULL (initial state),
    so the missing values are genuine data quality issues, not delta artifacts.
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
    rows = conn.execute(
        f'SELECT ocel_id FROM "{table}" {where} LIMIT ?', (count,)
    ).fetchall()
    affected = [r[0] for r in rows]
    for ocel_id in affected:
        conn.execute(f'UPDATE "{table}" SET "{column}" = NULL WHERE ocel_id = ?', (ocel_id,))
    return affected


def inject_n10_event(conn: sqlite3.Connection) -> str | None:
    """N10: Insert an E2O relation referencing a non-existent event."""
    obj = conn.execute("SELECT ocel_id FROM object WHERE ocel_type IS NOT NULL LIMIT 1").fetchone()
    if obj is None:
        return None
    fake_event_id = "FAKE_EVENT:99999"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (fake_event_id, obj[0], "unknown"),
    )
    return fake_event_id
