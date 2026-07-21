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
