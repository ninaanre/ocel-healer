"""Relation-side corruption injectors.

Covers issues rooted in the relation tables (`event_object`,
`object_object`): dangling E2O and O2O references, plus the
`missing_object` case where an E2O row points at a plausibly-typed but
non-existent object id.
"""

from __future__ import annotations

import sqlite3

from .p2p_mappings import (
    get_p2p_object_type,
    get_p2p_e2o_qualifier,
    get_p2p_o2o_qualifier,
)


# ---------------------------------------------------------------------------
# Base dangling_e2o_relationship helpers — building blocks reused by the
# tiered flavors below.
# ---------------------------------------------------------------------------


def inject_dangling_e2o_relationship_object(conn: sqlite3.Connection) -> str | None:
    """dangling_e2o_relationship: Insert an E2O row that references a
    non-existent object (typo-shape id, doesn't collide with real orders)."""
    event = conn.execute("SELECT ocel_id FROM event LIMIT 1").fetchone()
    if event is None:
        return None
    fake_object_id = "o-990001x"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (event[0], fake_object_id, "unknown"),
    )
    return fake_object_id


def inject_dangling_e2o_relationship_event(conn: sqlite3.Connection) -> str | None:
    """dangling_e2o_relationship: Insert an E2O row that references a
    non-existent event (bare-id shape, doesn't collide)."""
    obj = conn.execute("SELECT ocel_id FROM object WHERE ocel_type IS NOT NULL LIMIT 1").fetchone()
    if obj is None:
        return None
    fake_event_id = "e-9900099"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (fake_event_id, obj[0], "unknown"),
    )
    return fake_event_id


# ---------------------------------------------------------------------------
# dangling_e2o_relationship (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_dangling_e2o_relationship_missing_object_easy(conn: sqlite3.Connection) -> str | None:
    """dangling_e2o_relationship Easy: E2O row references a nonexistent
    object (`missing_side='object'`)."""
    return inject_dangling_e2o_relationship_object(conn)


def inject_dangling_e2o_relationship_missing_event(conn: sqlite3.Connection) -> str | None:
    """dangling_e2o_relationship Medium: E2O row references a nonexistent
    event (`missing_side='event'`)."""
    return inject_dangling_e2o_relationship_event(conn)


def inject_dangling_e2o_relationship_missing_both(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_e2o_relationship Hard: E2O row where BOTH endpoints are
    typo near-misses of real ids (neither exists)."""
    fake_event = "e-771001"
    fake_object = "Balkan Mineraals d.o.o."  # real id: 'Balkan Minerals d.o.o.'
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (fake_event, fake_object, "unknown"),
    )
    return fake_event, fake_object


# ---------------------------------------------------------------------------
# dangling_o2o_relationship (Easy/Medium/Hard)
# ---------------------------------------------------------------------------


def inject_dangling_o2o_relationship_missing_source(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_o2o_relationship Easy: Source id is a typo near-miss,
    target is a real object."""
    obj_type = get_p2p_object_type("employees")
    qualifier = get_p2p_o2o_qualifier("processed_by")

    dst = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (obj_type,)
    ).fetchone()
    if dst is None:
        return ("fake-src", "fake-dst")

    src = f"{dst[0]}-TYPO"  # Create typo variant
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst[0], qualifier),
    )
    return src, dst[0]


def inject_dangling_o2o_relationship_missing_target(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_o2o_relationship Medium: Real source references a
    typo near-miss target."""
    customers_type = get_p2p_object_type("customers")
    employee_type = get_p2p_object_type("employees")
    qualifier = get_p2p_o2o_qualifier("processed_by")

    src = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (customers_type,)
    ).fetchone()
    dst = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (employee_type,)
    ).fetchone()

    if src is None or dst is None:
        return ("fake-src", "fake-dst")

    dst_typo = f"{dst[0]}-TYPO"
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src[0], dst_typo, qualifier),
    )
    return src[0], dst_typo


def inject_dangling_o2o_relationship_missing_both_typo(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_o2o_relationship Hard: Both endpoints are typo near-misses
    of real ids."""
    customers_type = get_p2p_object_type("customers")
    employee_type = get_p2p_object_type("employees")
    qualifier = get_p2p_o2o_qualifier("processed_by")

    src = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (customers_type,)
    ).fetchone()
    dst = conn.execute(
        "SELECT ocel_id FROM object WHERE ocel_type = ? LIMIT 1", (employee_type,)
    ).fetchone()

    if src is None or dst is None:
        return ("fake-src", "fake-dst")

    src_typo = f"{src[0]}-TYPO1"
    dst_typo = f"{dst[0]}-TYPO2"
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src_typo, dst_typo, qualifier),
    )
    return src_typo, dst_typo


# ---------------------------------------------------------------------------
# missing_object (Easy/Medium/Hard)
#
# Each injector inserts an E2O row whose ocel_object_id is a plausibly-typed
# but non-existent id. These rows also trip the dangling_e2o_relationship
# detector — that's expected and intentional; the dashboard routes them to
# the missing_object cell.
# ---------------------------------------------------------------------------


def inject_missing_object_order_easy(conn: sqlite3.Connection) -> str | None:
    """missing_object Easy: E2O row references purchase_order:po-991000
    (id doesn't exist).

    Easy because the type prefix immediately pins the type and the
    peer set for the LLM to imitate is large.
    """
    place_type = get_p2p_event_type("place order")
    order_qual = get_p2p_e2o_qualifier("order")
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type=? LIMIT 1", (place_type,)).fetchone()
    if ev is None:
        return None
    missing_id = "purchase_order:po-991000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, order_qual),
    )
    return missing_id


def inject_missing_object_item_medium(conn: sqlite3.Connection) -> str | None:
    """missing_object Medium: E2O row references material:mat-881000
    (id doesn't exist).

    Medium because materials are used in multiple contexts.
    """
    pick_type = get_p2p_event_type("pick item")
    item_qual = get_p2p_e2o_qualifier("item")
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type=? LIMIT 1", (pick_type,)).fetchone()
    if ev is None:
        return None
    missing_id = "material:mat-881000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, item_qual),
    )
    return missing_id


def inject_missing_object_product_hard(conn: sqlite3.Connection) -> str | None:
    """missing_object Hard: E2O row references material with plausible name
    but doesn't exist.

    Hard because material ids may not use type prefix consistently.
    """
    place_type = get_p2p_event_type("place order")
    product_qual = get_p2p_e2o_qualifier("product")
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type=? LIMIT 1", (place_type,)).fetchone()
    if ev is None:
        return None
    missing_id = "material:MISSING_PRODUCT_XYZ"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, product_qual),
    )
    return missing_id


# ---------------------------------------------------------------------------
# duplicate_o2o_relations (Easy/Medium/Hard)
#
# Insert extra copies of a real (source, target, qualifier) triple in
# `object_object`. Both endpoints exist — it's the triple that's redundant.
# The clean fixture leaves this table without a PRIMARY KEY constraint, so
# straight INSERTs are enough; no schema tweaks needed.
# ---------------------------------------------------------------------------


def _insert_o2o_copies(
    conn: sqlite3.Connection, src: str, tgt: str, qual: str, extras: int
) -> tuple[str, str, str]:
    for _ in range(extras):
        conn.execute(
            "INSERT INTO object_object VALUES (?, ?, ?)",
            (src, tgt, qual),
        )
    return src, tgt, qual


def inject_duplicate_o2o_relations_comprises_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_o2o_relations Easy: insert duplicate o2o triple."""
    qual = get_p2p_o2o_qualifier("comprises")
    row = conn.execute(
        "SELECT ocel_source_id, ocel_target_id FROM object_object WHERE ocel_qualifier = ? LIMIT 1", (qual,)
    ).fetchone()
    if row is None:
        # Fallback: create relationship between any two materials
        src = conn.execute("SELECT ocel_id FROM object LIMIT 1").fetchone()
        tgt = conn.execute("SELECT ocel_id FROM object LIMIT 1 OFFSET 1").fetchone()
        if src and tgt:
            return _insert_o2o_copies(conn, src[0], tgt[0], qual, extras=2)
        return ("fake", "fake", qual)
    return _insert_o2o_copies(conn, row[0], row[1], qual, extras=2)


def inject_duplicate_o2o_relations_places_medium(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_o2o_relations Medium: a customer↔order `places` triple
    gets two extra copies. Cross-type edge (customer → order) rather than
    the item-to-order path exercised in easy."""
    src = conn.execute(
        "SELECT ocel_source_id FROM object_object WHERE ocel_qualifier = 'places' LIMIT 1"
    ).fetchone()
    tgt = conn.execute(
        "SELECT ocel_target_id FROM object_object WHERE ocel_qualifier = 'places' "
        "AND ocel_source_id = ? LIMIT 1",
        (src[0],) if src else (None,),
    ).fetchone()
    if not (src and tgt):
        return "", "", ""
    return _insert_o2o_copies(conn, src[0], tgt[0], "places", extras=2)


def inject_duplicate_o2o_relations_sales_rep_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_o2o_relations Hard: a low-frequency (~15 rows total)
    `primarySalesRep` triple gets two extra copies. Hard because the
    surrounding qualifier is rare — one accidental extra could plausibly
    look like a legitimate multi-rep assignment."""
    return _insert_o2o_copies(
        conn, "Danube Pharmaceuticals BV", "Christine von Dobbert",
        "primarySalesRep", extras=2,
    )


# ---------------------------------------------------------------------------
# o2o_self_loop (Easy/Medium/Hard)
#
# Insert an `object_object` row where source == target (same object id
# under a qualifier). Structurally illegal — an object relating to itself
# under a qualifier is not what `object_object` is for.
# ---------------------------------------------------------------------------


def inject_o2o_self_loop_order_easy(conn: sqlite3.Connection) -> str:
    """o2o_self_loop Easy: an order references itself under `contains`.
    Easy because orders are the dataset's central entity — a self-loop
    among them is glaring."""
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        ("o-990001", "o-990001", "contains"),
    )
    return "o-990001"


def inject_o2o_self_loop_employee_medium(conn: sqlite3.Connection) -> str:
    """o2o_self_loop Medium: an employee references themselves under
    `primarySalesRep`. Medium because a person being their own sales rep
    is superficially plausible in a poorly-designed schema — but still
    structurally wrong for O2O."""
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        ("Wil van der Aalst", "Wil van der Aalst", "primarySalesRep"),
    )
    return "Wil van der Aalst"


def inject_o2o_self_loop_product_hard(conn: sqlite3.Connection) -> str:
    """o2o_self_loop Hard: a product references itself under a qualifier
    that doesn't otherwise appear on products (`is a` typically links
    items to products). Hard because the qualifier itself is
    off-diagonal — the detector must still flag on the source=target
    condition alone, without leaning on qualifier semantics."""
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        ("Echo", "Echo", "is a"),
    )
    return "Echo"


# ---------------------------------------------------------------------------
# duplicate_e2o_relations (Easy/Medium/Hard)
#
# Insert extra copies of a real (event, object, qualifier) triple in
# `event_object`. Event-side mirror of `duplicate_o2o_relations`; the
# clean fixture also leaves this table PK-less.
# ---------------------------------------------------------------------------


def _insert_e2o_copies(
    conn: sqlite3.Connection, ev: str, obj: str, qual: str, extras: int
) -> tuple[str, str, str]:
    for _ in range(extras):
        conn.execute(
            "INSERT INTO event_object VALUES (?, ?, ?)",
            (ev, obj, qual),
        )
    return ev, obj, qual


def inject_duplicate_e2o_relations_order_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_e2o_relations Easy: a place_order↔order triple gets two
    extra copies. Order-touching events are the highest-signal edges in
    the log, so an accidental extra copy sits directly on the critical
    path."""
    return _insert_e2o_copies(conn, "place_o-990001", "o-990001", "order", extras=2)


def inject_duplicate_e2o_relations_item_medium(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_e2o_relations Medium: a pick_item↔item triple gets two
    extra copies. Items are the highest-frequency object type in E2O
    (~61k rows), so the duplicate has to actually hash-match to stand out."""
    ev = conn.execute(
        "SELECT ocel_event_id, ocel_object_id "
        "FROM event_object WHERE ocel_qualifier = 'item' "
        "AND ocel_event_id LIKE 'pick%' LIMIT 1"
    ).fetchone()
    if not ev:
        return "", "", ""
    return _insert_e2o_copies(conn, ev[0], ev[1], "item", extras=2)


def inject_duplicate_e2o_relations_sales_person_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """duplicate_e2o_relations Hard: a place_order↔sales_person triple
    gets two extra copies. Hard because `sales person` is a lower-volume
    qualifier (~2k rows) and multi-rep assignments could look real; the
    detector still fires because it's the SAME (event, sales_person)
    edge repeated, not two different reps on one order."""
    ev = conn.execute(
        "SELECT ocel_event_id, ocel_object_id "
        "FROM event_object WHERE ocel_qualifier = 'sales person' LIMIT 1"
    ).fetchone()
    if not ev:
        return "", "", ""
    return _insert_e2o_copies(conn, ev[0], ev[1], "sales person", extras=2)


# ---------------------------------------------------------------------------
# incorrect_e2o_relationship_target (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_e2o_relationship_target_wrong_order_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str]:
    """incorrect_e2o_relationship_target Easy: PlaceOrder event connected to wrong order.

    Easy because the event type and qualifier clearly indicate which object it should target,
    but it's connected to a completely different order."""
    # Find a PlaceOrder event and swap its order connection
    row = conn.execute("""
        SELECT eo.ocel_event_id, eo.ocel_object_id, o2.ocel_id as wrong_order
        FROM event_object eo
        JOIN event e ON eo.ocel_event_id = e.ocel_id
        JOIN object o1 ON eo.ocel_object_id = o1.ocel_id
        JOIN object o2 ON o2.ocel_type = 'orders' AND o2.ocel_id != o1.ocel_id
        WHERE e.ocel_type = 'place order' AND eo.ocel_qualifier = 'order'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", ""
    event_id, correct_order, wrong_order = row
    # Update to point to wrong order
    conn.execute(
        "UPDATE event_object SET ocel_object_id = ? WHERE ocel_event_id = ? AND ocel_qualifier = 'order'",
        (wrong_order, event_id),
    )
    return event_id, wrong_order


def inject_incorrect_e2o_relationship_target_plausible_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str]:
    """incorrect_e2o_relationship_target Hard: PickItem event connected to plausible but wrong item.

    Hard because both items might be in the same order, making it harder to detect."""
    # Find a PickItem event and swap to a different item
    row = conn.execute("""
        SELECT eo.ocel_event_id, eo.ocel_object_id, o2.ocel_id as wrong_item
        FROM event_object eo
        JOIN event e ON eo.ocel_event_id = e.ocel_id
        JOIN object o1 ON eo.ocel_object_id = o1.ocel_id
        JOIN object o2 ON o2.ocel_type = 'items' AND o2.ocel_id != o1.ocel_id
        WHERE e.ocel_type = 'pick item' AND eo.ocel_qualifier = 'item'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", ""
    event_id, correct_item, wrong_item = row
    # Update to point to wrong item
    conn.execute(
        "UPDATE event_object SET ocel_object_id = ? WHERE ocel_event_id = ? AND ocel_qualifier = 'item'",
        (wrong_item, event_id),
    )
    return event_id, wrong_item


# ---------------------------------------------------------------------------
# incorrect_e2o_relationship_qualifier (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_e2o_relationship_qualifier_obvious_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """incorrect_e2o_relationship_qualifier Easy: Change 'order' qualifier to 'item' (obviously wrong).

    Easy because a PlaceOrder event should connect to an order with 'order' qualifier, not 'item'."""
    row = conn.execute("""
        SELECT eo.ocel_event_id, eo.ocel_object_id, eo.ocel_qualifier
        FROM event_object eo
        JOIN event e ON eo.ocel_event_id = e.ocel_id
        WHERE e.ocel_type = 'place order' AND eo.ocel_qualifier = 'order'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", "", ""
    event_id, object_id, old_qual = row
    new_qual = "item"
    conn.execute(
        "UPDATE event_object SET ocel_qualifier = ? WHERE ocel_event_id = ? AND ocel_object_id = ?",
        (new_qual, event_id, object_id),
    )
    return event_id, object_id, new_qual


def inject_incorrect_e2o_relationship_qualifier_subtle_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """incorrect_e2o_relationship_qualifier Hard: Change 'sales person' to 'salesperson' (typo variant).

    Hard because it's a plausible typo that might not be caught without domain knowledge."""
    row = conn.execute("""
        SELECT ocel_event_id, ocel_object_id, ocel_qualifier
        FROM event_object
        WHERE ocel_qualifier = 'sales person'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", "", ""
    event_id, object_id, old_qual = row
    new_qual = "salesperson"
    conn.execute(
        "UPDATE event_object SET ocel_qualifier = ? WHERE ocel_event_id = ? AND ocel_object_id = ?",
        (new_qual, event_id, object_id),
    )
    return event_id, object_id, new_qual


# ---------------------------------------------------------------------------
# incorrect_o2o_relationship_target (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_o2o_relationship_target_wrong_item_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str]:
    """incorrect_o2o_relationship_target Easy: Order 'comprises' wrong item (obviously mismatched).

    Easy because the order and item are completely unrelated."""
    # Find an order→item comprises relation and swap the target
    row = conn.execute("""
        SELECT oo.ocel_source_id, oo.ocel_target_id, o2.ocel_id as wrong_item
        FROM object_object oo
        JOIN object o1 ON oo.ocel_source_id = o1.ocel_id
        JOIN object o2 ON o2.ocel_type = 'items' AND o2.ocel_id != oo.ocel_target_id
        WHERE oo.ocel_qualifier = 'comprises' AND o1.ocel_type = 'orders'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", ""
    source_id, correct_target, wrong_target = row
    # Update to point to wrong item
    conn.execute(
        "UPDATE object_object SET ocel_target_id = ? WHERE ocel_source_id = ? AND ocel_target_id = ? AND ocel_qualifier = 'comprises'",
        (wrong_target, source_id, correct_target),
    )
    return source_id, wrong_target


def inject_incorrect_o2o_relationship_target_plausible_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str]:
    """incorrect_o2o_relationship_target Hard: Customer 'places' wrong order (plausible but incorrect).

    Hard because the customer exists and the order exists, making it seem valid."""
    # Find a customer→order places relation and swap the target
    row = conn.execute("""
        SELECT oo.ocel_source_id, oo.ocel_target_id, o2.ocel_id as wrong_order
        FROM object_object oo
        JOIN object o1 ON oo.ocel_source_id = o1.ocel_id
        JOIN object o2 ON o2.ocel_type = 'orders' AND o2.ocel_id != oo.ocel_target_id
        WHERE oo.ocel_qualifier = 'places' AND o1.ocel_type = 'customers'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", ""
    source_id, correct_target, wrong_target = row
    # Update to point to wrong order
    conn.execute(
        "UPDATE object_object SET ocel_target_id = ? WHERE ocel_source_id = ? AND ocel_target_id = ? AND ocel_qualifier = 'places'",
        (wrong_target, source_id, correct_target),
    )
    return source_id, wrong_target


# ---------------------------------------------------------------------------
# incorrect_o2o_relationship_qualifier (Easy/Hard)
# ---------------------------------------------------------------------------


def inject_incorrect_o2o_relationship_qualifier_wrong_verb_easy(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """incorrect_o2o_relationship_qualifier Easy: Change 'comprises' to 'contains' (wrong verb).

    Easy because 'comprises' is the standard qualifier for order→item, not 'contains'."""
    row = conn.execute("""
        SELECT ocel_source_id, ocel_target_id, ocel_qualifier
        FROM object_object
        WHERE ocel_qualifier = 'comprises'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", "", ""
    source_id, target_id, old_qual = row
    new_qual = "contains"
    conn.execute(
        "UPDATE object_object SET ocel_qualifier = ? WHERE ocel_source_id = ? AND ocel_target_id = ? AND ocel_qualifier = ?",
        (new_qual, source_id, target_id, old_qual),
    )
    return source_id, target_id, new_qual


def inject_incorrect_o2o_relationship_qualifier_typo_hard(
    conn: sqlite3.Connection,
) -> tuple[str, str, str]:
    """incorrect_o2o_relationship_qualifier Hard: Change 'primarySalesRep' to 'primarySalesRepresentative' (verbose variant).

    Hard because it's a plausible expansion that might be used inconsistently."""
    row = conn.execute("""
        SELECT ocel_source_id, ocel_target_id, ocel_qualifier
        FROM object_object
        WHERE ocel_qualifier = 'primarySalesRep'
        LIMIT 1
    """).fetchone()
    if not row:
        return "", "", ""
    source_id, target_id, old_qual = row
    new_qual = "primarySalesRepresentative"
    conn.execute(
        "UPDATE object_object SET ocel_qualifier = ? WHERE ocel_source_id = ? AND ocel_target_id = ? AND ocel_qualifier = ?",
        (new_qual, source_id, target_id, old_qual),
    )
    return source_id, target_id, new_qual
