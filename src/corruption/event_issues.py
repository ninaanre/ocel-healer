"""Event-side corruption injectors.

Covers issues rooted in the `event` table and its per-type sub-tables:
E2O rows referencing non-existent events (`missing_event`), `event.ocel_type`
values that are NULL / empty / whitespace (`missing_event_type`), and
NULL / empty `ocel_time` values on `event_<Type>` rows
(`missing_event_timestamp`).

Each `inject_*` function introduces exactly one flavor and returns the
affected id(s) so callers can log or reference them.
"""

from __future__ import annotations

import sqlite3


# ---------------------------------------------------------------------------
# missing_event_timestamp — a row in `event_<Type>` has NULL or empty
# ocel_time. Rule detector via UNION over event sub-tables; LLM resolver
# interpolates from neighbor events touching the same object(s).
# ---------------------------------------------------------------------------


def inject_missing_event_timestamp_null_place_order_easy(conn: sqlite3.Connection) -> str | None:
    """missing_event_timestamp Easy: NULL the ocel_time on one `event_PlaceOrder` row.

    Easy because `place order` is the first event in the lifecycle, so its
    neighbors are all upper bounds — the resolver has a clear "must be
    before X" signal from the order's later events.
    """
    row = conn.execute("SELECT ocel_id FROM event_PlaceOrder LIMIT 1").fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event_PlaceOrder SET ocel_time = NULL WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_event_timestamp_empty_pick_item_medium(conn: sqlite3.Connection) -> str | None:
    """missing_event_timestamp Medium: Empty-string ocel_time on one `event_PickItem` row.

    Medium because empty-string trips the detector but a naive `IS NULL`
    filter misses it, and `pick item` sits deep in the lifecycle with
    tighter bracketing constraints from both sides.
    """
    row = conn.execute("SELECT ocel_id FROM event_PickItem LIMIT 1").fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event_PickItem SET ocel_time = '' WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_event_timestamp_null_item_out_of_stock_hard(conn: sqlite3.Connection) -> str | None:
    """missing_event_timestamp Hard: NULL the ocel_time on one `event_ItemOutOfStock` row.

    Hard because `item out of stock` is a rare, off-happy-path event with
    sparse peer signal — bracketing must come from the item's own history
    rather than a typical order lifecycle.
    """
    row = conn.execute("SELECT ocel_id FROM event_ItemOutOfStock LIMIT 1").fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event_ItemOutOfStock SET ocel_time = NULL WHERE ocel_id = ?", row)
    return row[0]


# ---------------------------------------------------------------------------
# missing_event — an event_object row references an event id that has no
# row in the `event` table. Rule detector via LEFT JOIN; resolver INSERTs
# the event row (plus an initial-state row in the per-type sub-table).
# ---------------------------------------------------------------------------


def inject_missing_event_place_order_easy(conn: sqlite3.Connection) -> str | None:
    """Missing event, Easy: E2O row references `place_order:e-991000` (no
    corresponding row in `event`).

    Easy because the fake id embeds the activity keyword (`place_order:…`),
    so the resolver has a clear signal for what event type + sub-table to
    insert into. The E2O row is linked to a real order object so the
    lifecycle context is intact.
    """
    obj = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = 'orders' LIMIT 1"
    ).fetchone()
    if obj is None:
        return None
    missing_event_id = "place_order:e-991000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (missing_event_id, obj[0], "order"),
    )
    return missing_event_id


def inject_missing_event_pick_item_medium(conn: sqlite3.Connection) -> str | None:
    """Missing event, Medium: E2O row references `pick_item:e-881000`.

    Medium because `pick item` is mid-lifecycle — the resolver must
    interpolate a plausible timestamp from bracketing neighbors, not just
    read it off a single anchor. The fake id still embeds the activity
    keyword, so the event type is unambiguous.
    """
    obj = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = 'items' LIMIT 1"
    ).fetchone()
    if obj is None:
        return None
    missing_event_id = "pick_item:e-881000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (missing_event_id, obj[0], "item"),
    )
    return missing_event_id


def inject_missing_event_bare_id_hard(conn: sqlite3.Connection) -> str | None:
    """Missing event, Hard: E2O row references `e-771000` — a bare id with
    no `<type>:` prefix.

    Hard because the fake id carries no activity hint. The resolver must
    infer the event type purely from the qualifier and the object type of
    the linked object (here, a `package`, with qualifier `packer` — a
    strong hint at `create package` or `send package`).
    """
    obj = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = 'packages' LIMIT 1"
    ).fetchone()
    if obj is None:
        return None
    missing_event_id = "e-771000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (missing_event_id, obj[0], "packer"),
    )
    return missing_event_id


# ---------------------------------------------------------------------------
# missing_event_type — a row in `event` has NULL or empty ocel_type.
# Rule detector; LLM resolver UPDATEs `event.ocel_type` from the objects
# the event touches, its id, and its timestamp.
# ---------------------------------------------------------------------------


def inject_missing_event_type_null_confirm_easy(conn: sqlite3.Connection) -> str | None:
    """Missing event type, Easy: NULL the ocel_type of one `confirm order` event.

    Easy because the event's id embeds the activity keyword
    (`confirm order:…`), giving the LLM a strong single-signal hint at
    the type. Also linked to one `orders` object with qualifier `order`,
    which corroborates.
    """
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'confirm order' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = NULL WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_event_type_empty_pay_medium(conn: sqlite3.Connection) -> str | None:
    """Missing event type, Medium: Empty-string ocel_type on one `pay order` event.

    Medium because empty-string trips the detector but a naive `IS NULL`
    filter misses it. The id (`pay order:…`) and the linked `orders`
    object give the LLM the signal to recover the type.
    """
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'pay order' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = '' WHERE ocel_id = ?", row)
    return row[0]


def inject_missing_event_type_whitespace_package_hard(conn: sqlite3.Connection) -> str | None:
    """Missing event type, Hard: Whitespace-only ocel_type on one `create
    package` event.

    Hard because whitespace passes `NOT NULL AND != ''` naive checks;
    detection requires TRIM. Also because `create package` and
    `send package` both touch package objects — the LLM must
    disambiguate via the id keyword or the qualifier of the packer
    object (`packer` for create, different for send).
    """
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'create package' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = '   ' WHERE ocel_id = ?", row)
    return row[0]


# ---------------------------------------------------------------------------
# missing_event_attribute_value (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_missing_event_attribute_value_null_order_id_easy(conn: sqlite3.Connection) -> str | None:
    """missing_event_attribute_value Easy: NULL an order_id in event_PlaceOrder.

    Easy because PlaceOrder is common and order_id is obviously required."""
    # Check if order_id column exists first
    cols = [c[1] for c in conn.execute("PRAGMA table_info(event_PlaceOrder)").fetchall()]
    if "order_id" not in cols:
        return None
    row = conn.execute(
        "SELECT ocel_id FROM event_PlaceOrder  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE event_PlaceOrder SET order_id = NULL WHERE ocel_id = ?",
        row,
    )
    return row[0]


def inject_missing_event_attribute_value_null_reason_hard(conn: sqlite3.Connection) -> str | None:
    """missing_event_attribute_value Hard: NULL a reason in event_ItemOutOfStock.

    Hard because ItemOutOfStock is rare and reason is a less obvious required field."""
    row = conn.execute(
        "SELECT ocel_id FROM event_ItemOutOfStock  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    # Check if reason column exists first
    cols = [c[1] for c in conn.execute("PRAGMA table_info(event_ItemOutOfStock)").fetchall()]
    if "reason" not in cols:
        return None
    conn.execute(
        "UPDATE event_ItemOutOfStock SET reason = NULL WHERE ocel_id = ?",
        row,
    )
    return row[0]


# ---------------------------------------------------------------------------
# incorrect_event_attribute_datatype (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_attribute_datatype_string_in_quantity_easy(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_attribute_datatype Easy: Put 'unknown' in a numeric quantity field."""
    # Check if PickItem has quantity field
    cols = [c[1] for c in conn.execute("PRAGMA table_info(event_PickItem)").fetchall()]
    if "quantity" not in cols:
        return None
    row = conn.execute(
        "SELECT ocel_id FROM event_PickItem  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE event_PickItem SET quantity = 'unknown' WHERE ocel_id = ?",
        row,
    )
    return row[0]


def inject_incorrect_event_attribute_datatype_blob_in_activity_hard(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_attribute_datatype Hard: Put UTF-16-LE bytes in a text field."""
    # Check if order_id column exists first
    cols = [c[1] for c in conn.execute("PRAGMA table_info(event_PlaceOrder)").fetchall()]
    if "order_id" not in cols:
        return None
    row = conn.execute(
        "SELECT ocel_id FROM event_PlaceOrder  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    # Put bytes in order_id field
    conn.execute(
        "UPDATE event_PlaceOrder SET order_id = ? WHERE ocel_id = ?",
        ("order".encode("utf-16-le"), row[0]),
    )
    return row[0]


# ---------------------------------------------------------------------------
# incorrect_event_attribute_value (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_attribute_value_negative_quantity_easy(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_attribute_value Easy: Negative quantity in PickItem."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(event_PickItem)").fetchall()]
    if "quantity" not in cols:
        return None
    row = conn.execute(
        "SELECT ocel_id FROM event_PickItem  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE event_PickItem SET quantity = -50 WHERE ocel_id = ?",
        row,
    )
    return row[0]


def inject_incorrect_event_attribute_value_time_violation_hard(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_attribute_value Hard: ConfirmOrder before PlaceOrder (temporal violation)."""
    # Find a PlaceOrder and its corresponding ConfirmOrder
    row = conn.execute("""
        SELECT po.ocel_id as place_id, co.ocel_id as confirm_id, po.ocel_time, co.ocel_time
        FROM event_PlaceOrder po
        JOIN event_object eo1 ON po.ocel_id = eo1.ocel_event_id
        JOIN event_object eo2 ON eo1.ocel_object_id = eo2.ocel_object_id AND eo2.ocel_qualifier = 'order'
        JOIN event_ConfirmOrder co ON eo2.ocel_event_id = co.ocel_id
        LIMIT 1
    """).fetchone()
    if row is None:
        return None
    place_id, confirm_id, place_time, confirm_time = row
    # Swap the times to create violation
    conn.execute(
        "UPDATE event_ConfirmOrder SET ocel_time = ? WHERE ocel_id = ?",
        (place_time, confirm_id),
    )
    conn.execute(
        "UPDATE event_PlaceOrder SET ocel_time = ? WHERE ocel_id = ?",
        (confirm_time, place_id),
    )
    return confirm_id


# ---------------------------------------------------------------------------
# incorrect_event_type (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_type_swap_easy(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_type Easy: Change PlaceOrder type to PickItem (completely wrong)."""
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'place order' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = 'pick item' WHERE ocel_id = ?", row)
    return row[0]


def inject_incorrect_event_type_case_variant_hard(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_type Hard: Change PlaceOrder to placeorder (case variant)."""
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'place order' LIMIT 1 OFFSET 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = 'placeorder' WHERE ocel_id = ?", row)
    return row[0]


# ---------------------------------------------------------------------------
# incorrect_event_time (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_time_future_easy(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_time Easy: Set event time to year 2099."""
    row = conn.execute(
        "SELECT ocel_id FROM event_PlaceOrder  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE event_PlaceOrder SET ocel_time = '2099-01-01 00:00:00' WHERE ocel_id = ?",
        row,
    )
    return row[0]


def inject_incorrect_event_time_past_hard(conn: sqlite3.Connection) -> str | None:
    """incorrect_event_time Hard: Set event time to year 1900."""
    row = conn.execute(
        "SELECT ocel_id FROM event_ConfirmOrder  LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE event_ConfirmOrder SET ocel_time = '1900-01-01 00:00:00' WHERE ocel_id = ?",
        row,
    )
    return row[0]


# ---------------------------------------------------------------------------
# duplicate_events_on_ids (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_events_on_ids_easy(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_ids Easy: Duplicate an event row identically."""
    row = conn.execute(
        "SELECT ocel_id, ocel_type FROM event WHERE ocel_type = 'place order' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO event VALUES (?, ?)", row)
    return row[0]


def inject_duplicate_events_on_ids_conflicting_types_hard(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_ids Hard: Duplicate event with conflicting type."""
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = 'place order' LIMIT 1 OFFSET 2"
    ).fetchone()
    if row is None:
        return None
    # Insert duplicate with different type
    conn.execute("INSERT INTO event VALUES (?, ?)", (row[0], "pick item"))
    return row[0]


# ---------------------------------------------------------------------------
# duplicate_events_on_attributes (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_events_on_attributes_clone_easy(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_attributes Easy: Clone a PlaceOrder event."""
    row = conn.execute("""
        SELECT ocel_id, ocel_time
        FROM event_PlaceOrder

        LIMIT 1
    """).fetchone()
    if row is None:
        return None
    orig_id, time = row
    clone_id = f"{orig_id}-CLONE"
    # Insert into event table
    conn.execute("INSERT INTO event VALUES (?, ?)", (clone_id, "place order"))
    # Insert into event_PlaceOrder
    conn.execute(
        "INSERT INTO event_PlaceOrder (ocel_id, ocel_time) VALUES (?, ?)",
        (clone_id, time),
    )
    return clone_id


def inject_duplicate_events_on_attributes_clone_with_refs_hard(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_attributes Hard: Clone event AND its event_object refs."""
    row = conn.execute("""
        SELECT ocel_id, ocel_time
        FROM event_PlaceOrder

        LIMIT 1 OFFSET 1
    """).fetchone()
    if row is None:
        return None
    orig_id, time = row
    clone_id = f"{orig_id}-CLONE"
    # Insert into event table
    conn.execute("INSERT INTO event VALUES (?, ?)", (clone_id, "place order"))
    # Insert into event_PlaceOrder
    conn.execute(
        "INSERT INTO event_PlaceOrder (ocel_id, ocel_time) VALUES (?, ?)",
        (clone_id, time),
    )
    # Clone event_object refs
    conn.execute(
        "INSERT INTO event_object (ocel_event_id, ocel_object_id, ocel_qualifier) "
        "SELECT ?, ocel_object_id, ocel_qualifier FROM event_object WHERE ocel_event_id = ?",
        (clone_id, orig_id),
    )
    return clone_id


# ---------------------------------------------------------------------------
# missing_event_attribute (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_missing_event_attribute_drop_order_id_easy(conn: sqlite3.Connection) -> str | None:
    """missing_event_attribute Easy: Drop order_id column from event_PlaceOrder.

    Schema changes not supported - returns None."""
    return None


def inject_missing_event_attribute_drop_optional_hard(conn: sqlite3.Connection) -> str | None:
    """missing_event_attribute Hard: Drop optional column from event table.

    Schema changes not supported - returns None."""
    return None
