"""Corruption injectors for the order-management OCEL SQLite log.

Each `inject_*` function introduces exactly one flavor of a known data-quality
issue and returns the affected ocel_id(s) so callers can log or reference them.
Injectors are grouped by the `Nx` code from the OCEL2 data-quality taxonomy
and, where multiple flavors exist, by difficulty tier (easy/medium/hard).

``corrupt_database`` is the single entry point used by the dashboard, notebooks
and tests. It copies the source SQLite file to the destination and runs the
selected `level`:

    legacy — the original hardcoded set (kept for backwards compatibility).
    easy   — one gentle flavor per detectable issue.
    medium — one moderate flavor per detectable issue.
    hard   — one adversarial flavor per detectable issue.
    all    — every injector (24 corruptions across 8 issue types).

    from src.corruption import corrupt_database, DEFAULT_CLEAN_PATH, DEFAULT_FULL_PATH
    from src.detection.error_detection import detect_all
    corrupt_database(DEFAULT_CLEAN_PATH, DEFAULT_FULL_PATH, level='all')
    for k, df in detect_all(DEFAULT_FULL_PATH).items():
        print(f'{k}: {df.height}')
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DEFAULT_CLEAN_PATH = str(_DATA_DIR / "order-management-clean.sqlite")
DEFAULT_DIRTY_PATH = str(_DATA_DIR / "order-management.sqlite")        # legacy output
DEFAULT_FULL_PATH = str(_DATA_DIR / "order-management-full.sqlite")     # level='all' output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def corrupt_database(
    src_path: str,
    dst_path: str | None = None,
    *,
    level: str = "legacy",
) -> str:
    """Copy `src_path` to `dst_path` and inject corruptions for `level`.

    When `dst_path` is None, the default per-level location under ``data/`` is
    used. Returns the resolved destination path.
    """
    dst_path = dst_path or _default_dst_for_level(level)
    shutil.copy2(src_path, dst_path)
    stages = _LEVEL_STAGES.get(level)
    if stages is None:
        raise ValueError(
            f"Unknown corruption level {level!r}; expected one of "
            f"{sorted(_LEVEL_STAGES)}"
        )
    with sqlite3.connect(dst_path) as conn:
        _remove_object_primary_key(conn)
        for stage in stages:
            stage(conn)
        conn.commit()
    return dst_path


def _default_dst_for_level(level: str) -> str:
    if level == "legacy":
        return DEFAULT_DIRTY_PATH
    if level == "all":
        return DEFAULT_FULL_PATH
    return str(_DATA_DIR / f"order-management-{level}.sqlite")


# ---------------------------------------------------------------------------
# Schema helper (shared prerequisite)
# ---------------------------------------------------------------------------


def _remove_object_primary_key(conn: sqlite3.Connection) -> None:
    """Recreate the object table without PRIMARY KEY so duplicate ocel_id rows
    can be inserted (needed by every N6a flavor).  Idempotent: no-op if the PK
    is already absent."""
    # sqlite_master carries the CREATE TABLE text; if it doesn't mention
    # PRIMARY KEY we're already done.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='object'"
    ).fetchone()
    if row is None or "PRIMARY KEY" not in (row[0] or "").upper():
        return
    conn.execute("CREATE TABLE object_tmp (ocel_id TEXT, ocel_type TEXT)")
    conn.execute("INSERT INTO object_tmp SELECT ocel_id, ocel_type FROM object")
    conn.execute("DROP TABLE object")
    conn.execute("ALTER TABLE object_tmp RENAME TO object")


# ---------------------------------------------------------------------------
# Legacy injectors — kept for the notebook and the `legacy` level
# ---------------------------------------------------------------------------


def inject_n6a(conn: sqlite3.Connection, ocel_id: str | None = None) -> str | None:
    """N6(a): Insert a fully duplicated object row (same ocel_id + ocel_type)."""
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


def inject_n6b(conn: sqlite3.Connection) -> str | None:
    """N6(b): Insert a new object whose per-type attributes match an existing
    one but under a different ocel_id (`{type}:CLONE_9999`)."""
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


def inject_n7a_incorrect_object_type(
    conn: sqlite3.Connection,
    ocel_id: str,
    wrong_type: str,
) -> str | None:
    """N7(a): Overwrite an object's ocel_type with a wrong-but-non-null value."""
    n = conn.execute(
        "UPDATE object SET ocel_type = ? WHERE ocel_id = ?",
        (wrong_type, ocel_id),
    ).rowcount
    return ocel_id if n > 0 else None


def inject_n10_object(conn: sqlite3.Connection) -> str | None:
    """N10: Insert an E2O row that references a non-existent object."""
    event = conn.execute("SELECT ocel_id FROM event LIMIT 1").fetchone()
    if event is None:
        return None
    fake_object_id = "FAKE_OBJECT:99999"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (event[0], fake_object_id, "unknown"),
    )
    return fake_object_id


def inject_n10_event(conn: sqlite3.Connection) -> str | None:
    """N10: Insert an E2O row that references a non-existent event."""
    obj = conn.execute("SELECT ocel_id FROM object WHERE ocel_type IS NOT NULL LIMIT 1").fetchone()
    if obj is None:
        return None
    fake_event_id = "FAKE_EVENT:99999"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (fake_event_id, obj[0], "unknown"),
    )
    return fake_event_id


def inject_n3a_missing_attribute(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    *,
    count: int = 1,
    ocel_ids: list[str] | None = None,
    changed_field_col: str = "ocel_changed_field",
) -> list[str]:
    """N3a: NULL `column` in `count` initial-state rows of `table`.

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


# ---------------------------------------------------------------------------
# N2 — missing_object_type (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_n2_null_employee(conn: sqlite3.Connection) -> str | None:
    """N2 Easy: NULL the ocel_type of a specific, well-known employee.

    Before: ocel_id='Wil van der Aalst', ocel_type='employees'.
    Easy because the id `Wil van der Aalst` looks unmistakably like a person
    name and only appears in `object_Employees`, so the resolver has strong
    signal.
    """
    return _null_type_for(conn, "Wil van der Aalst", set_to=None)


def inject_n2_empty_string_order(conn: sqlite3.Connection) -> str | None:
    """N2 Medium: Set ocel_type to '' on a specific order.

    Before: ocel_id='o-990010', ocel_type='orders'.
    Medium because empty-string types trip the detector (which treats
    whitespace as missing) but resolvers that key on ``IS NULL`` will miss it,
    and the id `o-990010` is only mildly informative on its own.
    """
    return _null_type_for(conn, "o-990010", set_to="")


def inject_n2_whitespace_product(conn: sqlite3.Connection) -> str | None:
    """N2 Hard: Set ocel_type to a whitespace-only string on a product.

    Before: ocel_id='MacBook Pro', ocel_type='products'.
    Hard because the visible symptom is subtle (three spaces render as a
    blank cell) and the resolver must fall back on membership in
    `object_Products` to recover the type.
    """
    return _null_type_for(conn, "MacBook Pro", set_to="   ")


def _null_type_for(
    conn: sqlite3.Connection, ocel_id: str, *, set_to: str | None
) -> str | None:
    n = conn.execute(
        "UPDATE object SET ocel_type = ? WHERE ocel_id = ? AND ocel_type IS NOT NULL "
        "AND (ocel_type != '' OR ? IS NULL)",
        (set_to, ocel_id, set_to),
    ).rowcount
    return ocel_id if n > 0 else None


# ---------------------------------------------------------------------------
# N3a — missing_attribute_value (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_n3a_null_product_weight(conn: sqlite3.Connection) -> list[str]:
    """N3a Easy: NULL the weight of `iPhone 8` (id-as-name hint applies).

    Before: object_Products.weight=0.21 for ocel_id='iPhone 8'.
    Easy because the hints file marks products as ``id_is_name: true`` for
    ``weight``, so the resolver can look up the real-world weight directly
    from general knowledge.
    """
    return inject_n3a_missing_attribute(
        conn, "object_Products", "weight", ocel_ids=["iPhone 8"]
    )


def inject_n3a_empty_string_role(conn: sqlite3.Connection) -> list[str]:
    """N3a Medium: Set `role` to '' for `Christine von Dobbert`.

    Before: object_Employees.role='Sales' for ocel_id='Christine von Dobbert'.
    Medium because roles have a small closed vocabulary but there's no
    external ground truth — the resolver must infer from peer employees.
    """
    n = conn.execute(
        'UPDATE object_Employees SET role = \'\' WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL AND role IS NOT NULL',
        ("Christine von Dobbert",),
    ).rowcount
    return ["Christine von Dobbert"] if n > 0 else []


def inject_n3a_null_order_price(conn: sqlite3.Connection) -> list[str]:
    """N3a Hard: NULL the initial `price` of order `o-990050`.

    Before: object_Orders.price=6518.96 for ocel_id='o-990050'.
    Hard because order prices are not derivable from external knowledge or
    from peer orders (each order has a unique basket); the resolver may need
    to sum linked item prices, or decline with low confidence.
    """
    return inject_n3a_missing_attribute(
        conn, "object_Orders", "price", ocel_ids=["o-990050"]
    )


# ---------------------------------------------------------------------------
# N7a — incorrect_object_type (LLM-detected, Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_n7a_swap_order_to_employee(conn: sqlite3.Connection) -> str | None:
    """N7a Easy: Retype order `o-990001` as `employees`.

    Before: object.ocel_type='orders' for ocel_id='o-990001'.
    Easy because an order id (`o-990001`) reads nothing like an employee
    name and its events (`place order`, `confirm order`, ...) don't match
    what employees do.
    """
    return inject_n7a_incorrect_object_type(conn, "o-990001", "employees")


def inject_n7a_swap_item_to_product(conn: sqlite3.Connection) -> str | None:
    """N7a Medium: Retype item `i-880100` as `products`.

    Before: object.ocel_type='items' for ocel_id='i-880100'.
    Medium because items and products share the same per-type schema
    (`weight`, `price`), so the LLM must disambiguate via the id pattern
    (`i-*` vs product-name) and the events the object participates in.
    """
    return inject_n7a_incorrect_object_type(conn, "i-880100", "products")


def inject_n7a_case_variant_customers(conn: sqlite3.Connection) -> str | None:
    """N7a Hard: Retype a customer as `CUSTOMERS` (case variant).

    Before: object.ocel_type='customers' for ocel_id='Carpathian Financial Services plc'.
    Hard because the wrong type is one uppercase-transform away from the
    right answer, so a naive prompt might normalise it silently.
    """
    return inject_n7a_incorrect_object_type(
        conn, "Carpathian Financial Services plc", "CUSTOMERS"
    )


# ---------------------------------------------------------------------------
# incorrect_attribute_datatype (new, Easy/Medium/Hard)
#
# SQLite type affinity coerces most naive datatype mismatches (e.g. '42' in
# an INTEGER column becomes int 42). The reliable recipes are:
#   * non-numeric strings in REAL/INT columns  -> stored as TEXT
#   * bytes objects in TEXT columns            -> stored as BLOB
# All three flavors below use one of these.
# ---------------------------------------------------------------------------


def inject_datatype_string_in_weight(conn: sqlite3.Connection) -> str | None:
    """Datatype Easy: Put `'unknown'` into `object_Products.weight` (REAL).

    Before: object_Products.weight=0.44 (REAL) for ocel_id='iPad Air'.
    Easy because the fix is to look up the real weight of `iPad Air`, which
    the resolver can recall from general knowledge (id-as-name hint).
    """
    n = conn.execute(
        'UPDATE object_Products SET weight = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        ("unknown", "iPad Air"),
    ).rowcount
    return "iPad Air" if n > 0 else None


def inject_datatype_string_in_order_price(conn: sqlite3.Connection) -> str | None:
    """Datatype Medium: Put `'TBD'` into `object_Orders.price` (REAL).

    Before: object_Orders.price=5383.99 (REAL) for ocel_id='o-990200'.
    Medium because the resolver has to coerce to a numeric type but there's
    no external truth for the concrete order value — it must lean on
    linked items or return low confidence.
    """
    n = conn.execute(
        'UPDATE object_Orders SET price = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        ("TBD", "o-990200"),
    ).rowcount
    return "o-990200" if n > 0 else None


def inject_datatype_blob_in_role(conn: sqlite3.Connection) -> str | None:
    """Datatype Hard: Put a `bytes` blob into `object_Employees.role` (TEXT).

    Before: object_Employees.role='Sales' (TEXT) for ocel_id='Jan Niklas Adams'.
    Hard because the stored value is opaque binary — the resolver must
    infer the intended role from peer employees rather than any hint from
    the corrupted value itself.
    """
    n = conn.execute(
        'UPDATE object_Employees SET role = ? WHERE ocel_id = ? '
        'AND ocel_changed_field IS NULL',
        (b"\x00\x00\x01", "Jan Niklas Adams"),
    ).rowcount
    return "Jan Niklas Adams" if n > 0 else None


# ---------------------------------------------------------------------------
# N6a — duplicate_objects_on_ids (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_dup_id_product(conn: sqlite3.Connection) -> str | None:
    """N6a Easy: Duplicate the (`Echo Dot`, `products`) row in `object`.

    Before: object has 1 row for ocel_id='Echo Dot' with ocel_type='products'.
    """
    return inject_n6a(conn, ocel_id="Echo Dot")


def inject_dup_id_conflicting_types(conn: sqlite3.Connection) -> str | None:
    """N6a Medium: Insert `('o-990300', 'items')` next to the real order row.

    Before: object has 1 row for ocel_id='o-990300' with ocel_type='orders'.
    The duplicate row has a DIFFERENT ocel_type; the deterministic delete
    path used by the resolver requires all duplicates to share one type, so
    this flavor exercises the `unrouted` review branch.
    """
    conn.execute("INSERT INTO object VALUES (?, ?)", ("o-990300", "items"))
    return "o-990300"


def inject_dup_id_triple_null_type(conn: sqlite3.Connection) -> str | None:
    """N6a Hard: Add two extra rows for one customer id, one with NULL type.

    Before: object has 1 row for ocel_id='AlpenTech Innovations AG' with
    ocel_type='customers'.
    Produces `count = 3` for that ocel_id and simultaneously seeds
    `missing_object_type` — the resolver has to reconcile three
    occurrences one of which is un-typed.
    """
    ocel_id = "AlpenTech Innovations AG"
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, None))
    conn.execute("INSERT INTO object VALUES (?, ?)", (ocel_id, "customers"))
    return ocel_id


# ---------------------------------------------------------------------------
# N6b — duplicate_objects_on_attributes (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_n6b_clone_product(conn: sqlite3.Connection) -> str | None:
    """N6b Easy: Clone `Echo Dot`'s initial-state row under a fabricated id.

    Before: object_Products for ocel_id='Echo Dot' has a single initial row
    (ocel_time='2023-04-03 01:00:00', weight=0.38, price=29.99). After
    injection, ocel_id='products:CLONE_ECHO_DOT' carries the same tuple.
    """
    return _clone_object_row(
        conn,
        source_id="Echo Dot",
        clone_id="products:CLONE_ECHO_DOT",
        ocel_type="products",
        table="object_Products",
    )


def inject_n6b_clone_employee(conn: sqlite3.Connection) -> str | None:
    """N6b Medium: Insert two employees with identical fabricated attributes.

    Before: no `Consulting` role exists in object_Employees (roles are
    Sales / Shipment / Warehousing).
    Medium because cloning a person is a more delicate merge decision than
    cloning a product SKU — the resolver has to commit to keeping one.
    Uses a fabricated `role='Consulting'` so the two new rows form a fresh
    duplicate group instead of joining the existing role clusters (Sales /
    Shipment / Warehousing all already share fingerprints).
    """
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


def inject_n6b_clone_order_and_referenced(conn: sqlite3.Connection) -> list[str]:
    """N6b Hard: Clone an order AND copy its `event_object` rows.

    Before: object_Orders for ocel_id='o-990500' has a single initial row
    (ocel_time='2023-07-13 10:58:40', price=4199.53) and a set of E2O rows
    linking it to place/pay/confirm events. After injection, ocel_id
    'o-990500-DUP' carries the same tuple and the same E2O membership.

    Because the clone participates in the same events as the original, any
    resolver that deletes the clone must first move those E2O rows onto
    the surviving id.
    """
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


# ---------------------------------------------------------------------------
# N10 — dangling_e2o_relationship (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_n10_missing_object_easy(conn: sqlite3.Connection) -> str | None:
    """N10 Easy: E2O row references a nonexistent object (`missing_side='object'`).

    Before: no E2O row referenced `FAKE_OBJECT:99999`; that id does not appear
    in `object`. After injection, one E2O row on a real event points at it.
    """
    return inject_n10_object(conn)


def inject_n10_missing_event(conn: sqlite3.Connection) -> str | None:
    """N10 Medium: E2O row references a nonexistent event (`missing_side='event'`).

    Before: no E2O row referenced `FAKE_EVENT:99999`; that id does not appear
    in `event`. After injection, one E2O row from a real object points at it.
    """
    return inject_n10_event(conn)


def inject_n10_missing_both(conn: sqlite3.Connection) -> tuple[str, str]:
    """N10 Hard: E2O row where BOTH endpoints are nonexistent.

    Before: neither `FAKE_EVENT:66666` nor `FAKE_OBJECT:66666` appears in
    `event` or `object`.
    Hard because the resolver has no anchor context at all — neither the
    event nor the object is real.
    """
    fake_event = "FAKE_EVENT:66666"
    fake_object = "FAKE_OBJECT:66666"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (fake_event, fake_object, "unknown"),
    )
    return fake_event, fake_object


# ---------------------------------------------------------------------------
# dangling_o2o_relationship (new, Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_o2o_missing_source(conn: sqlite3.Connection) -> tuple[str, str]:
    """O2O Easy: Source id is nonexistent, target is a real employee.

    Before: no O2O row referenced `GHOST_SRC:1`; target `Wil van der Aalst`
    exists as an employee.
    """
    src = "GHOST_SRC:1"
    dst = "Wil van der Aalst"
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


def inject_o2o_missing_target(conn: sqlite3.Connection) -> tuple[str, str]:
    """O2O Medium: Real customer references a nonexistent employee id.

    Before: source `Balkan Minerals d.o.o.` exists as a customer; no employee
    or object has id `GHOST_EMP:1`.
    """
    src = "Balkan Minerals d.o.o."
    dst = "GHOST_EMP:1"
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


def inject_o2o_missing_both_typo(conn: sqlite3.Connection) -> tuple[str, str]:
    """O2O Hard: Both endpoints are typo near-misses of real ids.

    Before: real ids are `AlpenTech Innovations AG` (customer) and
    `Wil van der Aalst` (employee); the injected row uses one-character
    variants of each.
    Hard because the resolver must reason about string similarity to pick
    the intended pair.
    """
    src = "AlpenTech Innovation AG"      # real id: 'AlpenTech Innovations AG'
    dst = "Wil van der Aallst"           # real id: 'Wil van der Aalst'
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


# ---------------------------------------------------------------------------
# Tier stages
# ---------------------------------------------------------------------------


def _stage_legacy(conn: sqlite3.Connection) -> None:
    """Reproduce the pre-`level` corruption sequence exactly."""
    n6a_id = inject_n6a(conn)
    inject_n6b(conn)
    inject_n2(conn, exclude_id=n6a_id)
    inject_n10_object(conn)
    inject_n10_event(conn)


def _stage_easy(conn: sqlite3.Connection) -> None:
    inject_dup_id_product(conn)                # N6a first (uses PK-less object table)
    inject_n2_null_employee(conn)              # then N2 (on a disjoint id)
    inject_n3a_null_product_weight(conn)
    inject_n7a_swap_order_to_employee(conn)
    inject_datatype_string_in_weight(conn)
    inject_n6b_clone_product(conn)
    inject_n10_missing_object_easy(conn)
    inject_o2o_missing_source(conn)


def _stage_medium(conn: sqlite3.Connection) -> None:
    inject_dup_id_conflicting_types(conn)
    inject_n2_empty_string_order(conn)
    inject_n3a_empty_string_role(conn)
    inject_n7a_swap_item_to_product(conn)
    inject_datatype_string_in_order_price(conn)
    inject_n6b_clone_employee(conn)
    inject_n10_missing_event(conn)
    inject_o2o_missing_target(conn)


def _stage_hard(conn: sqlite3.Connection) -> None:
    inject_dup_id_triple_null_type(conn)
    inject_n2_whitespace_product(conn)
    inject_n3a_null_order_price(conn)
    inject_n7a_case_variant_customers(conn)
    inject_datatype_blob_in_role(conn)
    inject_n6b_clone_order_and_referenced(conn)
    inject_n10_missing_both(conn)
    inject_o2o_missing_both_typo(conn)


_LEVEL_STAGES: dict[str, list[Callable[[sqlite3.Connection], None]]] = {
    "legacy": [_stage_legacy],
    "easy":   [_stage_easy],
    "medium": [_stage_medium],
    "hard":   [_stage_hard],
    "all":    [_stage_easy, _stage_medium, _stage_hard],
}
