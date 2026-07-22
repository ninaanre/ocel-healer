"""Relation-side corruption injectors.

Covers issues rooted in the relation tables (`event_object`,
`object_object`): dangling E2O and O2O references, plus the
`missing_object` case where an E2O row points at a plausibly-typed but
non-existent object id.
"""

from __future__ import annotations

import sqlite3


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
    """dangling_o2o_relationship Easy: Source id is a typo near-miss of a
    real employee, target is a real employee."""
    src = "Wil van der Aalts"      # real id: 'Wil van der Aalst'
    dst = "Wil van der Aalst"
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


def inject_dangling_o2o_relationship_missing_target(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_o2o_relationship Medium: Real customer references a
    typo near-miss of a real employee id (distinct typo from the hard tier)."""
    src = "Balkan Minerals d.o.o."
    dst = "Wil van der Aslst"      # real id: 'Wil van der Aalst' (l/s transposed)
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


def inject_dangling_o2o_relationship_missing_both_typo(conn: sqlite3.Connection) -> tuple[str, str]:
    """dangling_o2o_relationship Hard: Both endpoints are typo near-misses
    of real ids."""
    src = "AlpenTech Innovation AG"      # real id: 'AlpenTech Innovations AG'
    dst = "Wil van der Aallst"           # real id: 'Wil van der Aalst'
    conn.execute(
        "INSERT INTO object_object VALUES (?, ?, ?)",
        (src, dst, "primarySalesRep"),
    )
    return src, dst


# ---------------------------------------------------------------------------
# missing_object (Easy/Medium/Hard)
#
# Each injector inserts an E2O row whose ocel_object_id is a plausibly-typed
# but non-existent id. These rows also trip the dangling_e2o_relationship
# detector — that's expected and intentional; the dashboard routes them to
# the missing_object cell.
# ---------------------------------------------------------------------------


def inject_missing_object_order_easy(conn: sqlite3.Connection) -> str | None:
    """missing_object Easy: E2O row references orders:o-991000
    (id doesn't exist).

    Easy because the `orders:` prefix immediately pins the type and the
    peer set for the LLM to imitate is large (~2k orders).
    """
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type='place order' LIMIT 1").fetchone()
    if ev is None:
        return None
    missing_id = "orders:o-991000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, "order"),
    )
    return missing_id


def inject_missing_object_item_medium(conn: sqlite3.Connection) -> str | None:
    """missing_object Medium: E2O row references items:i-881000
    (id doesn't exist).

    Medium because items share the schema of products (`weight`, `price`),
    so the LLM must respect the `items:` prefix rather than sliding the
    inferred type to products.
    """
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type='pick item' LIMIT 1").fetchone()
    if ev is None:
        return None
    missing_id = "items:i-881000"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, "item"),
    )
    return missing_id


def inject_missing_object_product_hard(conn: sqlite3.Connection) -> str | None:
    """missing_object Hard: E2O row references products:Echo Show 10
    (a plausible-sounding product in an existing family — real ids include
    `Echo Show 5` and `Echo Show 8` — but this id doesn't exist).

    Hard because product ids don't use the `<type>:<id>` prefix in the
    clean dataset (product `ocel_id` is a bare product name like
    `Echo Dot`). We deliberately introduce a `products:` prefix here to
    exercise the prefix-based detector path and force the LLM to fabricate
    all initial attributes (weight, price) from the peer distribution.
    """
    ev = conn.execute("SELECT ocel_id FROM event WHERE ocel_type='place order' LIMIT 1").fetchone()
    if ev is None:
        return None
    missing_id = "products:Echo Show 10"
    conn.execute(
        "INSERT INTO event_object VALUES (?, ?, ?)",
        (ev[0], missing_id, "product"),
    )
    return missing_id
