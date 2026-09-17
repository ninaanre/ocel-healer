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

from src.corruption._common import _capture_and_update
from .p2p_mappings import (
    get_p2p_event_table,
    get_p2p_event_type,
    get_p2p_object_type,
    get_p2p_e2o_qualifier,
)


# ---------------------------------------------------------------------------
# missing_event_timestamp — a row in `event_<Type>` has NULL or empty
# ocel_time. Rule detector via UNION over event sub-tables; LLM resolver
# interpolates from neighbor events touching the same object(s).
# ---------------------------------------------------------------------------


def _pick_event_with_relations(
    conn: sqlite3.Connection, table: str, *, require_neighbor: bool = False
) -> str | None:
    """Pick an `ocel_id` from `table` that's actually resolvable, instead of
    blindly grabbing the first row.

    Without this, `SELECT ocel_id FROM {table} LIMIT 1` (no ORDER BY, no
    relational check) deterministically returns whichever row SQLite
    happens to store first -- on the real P2P dataset that can easily be an
    event with zero `event_object` rows at all, which makes the resulting
    "infer the missing timestamp from neighbor events" task impossible by
    construction, not merely hard. Confirmed via `resolution_noop_reasons`:
    both easy and hard runs landed on such an event, every run, for exactly
    this reason.

    `require_neighbor=True` (used for "easy") additionally requires at
    least one OTHER event to share an object with the candidate, so the
    tier's own premise -- "clear bracketing signal" -- actually holds.
    "medium"/"hard" only require the event isn't fully orphaned, preserving
    their intended sparse-signal difficulty rather than guaranteeing a
    clean bracket.
    """
    if require_neighbor:
        query = f"""
            SELECT DISTINCT e.ocel_id
            FROM "{table}" e
            JOIN event_object eo ON eo.ocel_event_id = e.ocel_id
            JOIN event_object eo2
                ON eo2.ocel_object_id = eo.ocel_object_id
               AND eo2.ocel_event_id != e.ocel_id
            LIMIT 1
        """
    else:
        query = f"""
            SELECT e.ocel_id
            FROM "{table}" e
            WHERE EXISTS (
                SELECT 1 FROM event_object eo WHERE eo.ocel_event_id = e.ocel_id
            )
            LIMIT 1
        """
    row = conn.execute(query).fetchone()
    return row[0] if row else None


def inject_missing_event_timestamp_null_place_order_easy(conn: sqlite3.Connection) -> dict | None:
    """missing_event_timestamp Easy: NULL the ocel_time on one `event_PlaceOrder` row.

    Easy because `place order` is the first event in the lifecycle, so its
    neighbors are all upper bounds — the resolver has a clear "must be
    before X" signal from the order's later events. The candidate is
    required to actually have such a neighbor (see
    `_pick_event_with_relations`) so that promise holds in practice.
    """
    table = get_p2p_event_table("event_PlaceOrder")
    ocel_id = _pick_event_with_relations(conn, table, require_neighbor=True)
    if ocel_id is None:
        return None
    old_value = _capture_and_update(conn, table, "ocel_time", ocel_id, None)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_missing_event_timestamp_empty_pick_item_medium(conn: sqlite3.Connection) -> dict | None:
    """missing_event_timestamp Medium: Empty-string ocel_time on one `event_PickItem` row.

    Medium because empty-string trips the detector but a naive `IS NULL`
    filter misses it, and `pick item` sits deep in the lifecycle with
    tighter bracketing constraints from both sides. The candidate is only
    required to have SOME relation (not fully orphaned) — sparser than
    "easy"'s guaranteed neighbor, on purpose.
    """
    table = get_p2p_event_table("event_PickItem")
    ocel_id = _pick_event_with_relations(conn, table, require_neighbor=False)
    if ocel_id is None:
        return None
    old_value = _capture_and_update(conn, table, "ocel_time", ocel_id, "")
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_missing_event_timestamp_null_item_out_of_stock_hard(conn: sqlite3.Connection) -> dict | None:
    """missing_event_timestamp Hard: NULL the ocel_time on one `event_ItemOutOfStock` row.

    Hard because `item out of stock` is a rare, off-happy-path event with
    sparse peer signal — bracketing must come from the item's own history
    rather than a typical order lifecycle. Still requires at least one
    `event_object` relation to exist so the task is merely hard, not
    unsolvable: a fully orphaned event isn't a harder reasoning case, it's
    a degenerate one no method could bracket.
    """
    table = get_p2p_event_table("event_ItemOutOfStock")
    ocel_id = _pick_event_with_relations(conn, table, require_neighbor=False)
    if ocel_id is None:
        return None
    old_value = _capture_and_update(conn, table, "ocel_time", ocel_id, None)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


# ---------------------------------------------------------------------------
# missing_event — an event_object row references an event id that has no
# row in the `event` table. Rule detector via LEFT JOIN; resolver INSERTs
# the event row (plus an initial-state row in the per-type sub-table).
# ---------------------------------------------------------------------------


def inject_missing_event_place_order_easy(conn: sqlite3.Connection) -> dict | None:
    """Missing event, Easy: delete a real 'place order' event that is still
    referenced by its E2O row (qualifier `order`), leaving that row dangling.

    Easy because `place order` is the first event in the lifecycle, so the
    linked order object is still intact, and the surviving E2O qualifier
    gives a clear signal for what event type belongs there.
    """
    place_type = get_p2p_event_type("place order")
    order_qual = get_p2p_e2o_qualifier("order")
    row = conn.execute(
        "SELECT e.ocel_id, e.ocel_type FROM event e "
        "JOIN event_object eo ON eo.ocel_event_id = e.ocel_id "
        "WHERE e.ocel_type = ? AND eo.ocel_qualifier = ? LIMIT 1",
        (place_type, order_qual),
    ).fetchone()
    if row is None:
        return None
    ocel_id, ocel_type = row
    conn.execute("DELETE FROM event WHERE ocel_id = ?", (ocel_id,))
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: ocel_type}}


def inject_missing_event_pick_item_medium(conn: sqlite3.Connection) -> dict | None:
    """Missing event, Medium: delete a real 'pick item' event that is still
    referenced by its E2O row (qualifier `item`), leaving that row dangling.

    Medium because `pick item` sits mid-lifecycle — the resolver must
    interpolate a plausible timestamp from bracketing neighbors, not just
    read it off a single anchor.
    """
    pick_type = get_p2p_event_type("pick item")
    item_qual = get_p2p_e2o_qualifier("item")
    row = conn.execute(
        "SELECT e.ocel_id, e.ocel_type FROM event e "
        "JOIN event_object eo ON eo.ocel_event_id = e.ocel_id "
        "WHERE e.ocel_type = ? AND eo.ocel_qualifier = ? LIMIT 1",
        (pick_type, item_qual),
    ).fetchone()
    if row is None:
        return None
    ocel_id, ocel_type = row
    conn.execute("DELETE FROM event WHERE ocel_id = ?", (ocel_id,))
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: ocel_type}}


def inject_missing_event_bare_id_hard(conn: sqlite3.Connection) -> dict | None:
    """Missing event, Hard: delete a real event still referenced by an E2O
    row with qualifier `packer`, leaving that row dangling.

    Hard because the surviving E2O row carries no `<type>:` id hint once the
    event is gone — the resolver must infer the event type purely from the
    qualifier and the linked object's type (here, a `package`).
    """
    obj_type = get_p2p_object_type("packages")
    qualifier = get_p2p_e2o_qualifier("packer")
    row = conn.execute(
        "SELECT e.ocel_id, e.ocel_type FROM event e "
        "JOIN event_object eo ON eo.ocel_event_id = e.ocel_id "
        "JOIN object o ON o.ocel_id = eo.ocel_object_id "
        "WHERE o.ocel_type = ? AND eo.ocel_qualifier = ? LIMIT 1",
        (obj_type, qualifier),
    ).fetchone()
    if row is None:
        return None
    ocel_id, ocel_type = row
    conn.execute("DELETE FROM event WHERE ocel_id = ?", (ocel_id,))
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: ocel_type}}


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
    event_type = get_p2p_event_type("confirm order")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1", (event_type,)
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
    event_type = get_p2p_event_type("pay order")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1", (event_type,)
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
    event_type = get_p2p_event_type("create package")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1", (event_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE event SET ocel_type = '   ' WHERE ocel_id = ?", row)
    return row[0]


# ---------------------------------------------------------------------------
# missing_event_attribute_value (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_missing_event_attribute_value_null_order_id_easy(conn: sqlite3.Connection) -> dict | None:
    """missing_event_attribute_value Easy: NULL lifecycle attribute in event_PlaceOrder.

    Easy because PlaceOrder is common and lifecycle is a standard attribute.

    Note: Adapted for P2P - uses lifecycle instead of order_id since P2P events
    have lifecycle/resource attributes instead of domain-specific ones.
    """
    table = get_p2p_event_table("event_PlaceOrder")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(conn, table, "lifecycle", ocel_id, None)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_missing_event_attribute_value_null_reason_hard(conn: sqlite3.Connection) -> dict | None:
    """missing_event_attribute_value Hard: NULL resource attribute in event_ItemOutOfStock.

    Hard because ItemOutOfStock is rare and resource is a less obvious required field.

    Note: Adapted for P2P - uses resource instead of reason since P2P events
    have lifecycle/resource attributes instead of domain-specific ones.
    """
    table = get_p2p_event_table("event_ItemOutOfStock")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(conn, table, "resource", ocel_id, None)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


# ---------------------------------------------------------------------------
# incorrect_event_attribute_datatype (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_attribute_datatype_string_in_quantity_easy(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_attribute_datatype Easy: Put integer in resource text field.

    Note: Adapted for P2P - uses resource (text field) instead of quantity (numeric).
    Corrupts by putting numeric value in text field, then detecting type mismatch.
    """
    table = get_p2p_event_table("event_PickItem")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(conn, table, "resource", ocel_id, 12345)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_incorrect_event_attribute_datatype_blob_in_activity_hard(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_attribute_datatype Hard: Put UTF-16-LE bytes in lifecycle text field.

    Note: Adapted for P2P - uses lifecycle instead of order_id.
    """
    table = get_p2p_event_table("event_PlaceOrder")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    # Put bytes in lifecycle field
    old_value = _capture_and_update(
        conn, table, "lifecycle", ocel_id, "complete".encode("utf-16-le")
    )
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


# ---------------------------------------------------------------------------
# incorrect_event_attribute_value (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_attribute_value_negative_quantity_easy(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_attribute_value Easy: Invalid lifecycle value in PickItem.

    Note: Adapted for P2P - uses lifecycle with invalid value instead of negative quantity.
    """
    table = get_p2p_event_table("event_PickItem")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(conn, table, "lifecycle", ocel_id, "invalid_state")
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_incorrect_event_attribute_value_time_violation_hard(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_attribute_value Hard: ConfirmOrder before PlaceOrder (temporal violation).

    Swaps the two events' ocel_time values, so both ids get an
    original_values entry -- the correct repair swaps them back, not just
    for the reported affected id but for its PlaceOrder counterpart too.
    """
    # Find a PlaceOrder and its corresponding ConfirmOrder
    place_table = get_p2p_event_table("event_PlaceOrder")
    confirm_table = get_p2p_event_table("event_ConfirmOrder")
    qualifier = get_p2p_e2o_qualifier("order")

    row = conn.execute(f"""
        SELECT po.ocel_id as place_id, co.ocel_id as confirm_id, po.ocel_time, co.ocel_time
        FROM {place_table} po
        JOIN event_object eo1 ON po.ocel_id = eo1.ocel_event_id
        JOIN event_object eo2 ON eo1.ocel_object_id = eo2.ocel_object_id AND eo2.ocel_qualifier = ?
        JOIN {confirm_table} co ON eo2.ocel_event_id = co.ocel_id
        LIMIT 1
    """, (qualifier,)).fetchone()
    if row is None:
        return None
    place_id, confirm_id, place_time, confirm_time = row
    # Swap the times to create violation
    conn.execute(
        f"UPDATE {confirm_table} SET ocel_time = ? WHERE ocel_id = ?",
        (place_time, confirm_id),
    )
    conn.execute(
        f"UPDATE {place_table} SET ocel_time = ? WHERE ocel_id = ?",
        (confirm_time, place_id),
    )
    return {
        "affected_ids": [confirm_id, place_id],
        "original_values": {confirm_id: confirm_time, place_id: place_time},
    }


# ---------------------------------------------------------------------------
# incorrect_event_type (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_type_swap_easy(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_type Easy: Change PlaceOrder type to PickItem (completely wrong)."""
    place_type = get_p2p_event_type("place order")
    pick_type = get_p2p_event_type("pick item")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1", (place_type,)
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(conn, "event", "ocel_type", ocel_id, pick_type)
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_incorrect_event_type_case_variant_hard(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_type Hard: Change PlaceOrder to placeorder (case variant)."""
    place_type = get_p2p_event_type("place order")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1 OFFSET 1", (place_type,)
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    # Create case variant - lowercase version
    old_value = _capture_and_update(
        conn, "event", "ocel_type", ocel_id, "create purchase order"
    )
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


# ---------------------------------------------------------------------------
# incorrect_event_time (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_event_time_future_easy(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_time Easy: Set event time to year 2099."""
    table = get_p2p_event_table("event_PlaceOrder")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(
        conn, table, "ocel_time", ocel_id, "2099-01-01 00:00:00"
    )
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


def inject_incorrect_event_time_past_hard(conn: sqlite3.Connection) -> dict | None:
    """incorrect_event_time Hard: Set event time to year 1900."""
    table = get_p2p_event_table("event_ConfirmOrder")
    row = conn.execute(
        f"SELECT ocel_id FROM {table} LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    ocel_id = row[0]
    old_value = _capture_and_update(
        conn, table, "ocel_time", ocel_id, "1900-01-01 00:00:00"
    )
    return {"affected_ids": [ocel_id], "original_values": {ocel_id: old_value}}


# ---------------------------------------------------------------------------
# duplicate_events_on_ids (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_events_on_ids_easy(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_ids Easy: Duplicate an event row identically."""
    event_type = get_p2p_event_type("place order")
    row = conn.execute(
        "SELECT ocel_id, ocel_type FROM event WHERE ocel_type = ? LIMIT 1", (event_type,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO event VALUES (?, ?)", row)
    return row[0]


def inject_duplicate_events_on_ids_conflicting_types_hard(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_ids Hard: Duplicate event with conflicting type."""
    place_type = get_p2p_event_type("place order")
    pick_type = get_p2p_event_type("pick item")
    row = conn.execute(
        "SELECT ocel_id FROM event WHERE ocel_type = ? LIMIT 1 OFFSET 2", (place_type,)
    ).fetchone()
    if row is None:
        return None
    # Insert duplicate with different type
    conn.execute("INSERT INTO event VALUES (?, ?)", (row[0], pick_type))
    return row[0]


# ---------------------------------------------------------------------------
# duplicate_events_on_attributes (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_duplicate_events_on_attributes_clone_easy(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_attributes Easy: Clone a PlaceOrder event."""
    event_type = get_p2p_event_type("place order")
    table = get_p2p_event_table("event_PlaceOrder")
    row = conn.execute(f"""
        SELECT ocel_id, ocel_time
        FROM {table}
        LIMIT 1
    """).fetchone()
    if row is None:
        return None
    orig_id, time = row
    clone_id = f"{orig_id}-CLONE"
    # Insert into event table
    conn.execute("INSERT INTO event VALUES (?, ?)", (clone_id, event_type))
    # Insert into event_PlaceOrder
    conn.execute(
        f"INSERT INTO {table} (ocel_id, ocel_time) VALUES (?, ?)",
        (clone_id, time),
    )
    return clone_id


def inject_duplicate_events_on_attributes_clone_with_refs_hard(conn: sqlite3.Connection) -> str | None:
    """duplicate_events_on_attributes Hard: Clone event AND its event_object refs."""
    event_type = get_p2p_event_type("place order")
    table = get_p2p_event_table("event_PlaceOrder")
    row = conn.execute(f"""
        SELECT ocel_id, ocel_time
        FROM {table}
        LIMIT 1 OFFSET 1
    """).fetchone()
    if row is None:
        return None
    orig_id, time = row
    clone_id = f"{orig_id}-CLONE"
    # Insert into event table
    conn.execute("INSERT INTO event VALUES (?, ?)", (clone_id, event_type))
    # Insert into event_PlaceOrder
    conn.execute(
        f"INSERT INTO {table} (ocel_id, ocel_time) VALUES (?, ?)",
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
