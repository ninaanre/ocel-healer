import sqlite3
from contextlib import contextmanager
from typing import Iterator

import polars as pl


SqliteInput = str | sqlite3.Connection

# OCEL2 reserved columns that aren't user-defined attributes.
_OCEL_RESERVED = {"ocel_id", "ocel:timestamp", "ocel_type"}

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


def _iter_object_attrs(
    conn: sqlite3.Connection,
) -> Iterator[tuple[str, object, str, str, object]]:
    """Yield (object_type, ocel_id, attr_name, declared_type, value) for every
    non-reserved attribute cell across all per-type object tables.
    """
    for ocel_type, table in _object_type_tables(conn):
        cols = _column_info(conn, table)
        if not cols:
            continue
        attr_names = [c for c, _ in cols]
        quoted = ", ".join(f'"{c}"' for c in attr_names)
        for ocel_id, *values in conn.execute(
            f'SELECT ocel_id, {quoted} FROM "{table}"'
        ).fetchall():
            for (attr, declared), value in zip(cols, values):
                yield ocel_type, ocel_id, attr, declared, value


def detect_missing_attributes(src: SqliteInput) -> pl.DataFrame:
    """Find object rows where any attribute value is NULL or empty/whitespace."""
    with _connect(src) as conn:
        rows = [
            {
                "object_type": ocel_type,
                "ocel_id": ocel_id,
                "attribute": attr,
                "actual_value": value,
                "issue": "missing_value",
            }
            for ocel_type, ocel_id, attr, _, value in _iter_object_attrs(conn)
            if _is_missing(value)
        ]
    return _frame(
        rows, ["object_type", "ocel_id", "attribute", "actual_value", "issue"]
    )


def detect_incorrect_datatypes(src: SqliteInput) -> pl.DataFrame:
    """Find attribute values that don't match the column's declared SQLite type.

    NULL/empty values are skipped (covered by `detect_missing_attributes`).
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
                "issue": "wrong_datatype",
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


def detect_dangling_o2o_relations(src: SqliteInput) -> pl.DataFrame:
    """Find object-to-object relationships referencing missing objects."""
    with _connect(src) as conn:
        objects = pl.read_database("SELECT ocel_id, ocel_type FROM object", conn)
        o2o = pl.read_database(
            "SELECT ocel_source_id, ocel_target_id, ocel_qualifier FROM object_object",
            conn,
        )

    # Dedup: the object table can legitimately list the same id twice in this dataset.
    known = objects.unique(subset=["ocel_id"])

    enriched = o2o.join(
        known.rename({"ocel_id": "ocel_source_id", "ocel_type": "source_type"}),
        on="ocel_source_id",
        how="left",
    ).join(
        known.rename({"ocel_id": "ocel_target_id", "ocel_type": "target_type"}),
        on="ocel_target_id",
        how="left",
    )

    violations = enriched.filter(
        pl.col("source_type").is_null() | pl.col("target_type").is_null()
    )

    cols = [
        "ocel_source_id", "source_type",
        "ocel_target_id", "target_type",
        "ocel_qualifier", "missing_side", "issue",
    ]
    return violations.with_columns(
        pl.when(pl.col("source_type").is_null() & pl.col("target_type").is_null())
        .then(pl.lit("both"))
        .when(pl.col("source_type").is_null())
        .then(pl.lit("source"))
        .otherwise(pl.lit("target"))
        .alias("missing_side"),
        pl.lit("dangling_o2o").alias("issue"),
    ).select(cols).cast({c: pl.Utf8 for c in cols})


def detect_all(src: SqliteInput) -> dict[str, pl.DataFrame]:
    """Run all detectors and return their results keyed by check name."""
    return {
        "missing_attributes": detect_missing_attributes(src),
        "incorrect_datatypes": detect_incorrect_datatypes(src),
        "dangling_o2o_relations": detect_dangling_o2o_relations(src),
    }
