"""Deterministic, representative sampling for LLM context.

Replaces the earlier `LIMIT 5` / `LIMIT 200` sites in the task context
builders. Two guarantees:

  - **Deterministic** — same anchor + same DB → same sample. SQLite
    doesn't guarantee scan order across schema changes / vacuums, so we
    hash-order by rowid using a fixed multiplier (Knuth's 2654435761)
    plus a per-anchor seed.
  - **Representative** — for `sample_peers`, when `target_col` is given
    we stratify: at least half the peers have a non-null value in
    `target_col`, so the LLM sees examples of what a valid value looks
    like. For `sample_candidates`, when `expected_type` matches an
    existing ocel_type stem we pre-filter to that type.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.detection.error_detection import _column_info, _object_type_tables
from src.llm.sql_utils import quote, table_for_type


# Knuth's multiplicative hash constant. Combined with a per-anchor seed it
# gives a stable, decorrelated pseudo-random order across rowids.
_HASH_MULT = 2654435761
_HASH_MOD = 2**31 - 1


def _seed_from(anchor_id: str | None, seed: int) -> int:
    """Mix `anchor_id` into `seed` so different anchors see different peers."""
    if anchor_id is None:
        return seed & _HASH_MOD
    h = seed
    for ch in str(anchor_id):
        h = (h * 131 + ord(ch)) & _HASH_MOD
    return h


def _hash_order_sql(seed: int) -> str:
    """SQLite ORDER BY expression that gives a deterministic pseudo-random order."""
    # ((rowid * mult) + seed) % mod  — stable across runs, cheap to compute.
    return f"((rowid * {_HASH_MULT}) + {seed}) % {_HASH_MOD}"


def sample_peers(
    conn: sqlite3.Connection,
    row: dict,
    *,
    anchor_id: str,
    anchor_type: str | None,
    k: int = 5,
    seed: int = 0,
    target_col: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to `k` peer rows from the anchor's object-type table.

    When `target_col` is given, stratify so at least half of the returned
    peers have a non-null value in that column. Excludes the anchor itself
    and (when present) delta-change rows (`ocel_changed_field IS NULL`).
    """
    table = table_for_type(conn, anchor_type)
    if not table:
        return []

    cols = [c for c, _ in _column_info(conn, table)]
    if not cols:
        return []

    # Select ocel_id + the non-reserved attribute columns. `_column_info`
    # strips reserved names, but ocel_id is useful for the LLM to tell
    # peers apart and is required for internal dedup between stratified
    # and fill rows.
    select_cols = ["ocel_id", *cols]
    quoted_cols = ", ".join(quote(c) for c in select_cols)
    all_cols = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}

    where_clauses = ["ocel_id != ?"]
    params: list[Any] = [anchor_id]
    if "ocel_changed_field" in all_cols:
        where_clauses.append('"ocel_changed_field" IS NULL')
    base_where = " AND ".join(where_clauses)

    order = _hash_order_sql(_seed_from(anchor_id, seed))

    if target_col and target_col in all_cols:
        # Stratified: half with target_col non-null, half unrestricted.
        half = max(1, k // 2)
        rest = k - half

        strat_sql = (
            f'SELECT {quoted_cols} FROM {quote(table)} '
            f'WHERE {base_where} AND {quote(target_col)} IS NOT NULL '
            f'ORDER BY {order} LIMIT ?'
        )
        strat_rows = conn.execute(strat_sql, (*params, half)).fetchall()

        seen_ids = {r[0] for r in strat_rows}
        seen_ids.add(anchor_id)

        # Fill remainder with any peers not already included.
        placeholders = ",".join("?" * len(seen_ids))
        fill_sql = (
            f'SELECT {quoted_cols} FROM {quote(table)} '
            f'WHERE {base_where} AND ocel_id NOT IN ({placeholders}) '
            f'ORDER BY {order} LIMIT ?'
        )
        fill_params = (*params, *seen_ids, rest)
        fill_rows = conn.execute(fill_sql, fill_params).fetchall()

        rows = list(strat_rows) + list(fill_rows)
    else:
        sql = (
            f'SELECT {quoted_cols} FROM {quote(table)} '
            f'WHERE {base_where} '
            f'ORDER BY {order} LIMIT ?'
        )
        rows = conn.execute(sql, (*params, k)).fetchall()

    return [dict(zip(select_cols, r)) for r in rows]


def sample_candidates(
    conn: sqlite3.Connection,
    *,
    anchor_id: str | None,
    k: int = 50,
    seed: int = 0,
    expected_type: str | None = None,
    kind: str = "object",
) -> list[dict[str, Any]]:
    """Return up to `k` candidate {ocel_id, ocel_type} rows.

    `kind='object'` samples from `object`; `kind='event'` from `event`.
    When `expected_type` matches a known ocel_type stem (case-insensitive),
    the sample is pre-filtered to that type. Otherwise the sample mixes
    deterministic-random rows with rows sharing an id-prefix with `anchor_id`.
    """
    if kind not in ("object", "event"):
        raise ValueError(f"kind must be 'object' or 'event', got {kind!r}")

    table = kind
    known_types = {t.lower() for t, _ in _object_type_tables(conn)} if kind == "object" else set()

    expected = _resolve_expected_type(expected_type, known_types)
    order = _hash_order_sql(_seed_from(anchor_id, seed))

    if expected is not None:
        sql = (
            f'SELECT ocel_id, ocel_type FROM {table} '
            f'WHERE LOWER(ocel_type) = ? '
            f'ORDER BY {order} LIMIT ?'
        )
        rows = conn.execute(sql, (expected.lower(), k)).fetchall()
        return [{"ocel_id": r[0], "ocel_type": r[1]} for r in rows]

    # Two-strand fallback: half random, half sharing an id prefix. Query
    # `k` from each strand (not `half`/`rest`) so overlap between the two
    # strands doesn't drop the returned count below k.
    rand_sql = f'SELECT ocel_id, ocel_type FROM {table} ORDER BY {order} LIMIT ?'
    rand_rows = conn.execute(rand_sql, (k,)).fetchall()

    prefix_rows: list[tuple[Any, Any]] = []
    if anchor_id:
        prefix = _id_prefix(str(anchor_id))
        if prefix:
            prefix_sql = (
                f'SELECT ocel_id, ocel_type FROM {table} '
                f'WHERE ocel_id LIKE ? AND ocel_id != ? '
                f'ORDER BY {order} LIMIT ?'
            )
            prefix_rows = conn.execute(prefix_sql, (f"{prefix}%", anchor_id, k)).fetchall()

    # Interleave prefix-first (they're the higher-signal picks) then random,
    # deduping and stopping at k.
    combined: list[tuple[Any, Any]] = []
    seen: set[Any] = set()
    for r in (*prefix_rows, *rand_rows):
        if r[0] in seen or r[0] == anchor_id:
            continue
        seen.add(r[0])
        combined.append(r)
        if len(combined) >= k:
            break

    return [{"ocel_id": r[0], "ocel_type": r[1]} for r in combined]


def _resolve_expected_type(expected: str | None, known: set[str]) -> str | None:
    """Match `expected` (from a qualifier) against known types, tolerating stems.

    e.g. qualifier='customer' matches ocel_type='customers'.
    """
    if not expected or not known:
        return None
    needle = expected.lower()
    if needle in known:
        return needle
    # Plural / singular tolerance.
    if needle.endswith("s") and needle[:-1] in known:
        return needle[:-1]
    if (needle + "s") in known:
        return needle + "s"
    return None


def _id_prefix(ocel_id: str) -> str:
    """Split off the leading token before the first '-' or '_'."""
    for sep in ("-", "_"):
        if sep in ocel_id:
            head = ocel_id.split(sep, 1)[0]
            if head and len(head) <= 8:
                return head + sep
    return ""


__all__ = ["sample_peers", "sample_candidates"]
