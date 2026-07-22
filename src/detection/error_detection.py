import sqlite3
from contextlib import contextmanager
from typing import Iterator

import polars as pl


SqliteInput = str | sqlite3.Connection

# OCEL2 reserved columns that aren't user-defined attributes.
_OCEL_RESERVED = {"ocel_id", "ocel:timestamp", "ocel_type", "ocel_time", "ocel_changed_field", "new_value"}

def _frame(rows: list[dict], columns: list[str]) -> pl.DataFrame:
    """Build a Utf8-typed DataFrame, enforcing column order even when empty."""
    return pl.DataFrame(rows, schema={c: pl.Utf8 for c in columns})


@contextmanager
def _connect(src: SqliteInput) -> Iterator[sqlite3.Connection]:
    if isinstance(src, sqlite3.Connection):
        yield src
        return
    conn = sqlite3.connect(src)
    try:
        yield conn
    finally:
        conn.close()


def _object_type_tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (object_type, table_name) pairs.

    `object_map_type.ocel_type_map` stores the type suffix (e.g. `order`),
    and the per-type table is named `object_<suffix>` (e.g. `object_order`).
    """
    rows = conn.execute("SELECT ocel_type, ocel_type_map FROM object_map_type").fetchall()
    return [(t, f"object_{m}") for t, m in rows]


def _event_type_tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (event_type, table_name) pairs.

    Mirrors `_object_type_tables`. `event_map_type.ocel_type_map` stores the
    CamelCase suffix (e.g. `PlaceOrder`) and the per-type table is named
    `event_<suffix>` (e.g. `event_PlaceOrder`).
    """
    rows = conn.execute("SELECT ocel_type, ocel_type_map FROM event_map_type").fetchall()
    return [(t, f"event_{m}") for t, m in rows]


def _column_info(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """Return [(column_name, declared_type)] for a table, skipping reserved cols."""
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [(name, dtype) for _, name, dtype, *_ in info if name not in _OCEL_RESERVED]


def _value_matches_type(value: object, declared_type: str) -> bool:
    """Check that a non-null value is compatible with the declared SQLite type."""
    if isinstance(value, bool):
        return False
    t = (declared_type or "").upper()
    if "INT" in t:
        return isinstance(value, int)
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return isinstance(value, (int, float))
    if any(k in t for k in ("CHAR", "TEXT", "CLOB")):
        return isinstance(value, str)
    if "BLOB" in t:
        return isinstance(value, (bytes, bytearray))
    return True


def _is_missing(value: object) -> bool:
    """Treat NULL and empty/whitespace strings as missing values."""
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    names = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    return column in names


def _iter_object_attrs(
    conn: sqlite3.Connection,
    initial_rows_only: bool = False,
) -> Iterator[tuple[str, object, str, str, object]]:
    """Yield (object_type, ocel_id, attr_name, declared_type, value) for every
    non-reserved attribute cell across all per-type object tables.

    When initial_rows_only=True, only rows where ocel_changed_field IS NULL are
    considered — change rows (delta entries) are skipped because null values in
    unchanged attributes are not missing values.
    """
    for ocel_type, table in _object_type_tables(conn):
        cols = _column_info(conn, table)
        if not cols:
            continue
        attr_names = [c for c, _ in cols]
        quoted = ", ".join(f'"{c}"' for c in attr_names)
        where = (
            'WHERE "ocel_changed_field" IS NULL'
            if initial_rows_only and _has_column(conn, table, "ocel_changed_field")
            else ""
        )
        for ocel_id, *values in conn.execute(
            f'SELECT ocel_id, {quoted} FROM "{table}" {where}'
        ).fetchall():
            for (attr, declared), value in zip(cols, values):
                yield ocel_type, ocel_id, attr, declared, value


def detect_missing_attribute_value(src: SqliteInput) -> pl.DataFrame:
    """Find object rows where any attribute value is NULL or empty/whitespace."""
    with _connect(src) as conn:
        rows = [
            {
                "object_type": ocel_type,
                "ocel_id": ocel_id,
                "attribute": attr,
                "actual_value": value,
                "issue": "missing_attribute_value",
            }
            for ocel_type, ocel_id, attr, _, value in _iter_object_attrs(conn, initial_rows_only=True)
            if _is_missing(value)
        ]
    return _frame(
        rows, ["object_type", "ocel_id", "attribute", "actual_value", "issue"]
    )


def detect_incorrect_attribute_datatype(src: SqliteInput) -> pl.DataFrame:
    """Find attribute values that don't match the column's declared SQLite type.

    NULL/empty values are skipped (covered by `detect_missing_attribute_value`).
    """
    with _connect(src) as conn:
        rows = [
            {
                "object_type": ocel_type,
                "ocel_id": ocel_id,
                "attribute": attr,
                "expected_type": declared,
                "actual_value": repr(value),
                "actual_python_type": type(value).__name__,
                "issue": "incorrect_attribute_datatype",
            }
            for ocel_type, ocel_id, attr, declared, value in _iter_object_attrs(conn)
            if not _is_missing(value) and not _value_matches_type(value, declared)
        ]
    return _frame(
        rows,
        [
            "object_type", "ocel_id", "attribute",
            "expected_type", "actual_value", "actual_python_type", "issue",
        ],
    )


def detect_dangling_o2o_relationship(src: SqliteInput) -> pl.DataFrame:
    """Find object-to-object relationships referencing missing objects."""
    with _connect(src) as conn:
        objects = pl.read_database("SELECT ocel_id, ocel_type FROM object", conn)
        o2o = pl.read_database(
            "SELECT ocel_source_id, ocel_target_id, ocel_qualifier FROM object_object",
            conn,
        )

    # An empty `object_object` table (common on logs upcast from OCEL 1.0)
    # comes back with `Null`-typed columns, which then fail to join against
    # the Utf8 `ocel_id` on the object side. Force the id cols to Utf8.
    o2o = o2o.cast({"ocel_source_id": pl.Utf8, "ocel_target_id": pl.Utf8})

    # Dedup: the object table can legitimately list the same id twice in this dataset.
    known = objects.unique(subset=["ocel_id"])
    # `_exists` sentinels distinguish "object row absent" (dangling) from
    # "object row present but ocel_type IS NULL" (missing_object_type).
    known = known.with_columns(pl.lit(True).alias("_exists"))

    enriched = o2o.join(
        known.rename({"ocel_id": "ocel_source_id", "ocel_type": "source_type", "_exists": "source_exists"}),
        on="ocel_source_id",
        how="left",
    ).join(
        known.rename({"ocel_id": "ocel_target_id", "ocel_type": "target_type", "_exists": "target_exists"}),
        on="ocel_target_id",
        how="left",
    )

    violations = enriched.filter(
        pl.col("source_exists").is_null() | pl.col("target_exists").is_null()
    )

    cols = [
        "ocel_source_id", "source_type",
        "ocel_target_id", "target_type",
        "ocel_qualifier", "missing_side", "issue",
    ]
    return violations.with_columns(
        pl.when(pl.col("source_exists").is_null() & pl.col("target_exists").is_null())
        .then(pl.lit("both"))
        .when(pl.col("source_exists").is_null())
        .then(pl.lit("source"))
        .otherwise(pl.lit("target"))
        .alias("missing_side"),
        pl.lit("dangling_o2o_relationship").alias("issue"),
    ).select(cols).cast({c: pl.Utf8 for c in cols})


# ---------------------------------------------------------------------------
# New detectors
# ---------------------------------------------------------------------------

def detect_duplicate_objects_on_ids(src: SqliteInput) -> pl.DataFrame:
    """Find ocel_ids that appear more than once in the main object table.

    One row is emitted per duplicated ocel_id.  ``ocel_types`` lists every
    type value seen for that id (comma-separated); ``count`` is the number of
    duplicate rows.
    """
    with _connect(src) as conn:
        objects = pl.read_database("SELECT ocel_id, ocel_type FROM object", conn)

    cols = ["ocel_id", "ocel_types", "count", "issue"]
    return (
        objects
        .group_by("ocel_id")
        .agg(
            pl.len().alias("count"),
            pl.col("ocel_type").unique().sort().str.join(", ").alias("ocel_types"),
        )
        .filter(pl.col("count") > 1)
        .with_columns(pl.lit("duplicate_objects_on_ids").alias("issue"))
        .select(cols)
        .cast({c: pl.Utf8 for c in cols})
    )


def detect_duplicate_objects_on_attributes(src: SqliteInput) -> pl.DataFrame:
    """Find objects within the same type-table that share identical attribute
    values but carry different ocel_ids.

    One row is emitted per duplicate group.  ``ocel_ids`` lists every id that
    shares the same attribute fingerprint (comma-separated).
    """
    with _connect(src) as conn:
        rows: list[dict] = []
        for ocel_type, table in _object_type_tables(conn):
            attr_cols = [c for c, _ in _column_info(conn, table)]
            if not attr_cols:
                continue
            quoted = ", ".join(f'"{c}"' for c in attr_cols)
            raw = conn.execute(
                f'SELECT ocel_id, {quoted} FROM "{table}"'
            ).fetchall()

            # Group ocel_ids by their attribute fingerprint.
            groups: dict[tuple, list[str]] = {}
            for ocel_id, *values in raw:
                key = tuple(values)
                groups.setdefault(key, []).append(ocel_id)

            for key, ids in groups.items():
                if len(ids) < 2:
                    continue
                rows.append({
                    "object_type": ocel_type,
                    "ocel_ids": ", ".join(ids),
                    "duplicate_count": str(len(ids)),
                    "attribute_values": str(dict(zip(attr_cols, key))),
                    "issue": "duplicate_objects_on_attributes",
                })
    return _frame(
        rows,
        ["object_type", "ocel_ids", "duplicate_count", "attribute_values", "issue"],
    )


def detect_missing_object_type(src: SqliteInput) -> pl.DataFrame:
    """Find objects in the main object table whose ocel_type is NULL or
    empty/whitespace."""
    with _connect(src) as conn:
        objects = pl.read_database("SELECT ocel_id, ocel_type FROM object", conn)

    cols = ["ocel_id", "ocel_type", "issue"]
    return (
        objects
        .filter(
            pl.col("ocel_type").is_null()
            | (pl.col("ocel_type").str.strip_chars() == "")
        )
        .with_columns(pl.lit("missing_object_type").alias("issue"))
        .select(cols)
        .cast({c: pl.Utf8 for c in cols})
    )


def detect_missing_event_timestamp(src: SqliteInput) -> pl.DataFrame:
    """Find events whose ocel_time is NULL or empty/whitespace.

    `ocel_time` lives on the per-type sub-tables (`event_<CamelCase>`), not
    on `event` itself. This scans every event sub-table and reports the
    concrete target_table for the resolver to UPDATE.
    """
    with _connect(src) as conn:
        rows: list[dict] = []
        for event_type, table in _event_type_tables(conn):
            if not _has_column(conn, table, "ocel_time"):
                continue
            for ocel_id, ocel_time in conn.execute(
                f'SELECT ocel_id, ocel_time FROM "{table}"'
            ).fetchall():
                if _is_missing(ocel_time):
                    rows.append({
                        "ocel_id": ocel_id,
                        "event_type": event_type,
                        "target_table": table,
                        "actual_value": ocel_time,
                        "issue": "missing_event_timestamp",
                    })
    return _frame(
        rows,
        ["ocel_id", "event_type", "target_table", "actual_value", "issue"],
    )


def detect_missing_event(src: SqliteInput) -> pl.DataFrame:
    """Find event_object rows that reference an event id that has no row in
    the main `event` table.

    Counterpart to ``detect_missing_object``: the same rows also trip
    ``dangling_e2o_relationship`` (from the event side), but where the
    dangling task swaps to a plausible existing event, this task proposes
    creating the missing one. The dashboard routes them apart.

    Unlike missing_object we don't require a `<type>:` prefix on the id —
    the resolver infers the type from the qualifier + linked object types.
    """
    with _connect(src) as conn:
        known_event_ids = {
            r[0] for r in conn.execute("SELECT ocel_id FROM event").fetchall()
        }
        rows: list[dict] = []
        for ocel_event_id, ocel_object_id, ocel_qualifier in conn.execute(
            "SELECT ocel_event_id, ocel_object_id, ocel_qualifier FROM event_object"
        ).fetchall():
            if ocel_event_id is None or ocel_event_id in known_event_ids:
                continue
            rows.append({
                "ocel_event_id":   ocel_event_id,
                "ocel_object_id":  ocel_object_id,
                "ocel_qualifier":  ocel_qualifier,
                "issue":           "missing_event",
            })
    return _frame(
        rows,
        ["ocel_event_id", "ocel_object_id", "ocel_qualifier", "issue"],
    )


def detect_missing_event_type(src: SqliteInput) -> pl.DataFrame:
    """Find events in the main event table whose ocel_type is NULL or
    empty/whitespace.

    Mirrors ``detect_missing_object_type`` on the event side.
    """
    with _connect(src) as conn:
        events = pl.read_database("SELECT ocel_id, ocel_type FROM event", conn)

    cols = ["ocel_id", "ocel_type", "issue"]
    return (
        events
        .filter(
            pl.col("ocel_type").is_null()
            | (pl.col("ocel_type").str.strip_chars() == "")
        )
        .with_columns(pl.lit("missing_event_type").alias("issue"))
        .select(cols)
        .cast({c: pl.Utf8 for c in cols})
    )


def detect_missing_object(src: SqliteInput) -> pl.DataFrame:
    """Find event_object rows that reference an object id that doesn't exist
    in `object`, but whose id shape (a ``<type>:`` prefix from a real
    object_type) suggests it was meant to exist.

    This is the counterpart to ``dangling_e2o_relationship``: the same rows
    also trip that detector, but where the dangling task swaps to a
    plausible existing object, this task proposes creating the missing one.
    Routing to the correct issue is a dashboard concern (see _mapping).
    """
    with _connect(src) as conn:
        known_ids = {
            r[0] for r in conn.execute("SELECT ocel_id FROM object").fetchall()
        }
        # Type prefixes we consider legitimate (e.g. `orders:`, `items:`).
        prefixes = [ocel_type for ocel_type, _ in _object_type_tables(conn)]
        # Products don't follow the `<type>:<id>` convention; their `ocel_id`
        # is a bare product name. We rely purely on the referring qualifier
        # `product` to catch those.
        rows: list[dict] = []
        for ocel_event_id, ocel_object_id, ocel_qualifier in conn.execute(
            "SELECT ocel_event_id, ocel_object_id, ocel_qualifier FROM event_object"
        ).fetchall():
            if ocel_object_id in known_ids or ocel_object_id is None:
                continue
            # Match by `<type>:` prefix, else by qualifier hint.
            inferred = None
            for ptype in prefixes:
                if isinstance(ocel_object_id, str) and ocel_object_id.startswith(f"{ptype}:"):
                    inferred = ptype
                    break
            if inferred is None and ocel_qualifier == "product":
                inferred = "products"
            if inferred is None:
                # Opaque id (no prefix, no qualifier hint) → leave to the
                # dangling detector; we'd only be guessing.
                continue
            rows.append({
                "ocel_object_id": ocel_object_id,
                "inferred_type_from_prefix": inferred,
                "ocel_event_id": ocel_event_id,
                "ocel_qualifier": ocel_qualifier,
                "issue": "missing_object",
            })
    return _frame(
        rows,
        [
            "ocel_object_id", "inferred_type_from_prefix",
            "ocel_event_id", "ocel_qualifier", "issue",
        ],
    )


def detect_dangling_e2o_relationship(src: SqliteInput) -> pl.DataFrame:
    """Find event-to-object relationships referencing a non-existent event,
    a non-existent object, or both.

    Columns mirror detect_dangling_o2o_relationship:
        ocel_event_id, event_type, ocel_object_id, object_type,
        ocel_qualifier, missing_side, issue
    ``missing_side`` is ``"event"``, ``"object"``, or ``"both"``.
    """
    with _connect(src) as conn:
        e2o = pl.read_database(
            "SELECT ocel_event_id, ocel_object_id, ocel_qualifier FROM event_object",
            conn,
        )
        events  = pl.read_database("SELECT ocel_id, ocel_type FROM event",  conn).unique(subset=["ocel_id"])
        objects = pl.read_database("SELECT ocel_id, ocel_type FROM object", conn).unique(subset=["ocel_id"])

    # An empty `event_object` table would come back with `Null`-typed
    # columns and blow up the joins below. Force the id cols to Utf8.
    e2o = e2o.cast({"ocel_event_id": pl.Utf8, "ocel_object_id": pl.Utf8})

    # `_exists` sentinels let us distinguish "row absent from parent table"
    # (dangling) from "row present but ocel_type IS NULL" (a separate issue,
    # covered by detect_missing_event_type / detect_missing_object_type).
    events  = events.with_columns(pl.lit(True).alias("event_exists"))
    objects = objects.with_columns(pl.lit(True).alias("object_exists"))

    enriched = (
        e2o
        .join(
            events.rename({"ocel_id": "ocel_event_id", "ocel_type": "event_type"}),
            on="ocel_event_id",
            how="left",
        )
        .join(
            objects.rename({"ocel_id": "ocel_object_id", "ocel_type": "object_type"}),
            on="ocel_object_id",
            how="left",
        )
    )

    violations = enriched.filter(
        pl.col("event_exists").is_null() | pl.col("object_exists").is_null()
    )

    cols = [
        "ocel_event_id", "event_type",
        "ocel_object_id", "object_type",
        "ocel_qualifier", "missing_side", "issue",
    ]
    return (
        violations
        .with_columns(
            pl.when(pl.col("event_exists").is_null() & pl.col("object_exists").is_null())
            .then(pl.lit("both"))
            .when(pl.col("event_exists").is_null())
            .then(pl.lit("event"))
            .otherwise(pl.lit("object"))
            .alias("missing_side"),
            pl.lit("dangling_e2o_relationship").alias("issue"),
        )
        .select(cols)
        .cast({c: pl.Utf8 for c in cols})
    )


def detect_all(src: SqliteInput) -> dict[str, pl.DataFrame]:
    """Run all detectors and return their results keyed by check name."""
    return {
        "missing_object_type":              detect_missing_object_type(src),
        "missing_attribute_value":          detect_missing_attribute_value(src),
        "duplicate_objects_on_ids":         detect_duplicate_objects_on_ids(src),
        "duplicate_objects_on_attributes":  detect_duplicate_objects_on_attributes(src),
        "incorrect_attribute_datatype":     detect_incorrect_attribute_datatype(src),
        "dangling_o2o_relationship":        detect_dangling_o2o_relationship(src),
        "dangling_e2o_relationship":        detect_dangling_e2o_relationship(src),
        "missing_event_timestamp":          detect_missing_event_timestamp(src),
        "missing_event":                    detect_missing_event(src),
        "missing_event_type":               detect_missing_event_type(src),
        "missing_object":                   detect_missing_object(src),
    }
