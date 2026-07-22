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
    """duplicate_o2o_relations Easy: an order↔item `comprises` triple gets
    two extra copies (3 rows total). High-frequency qualifier — the
    duplicate stands out clearly against the surrounding 7.6k comprises
    rows only because it repeats id-for-id."""
    return _insert_o2o_copies(conn, "o-990001", "i-880001", "comprises", extras=2)


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
