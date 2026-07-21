"""Object-side corruption injectors.

Covers all issues rooted in the `object` table and its per-type
sub-tables (`object_<Type>`): missing/incorrect object types, missing or
type-invalid attribute values, and duplicate objects.

Each `inject_*` function introduces exactly one flavor and returns the
affected id(s) so callers can log or reference them. Naming convention:
`inject_<issue_key>_<flavor>`, where `<issue_key>` is the canonical
detector name (e.g. `missing_object_type`, `duplicate_objects_on_ids`).
"""

from __future__ import annotations

import sqlite3

from src.corruption._common import (
    _clone_object_row,
    _null_type_for,
    inject_missing_attribute_value,
)


# ---------------------------------------------------------------------------
# Base injectors — building blocks reused by the tiered flavors below and
# called directly from the legacy stage.
# ---------------------------------------------------------------------------


def inject_duplicate_objects_on_ids(conn: sqlite3.Connection, ocel_id: str | None = None) -> str | None:
    """duplicate_objects_on_ids: Insert a fully duplicated object row
    (same ocel_id + ocel_type)."""
    if ocel_id:
        row = conn.execute(
            "SELECT ocel_id, ocel_type FROM object WHERE ocel_id = ? LIMIT 1", (ocel_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT ocel_id, ocel_type FROM object WHERE ocel_type IS NOT NULL LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO object VALUES (?, ?)", row)
    return row[0]


def inject_duplicate_objects_on_attributes(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_attributes: Insert a new object whose per-type
    attributes match an existing one but under a different ocel_id
    (`{type}:CLONE_9999`)."""
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


def inject_missing_object_type(conn: sqlite3.Connection, exclude_id: str | None = None) -> str | None:
    """missing_object_type: Set ocel_type to NULL for one object
    (different from exclude_id)."""
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


def inject_incorrect_object_type(
    conn: sqlite3.Connection,
    ocel_id: str,
    incorrect_type: str,
) -> str | None:
    """incorrect_object_type: Overwrite an object's ocel_type with an
    incorrect-but-non-null value."""
    n = conn.execute(
        "UPDATE object SET ocel_type = ? WHERE ocel_id = ?",
        (incorrect_type, ocel_id),
    ).rowcount
    return ocel_id if n > 0 else None


# ---------------------------------------------------------------------------
# missing_object_type (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_missing_object_type_null_employee(conn: sqlite3.Connection) -> str | None:
    """missing_object_type Easy: NULL the ocel_type of a specific,
    well-known employee."""
    return _null_type_for(conn, "Wil van der Aalst", set_to=None)


def inject_missing_object_type_empty_string_order(conn: sqlite3.Connection) -> str | None:
    """missing_object_type Medium: Set ocel_type to '' on a specific order."""
    return _null_type_for(conn, "o-990010", set_to="")


def inject_missing_object_type_whitespace_product(conn: sqlite3.Connection) -> str | None:
    """missing_object_type Hard: Set ocel_type to a whitespace-only string
    on a product."""
    return _null_type_for(conn, "MacBook Pro", set_to="   ")


# ---------------------------------------------------------------------------
# missing_attribute_value (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_missing_attribute_value_null_product_weight(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Easy: NULL the weight of `iPhone 8`
    (id-as-name hint applies)."""
    return inject_missing_attribute_value(
        conn, "object_Products", "weight", ocel_ids=["iPhone 8"]
    )


def inject_missing_attribute_value_empty_string_role(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Medium: Set `role` to '' for
    `Christine von Dobbert`."""
    n = conn.execute(
        'UPDATE object_Employees SET role = \'\' WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL AND role IS NOT NULL',
        ("Christine von Dobbert",),
    ).rowcount
    return ["Christine von Dobbert"] if n > 0 else []


def inject_missing_attribute_value_null_order_price(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Hard: NULL the initial `price` of order
    `o-990050`."""
    return inject_missing_attribute_value(
        conn, "object_Orders", "price", ocel_ids=["o-990050"]
    )


# ---------------------------------------------------------------------------
# incorrect_object_type (LLM-detected, Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_object_type_swap_order_to_employee(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Easy: Retype order `o-990001` as `employees`."""
    return inject_incorrect_object_type(conn, "o-990001", "employees")


def inject_incorrect_object_type_swap_item_to_product(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Medium: Retype item `i-880100` as `products`."""
    return inject_incorrect_object_type(conn, "i-880100", "products")


def inject_incorrect_object_type_case_variant_customers(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Hard: Retype a customer as `CUSTOMERS`
    (case variant)."""
    return inject_incorrect_object_type(
        conn, "Carpathian Financial Services plc", "CUSTOMERS"
    )


# ---------------------------------------------------------------------------
# incorrect_attribute_datatype (Easy/Medium/Hard)
#
# SQLite type affinity coerces most naive datatype mismatches (e.g. '42' in
# an INTEGER column becomes int 42). The reliable recipes are:
#   * non-numeric strings in REAL/INT columns  -> stored as TEXT
#   * bytes objects in TEXT columns            -> stored as BLOB
# ---------------------------------------------------------------------------


def inject_incorrect_attribute_datatype_string_in_weight(conn: sqlite3.Connection) -> str | None:
    """incorrect_attribute_datatype Easy: Put `'unknown'` into
    `object_Products.weight` (REAL)."""
    n = conn.execute(
        'UPDATE object_Products SET weight = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        ("unknown", "iPad Air"),
    ).rowcount
    return "iPad Air" if n > 0 else None


def inject_incorrect_attribute_datatype_string_in_order_price(conn: sqlite3.Connection) -> str | None:
    """incorrect_attribute_datatype Medium: Put `'TBD'` into
    `object_Orders.price` (REAL)."""
    n = conn.execute(
        'UPDATE object_Orders SET price = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        ("TBD", "o-990200"),
    ).rowcount
    return "o-990200" if n > 0 else None


def inject_incorrect_attribute_datatype_blob_in_role(conn: sqlite3.Connection) -> str | None:
    """incorrect_attribute_datatype Hard: Put a `bytes` blob into
    `object_Employees.role` (TEXT)."""
    n = conn.execute(
        'UPDATE object_Employees SET role = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        (b"\x00\x00\x01", "Jan Niklas Adams"),
    ).rowcount
    return "Jan Niklas Adams" if n > 0 else None


# ---------------------------------------------------------------------------
# duplicate_objects_on_ids (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_objects_on_ids_product(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Easy: Duplicate the (`Echo Dot`, `products`)
    row in `object`."""
    return inject_duplicate_objects_on_ids(conn, ocel_id="Echo Dot")


def inject_duplicate_objects_on_ids_conflicting_types(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Medium: Insert `('o-990300', 'items')` next
    to the real order row."""
    conn.execute("INSERT INTO object VALUES (?, ?)", ("o-990300", "items"))
    return "o-990300"


def inject_duplicate_objects_on_ids_triple_null_type(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Hard: Add two extra rows for one customer id,
    one with NULL type."""
    ocel_id = "AlpenTech Innovations AG"
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, None))
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, "customers"))
    return ocel_id


# ---------------------------------------------------------------------------
# duplicate_objects_on_attributes (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_objects_on_attributes_clone_product(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_attributes Easy: Clone `Echo Dot`'s initial-state
    row under a fabricated id."""
    return _clone_object_row(
        conn,
        source_id="Echo Dot",
        clone_id="products:CLONE_ECHO_DOT",
        ocel_type="products",
        table="object_Products",
    )


def inject_duplicate_objects_on_attributes_clone_employee(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_attributes Medium: Insert two employees with
    identical fabricated attributes."""
    original = "employees:CONSULT_A"
    clone = "employees:CONSULT_B"
    fingerprint_time = "2023-04-03 01:00:00"
    role = "Consulting"
    conn.execute("INSERT INTO object VALUES (?, ?)", (original, "employees"))
    conn.execute("INSERT INTO object VALUES (?, ?)", (clone, "employees"))
    conn.execute(
        'INSERT INTO object_Employees (ocel_id, ocel_time, ocel_changed_field, role) '
        "VALUES (?, ?, NULL, ?)",
        (original, fingerprint_time, role),
    )
    conn.execute(
        'INSERT INTO object_Employees (ocel_id, ocel_time, ocel_changed_field, role) '
        "VALUES (?, ?, NULL, ?)",
        (clone, fingerprint_time, role),
    )
    return clone


def inject_duplicate_objects_on_attributes_clone_order_and_referenced(conn: sqlite3.Connection) -> list[str]:
    """duplicate_objects_on_attributes Hard: Clone an order AND copy its
    `event_object` rows."""
    original = "o-990500"
    clone = "o-990500-DUP"
    if not _clone_object_row(
        conn,
        source_id=original,
        clone_id=clone,
        ocel_type="orders",
        table="object_Orders",
    ):
        return []
    conn.execute(
        "INSERT INTO event_object (ocel_event_id, ocel_object_id, ocel_qualifier) "
        "SELECT ocel_event_id, ?, ocel_qualifier FROM event_object "
        "WHERE ocel_object_id = ?",
        (clone, original),
    )
    return [original, clone]
