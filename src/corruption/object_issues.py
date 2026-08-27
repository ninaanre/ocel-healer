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
from .p2p_mappings import (
    get_p2p_object_type,
    get_p2p_object_table,
)


# ---------------------------------------------------------------------------
# Base injectors — building blocks reused by the tiered flavors below.
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
    """missing_object_type Easy: NULL the ocel_type of a payment object."""
    obj_type = get_p2p_object_type("employees")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (obj_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE object SET ocel_type = NULL WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_object_type_empty_string_order(conn: sqlite3.Connection) -> str | None:
    """missing_object_type Medium: Set ocel_type to '' on a purchase order."""
    obj_type = get_p2p_object_type("orders")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (obj_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE object SET ocel_type = '' WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_object_type_whitespace_product(conn: sqlite3.Connection) -> str | None:
    """missing_object_type Hard: Set ocel_type to a whitespace-only string
    on a material."""
    obj_type = get_p2p_object_type("products")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (obj_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE object SET ocel_type = '   ' WHERE ocel_id = ?", row)
    return row[0]


# ---------------------------------------------------------------------------
# missing_attribute_value (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_missing_attribute_value_null_product_weight(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Easy: NULL the weight of a material."""
    table = get_p2p_object_table("object_Products")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE QuantityEKPOMENGE IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    conn.execute(
        f"UPDATE {table} SET QuantityEKPOMENGE = NULL WHERE ocel_id = ?",
        row,
    )
    return [row[0]]


def inject_missing_attribute_value_empty_string_role(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Medium: Set payment amount to empty string."""
    table = get_p2p_object_table("object_employees")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE AmountDMBTR IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    conn.execute(
        f"UPDATE {table} SET AmountDMBTR = '' WHERE ocel_id = ?",
        row,
    )
    return [row[0]]


def inject_missing_attribute_value_null_order_price(conn: sqlite3.Connection) -> list[str]:
    """missing_attribute_value Hard: NULL vendor field in purchase order."""
    table = get_p2p_object_table("object_orders")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE VendorEKKOLIFNR IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    conn.execute(
        f"UPDATE {table} SET VendorEKKOLIFNR = NULL WHERE ocel_id = ?",
        row,
    )
    return [row[0]]


# ---------------------------------------------------------------------------
# incorrect_object_type (LLM-detected, Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_object_type_swap_order_to_employee(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Easy: Retype purchase_order as payment."""
    orders_type = get_p2p_object_type("orders")
    employee_type = get_p2p_object_type("employees")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (orders_type,)
    ).fetchone()
    if row is None:
        return None
    return inject_incorrect_object_type(conn, row[0], employee_type)


def inject_incorrect_object_type_swap_item_to_product(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Medium: Retype material as quotation."""
    items_type = get_p2p_object_type("items")
    products_type = get_p2p_object_type("products")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (items_type,)
    ).fetchone()
    if row is None:
        return None
    # Both map to 'material' in P2P, so use quotation as different type
    return inject_incorrect_object_type(conn, row[0], "quotation")


def inject_incorrect_object_type_case_variant_customers(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_type Hard: Retype purchase_requisition with case variant."""
    customers_type = get_p2p_object_type("customers")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (customers_type,)
    ).fetchone()
    if row is None:
        return None
    # Create case variant - uppercase
    return inject_incorrect_object_type(conn, row[0], "PURCHASE_REQUISITION")


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
    material NetPrice (REAL)."""
    table = get_p2p_object_table("object_Products")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE NetPriceEKPONETPR IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        f"UPDATE {table} SET NetPriceEKPONETPR = ? WHERE ocel_id = ?",
        ("unknown", row[0]),
    )
    return row[0]


def inject_incorrect_attribute_datatype_string_in_order_price(conn: sqlite3.Connection) -> str | None:
    """incorrect_attribute_datatype Medium: Put 'TBD' into purchase order vendor (TEXT expecting code)."""
    table = get_p2p_object_table("object_Orders")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE VendorEKKOLIFNR IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        f"UPDATE {table} SET VendorEKKOLIFNR = ? WHERE ocel_id = ?",
        ("TBD", row[0]),
    )
    return row[0]


def inject_incorrect_attribute_datatype_blob_in_role(conn: sqlite3.Connection) -> str | None:
    """incorrect_attribute_datatype Hard: Put UTF-16-LE bytes into
    payment AmountDMBTR field (TEXT)."""
    table = get_p2p_object_table("object_employees")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE AmountDMBTR IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        f"UPDATE {table} SET AmountDMBTR = ? WHERE ocel_id = ?",
        ("100.00".encode("utf-16-le"), row[0]),
    )
    return row[0]


# ---------------------------------------------------------------------------
# duplicate_objects_on_ids (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_objects_on_ids_product(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Easy: Duplicate a material row in object."""
    products_type = get_p2p_object_type("products")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (products_type,)
    ).fetchone()
    if row is None:
        return None
    return inject_duplicate_objects_on_ids(conn, ocel_id=row[0])


def inject_duplicate_objects_on_ids_conflicting_types(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Medium: Insert purchase_order with conflicting material type."""
    orders_type = get_p2p_object_type("orders")
    items_type = get_p2p_object_type("items")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (orders_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO object VALUES (?, ?)", (row[0], items_type))
    return row[0]


def inject_duplicate_objects_on_ids_triple_null_type(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_ids Hard: Add two extra rows for one purchase requisition, one with NULL type."""
    customers_type = get_p2p_object_type("customers")
    row = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (customers_type,)
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, None))
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, customers_type))
    return ocel_id


# ---------------------------------------------------------------------------
# duplicate_objects_on_attributes (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_objects_on_attributes_clone_product(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_attributes Easy: Clone a material's initial-state
    row under a fabricated id."""
    products_type = get_p2p_object_type("products")
    table = get_p2p_object_table("object_Products")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return _clone_object_row(
        conn,
        source_id=row[0],
        clone_id=f"{row[0]}-CLONE",
        ocel_type=products_type,
        table=table,
    )


def inject_duplicate_objects_on_attributes_clone_employee(conn: sqlite3.Connection) -> str | None:
    """duplicate_objects_on_attributes Medium: Insert two payment objects with
    same attributes - simulating duplicate master data records."""
    employee_type = get_p2p_object_type("employees")
    table = get_p2p_object_table("object_employees")

    original = "payment:original"
    clone = "payment:clone"
    fingerprint_time = "2023-04-03 01:00:00"
    amount = "1000.00"

    conn.execute("INSERT INTO object VALUES (?, ?)", (original, employee_type))
    conn.execute("INSERT INTO object VALUES (?, ?)", (clone, employee_type))
    conn.execute(
        f'INSERT INTO {table} (ocel_id, ocel_time, ocel_changed_field, AmountDMBTR) '
        "VALUES (?, ?, NULL, ?)",
        (original, fingerprint_time, amount),
    )
    conn.execute(
        f'INSERT INTO {table} (ocel_id, ocel_time, ocel_changed_field, AmountDMBTR) '
        "VALUES (?, ?, NULL, ?)",
        (clone, fingerprint_time, amount),
    )
    return clone


def inject_duplicate_objects_on_attributes_clone_order_and_referenced(conn: sqlite3.Connection) -> list[str]:
    """duplicate_objects_on_attributes Hard: Clone a purchase order AND copy its
    event_object rows."""
    orders_type = get_p2p_object_type("orders")
    table = get_p2p_object_table("object_orders")
    qualifier = get_p2p_object_type("orders")

    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return []

    original = row[0]
    clone = f"{original}-DUP"

    if not _clone_object_row(
        conn,
        source_id=original,
        clone_id=clone,
        ocel_type=orders_type,
        table=table,
    ):
        return []
    conn.execute(
        "INSERT INTO event_object (ocel_event_id, ocel_object_id, ocel_qualifier) "
        "SELECT ocel_event_id, ?, ocel_qualifier FROM event_object "
        "WHERE ocel_object_id = ?",
        (clone, original),
    )
    return [original, clone]


# ---------------------------------------------------------------------------
# incorrect_object_attribute_value (LLM detection, Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_object_attribute_value_negative_weight_easy(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_attribute_value Easy: Set material quantity to -999 (obviously wrong)."""
    table = get_p2p_object_table("object_Products")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE QuantityEKPOMENGE > 0 LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        f'UPDATE {table} SET QuantityEKPOMENGE = -999 WHERE ocel_id = ?',
        row,
    )
    return row[0]


def inject_incorrect_object_attribute_value_implausible_weight_hard(conn: sqlite3.Connection) -> str | None:
    """incorrect_object_attribute_value Hard: Set material NetPrice to 999999 (plausible format but wrong)."""
    table = get_p2p_object_table("object_Products")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} WHERE NetPriceEKPONETPR > 0 AND NetPriceEKPONETPR < 1000 LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        f'UPDATE {table} SET NetPriceEKPONETPR = 999999 WHERE ocel_id = ?',
        row,
    )
    return row[0]


# ---------------------------------------------------------------------------
# missing_object_attribute (LLM detection, Easy/Hard)
# Note: These require schema changes (DROP COLUMN) which are complex in SQLite.
# Marking as not implemented for now - would need ALTER TABLE workarounds.
# ---------------------------------------------------------------------------


def inject_missing_object_attribute_drop_price_easy(conn: sqlite3.Connection) -> str | None:
    """missing_object_attribute Easy: Drop price column from Products (schema change - not implemented)."""
    return None


def inject_missing_object_attribute_drop_optional_hard(conn: sqlite3.Connection) -> str | None:
    """missing_object_attribute Hard: Drop optional column (schema change - not implemented)."""
    return None
