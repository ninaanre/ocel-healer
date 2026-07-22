"""Everything between the LLM payload and the SQLite UPDATE."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from src.detection.error_detection import (
    _connect,
    _event_type_tables,
    _object_type_tables,
)
from src.llm.client import MIN_CONFIDENCE
from src.llm.sql_utils import quote


# --- Result type returned by IssueTask.parse_payload ----------------------

@dataclass
class ActionResult:
    """Task-side outcome of parsing the LLM payload.

    `kind` is one of:
      "update"   -- proposed concrete value; all fields populated.
      "delete"   -- remove duplicate rows keeping MIN(rowid).
      "insert"   -- create one or more new rows (see `inserts`); used by
                    tasks like `missing_object` that must add both a row to
                    `object` and a matching initial-state row to `object_<Type>`.
      "decline"  -- LLM said null; orchestrator attaches a routable target
                    (via task.suppressed_target) so override still works.
      "unrouted" -- no clean override target (e.g. duplicate_objects_on_ids).
    """
    kind: str
    target_table: str = ""
    target_pk: dict = field(default_factory=dict)
    column: str | None = None
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    # Only populated when kind == "insert". Each entry:
    #   {"table": str, "columns": dict[column_name, value]}
    inserts: list[dict] = field(default_factory=list)

    @classmethod
    def update(cls, **fields: Any) -> "ActionResult":
        return cls(kind="update", **fields)

    @classmethod
    def delete(cls, *, target_table: str, target_pk: dict, reason: str = "") -> "ActionResult":
        """Delete duplicate rows keeping the one with MIN(rowid)."""
        return cls(kind="delete", target_table=target_table, target_pk=target_pk, reason=reason)

    @classmethod
    def insert(cls, *, inserts: list[dict], reason: str = "") -> "ActionResult":
        """Insert one or more new rows. All inserts run in a single transaction."""
        return cls(kind="insert", reason=reason, inserts=list(inserts))

    @classmethod
    def decline(cls, reason: str) -> "ActionResult":
        return cls(kind="decline", reason=reason)

    @classmethod
    def unrouted(cls, reason: str, *, target_table: str = "") -> "ActionResult":
        # `target_table` is allowed for display only; apply_repair still
        # refuses noops without a full routable target (column + pk).
        return cls(kind="unrouted", reason=reason, target_table=target_table)


# --- Shared override-target builders --------------------------------------
# Used by the three tasks that write to `object_<type>.<attribute>` and the
# two dangling-relation tasks (source/target column swap).

def object_attribute_target(row: dict) -> dict | None:
    object_type = row.get("object_type")
    attr_col = row.get("attribute_name") or row.get("attribute")
    anchor_id = row.get("ocel_id") or row.get("ocel_object_id")
    if not (object_type and attr_col and anchor_id):
        return None
    old = row["actual_value"] if "actual_value" in row else row.get("attribute_values")
    return {
        "target_table": f"object_{object_type}",
        "target_pk": {"ocel_id": anchor_id},
        "column": attr_col,
        "old_value": old,
    }


def event_attribute_target(row: dict, *, sqlite_path: str | None = None) -> dict | None:
    """Target builder for repairing an event-attribute cell.

    Mirrors :func:`object_attribute_target`. Per-type event tables are
    named ``event_<ocel_type_map>`` where the ``ocel_type_map`` suffix
    comes from ``event_map_type`` and may differ from the paper-label
    ``event_type`` (e.g. type `"Create Order"` maps to
    ``event_CreateOrder``). When ``sqlite_path`` is supplied, this
    function resolves the map; otherwise it falls back to the raw event
    type as the suffix and relies on ``apply_repair``'s tolerant table
    resolver (see :func:`_resolve_table`) to case-match.
    """
    event_type = row.get("event_type")
    attr_col = row.get("attribute_name") or row.get("attribute")
    anchor_id = row.get("ocel_id") or row.get("ocel_event_id")
    if not (event_type and attr_col and anchor_id):
        return None
    old = row["actual_value"] if "actual_value" in row else None
    table = f"event_{event_type}"
    if sqlite_path:
        try:
            with _connect(sqlite_path) as _conn:
                row_map = _conn.execute(
                    "SELECT ocel_type_map FROM event_map_type WHERE ocel_type = ? LIMIT 1",
                    (event_type,),
                ).fetchone()
                if row_map and row_map[0]:
                    table = f"event_{row_map[0]}"
        except sqlite3.Error:
            # Fall through to the raw suffix — apply_repair's resolver may
            # still case-match it against the schema.
            pass
    return {
        "target_table": table,
        "target_pk": {"ocel_id": anchor_id},
        "column": attr_col,
        "old_value": old,
    }


def relation_swap_target(row: dict, *, table: str, sides: dict[str, dict]) -> dict | None:
    """`sides` maps each missing_side value to {"column": <write>, "pk": [...]}."""
    spec = sides.get(row.get("missing_side"))
    if spec is None or not all(row.get(c) is not None for c in spec["pk"]):
        return None
    return {
        "target_table": table,
        "target_pk": {c: row[c] for c in spec["pk"]},
        "column": spec["column"],
        "old_value": row.get(spec["column"]),
    }


# --- Action-dict factory --------------------------------------------------
# Owns the cross-task concerns: confidence-gating, proposed_value extraction,
# routable-decline wrapping. The dashboard consumes these dicts.

_PROPOSED_KEYS = (
    "coerced_value", "inferred_value", "inferred_type",
    "inferred_referent", "canonical_id", "canonical_value",
    "inferred_timestamp", "ocel_type", "suggested_value",
)

_EMPTY_TARGET = {"target_table": "", "target_pk": {}, "column": None, "old_value": None}


def _action(kind: str, *, target: dict, new_value: Any, rationale: str,
            confidence: float, issue_key: str, proposed_value: Any) -> dict:
    """Assemble one action dict. All branches of from_task_result share this shape."""
    return {
        "kind": kind, **target, "new_value": new_value,
        "rationale": rationale, "confidence": confidence,
        "issue_key": issue_key, "proposed_value": proposed_value,
    }


def unknown_issue_noop(issue_key: str) -> dict:
    return _action(
        "noop", target=_EMPTY_TARGET, new_value=None,
        rationale=f"No LLM task defined for {issue_key!r}.",
        confidence=0.0, issue_key=issue_key, proposed_value=None,
    )


def malformed_output_noop(task, row: dict, error: str) -> dict:
    """Surface an LLMOutputInvalid as a routable noop so the UI can offer an override."""
    return _action(
        "noop", target=task.suppressed_target(row) or _EMPTY_TARGET,
        new_value=None,
        rationale=f"LLM reply did not match the task schema: {error}",
        confidence=0.0, issue_key=task.issue_key, proposed_value=None,
    )


def from_task_result(task, row: dict, payload: dict) -> dict:
    confidence = float(payload.get("confidence", 0.0))
    rationale = str(payload.get("rationale", ""))
    proposed_value = next(
        (payload[k] for k in _PROPOSED_KEYS if payload.get(k) is not None),
        None,
    )
    issue_key = task.issue_key

    # Confidence gate -- short-circuits the task's own parse path. We still
    # try for a routable target so the dashboard can offer an override.
    threshold = task.min_confidence if task.min_confidence is not None else MIN_CONFIDENCE
    if confidence < threshold:
        bits = [f"Confidence {confidence:.2f} below threshold {MIN_CONFIDENCE:.2f}."]
        if proposed_value is not None:
            bits.append(f"Would have proposed: {proposed_value!r}.")
        if rationale.strip():
            bits.append(f"Rationale: {rationale.strip()}")
        return _action(
            "noop", target=task.suppressed_target(row) or _EMPTY_TARGET,
            new_value=None, rationale=" ".join(bits),
            confidence=confidence, issue_key=issue_key, proposed_value=proposed_value,
        )

    result = task.parse_payload(row, payload)

    if result.kind == "update":
        return _action(
            "update",
            target={"target_table": result.target_table, "target_pk": result.target_pk,
                    "column": result.column, "old_value": result.old_value},
            new_value=result.new_value, rationale=rationale,
            confidence=confidence, issue_key=issue_key, proposed_value=result.new_value,
        )

    if result.kind == "delete":
        return _action(
            "delete",
            target={"target_table": result.target_table, "target_pk": result.target_pk,
                    "column": None, "old_value": None},
            new_value=None, rationale=result.reason or rationale,
            confidence=confidence, issue_key=issue_key, proposed_value=None,
        )

    if result.kind == "insert":
        # target_table for display only; the real payload lives under "inserts".
        display_table = result.inserts[0]["table"] if result.inserts else ""
        action = _action(
            "insert",
            target={"target_table": display_table, "target_pk": {},
                    "column": None, "old_value": None},
            new_value=None, rationale=result.reason or rationale,
            confidence=confidence, issue_key=issue_key, proposed_value=result.inserts,
        )
        action["inserts"] = result.inserts
        return action

    if result.kind == "decline":
        return _action(
            "noop", target=task.suppressed_target(row) or _EMPTY_TARGET,
            new_value=None, rationale=result.reason,
            confidence=confidence, issue_key=issue_key, proposed_value=None,
        )

    # unrouted -- keep target_table for display, but no routable pk/column.
    return _action(
        "noop",
        target={"target_table": result.target_table, "target_pk": {},
                "column": None, "old_value": None},
        new_value=None, rationale=result.reason,
        confidence=confidence, issue_key=issue_key, proposed_value=proposed_value,
    )


# --- Type-affinity coercion (for the apply path) --------------------------
# Used only by apply_repair when the user supplies an override; ensures we
# don't silently re-introduce a `incorrect_attribute_datatype` violation via the
# fix path. Mirrors the buckets used by the detector's _value_matches_type.

def _column_affinity(conn: sqlite3.Connection, table: str, column: str) -> str:
    for _, name, dtype, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name == column:
            return (dtype or "").upper()
    return ""


def _to_int(raw: Any, affinity: str) -> Any:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        # Tolerate "42" and "42.0", reject "3.14".
        try:
            return int(raw.strip())
        except ValueError:
            try:
                f = float(raw.strip())
            except ValueError as exc:
                raise ValueError(f"override {raw!r} is not compatible with INTEGER affinity") from exc
            if f.is_integer():
                return int(f)
    raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")


def _to_float(raw: Any, affinity: str) -> Any:
    if isinstance(raw, bool):
        return float(int(raw))
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity") from exc
    raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")


def _to_text(raw: Any, affinity: str) -> Any:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float, bool)):
        return str(raw)
    raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")


def _to_blob(raw: Any, affinity: str) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")


# Token-in-affinity -> coercer. Order matters: SQLite affinity rules say
# INT wins over anything else, then REAL/FLOA/etc, then CHAR/TEXT/CLOB,
# then BLOB. An empty/unknown affinity passes the value through.
_AFFINITY_RULES = (
    (("INT",),                                       _to_int),
    (("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL"), _to_float),
    (("CHAR", "TEXT", "CLOB"),                       _to_text),
    (("BLOB",),                                      _to_blob),
)


def _coerce_for_affinity(raw: Any, affinity: str) -> Any:
    if raw is None:
        return None
    t = (affinity or "").upper()
    if not t:
        return raw  # No declared affinity -> accept the value as-is.
    for tokens, coerce in _AFFINITY_RULES:
        if any(k in t for k in tokens):
            return coerce(raw, affinity)
    return raw


# --- Apply path -- writes (or dry-runs) one UPDATE ------------------------

_OVERRIDE_UNSET = object()


def _allowed_tables(conn: sqlite3.Connection) -> set[str]:
    """Whitelist: base OCEL2 tables + every per-type object_<Type> and event_<Type>."""
    return (
        {"object", "event", "object_object", "event_object"}
        | {t for _, t in _object_type_tables(conn)}
        | {t for _, t in _event_type_tables(conn)}
    )


def _resolve_table(conn: sqlite3.Connection, table: str) -> str:
    """Return the schema-cased table name, tolerating case mismatches."""
    allowed = _allowed_tables(conn)
    if table in allowed:
        return table
    table_map = {t.lower(): t for t in allowed}
    if table.lower() in table_map:
        return table_map[table.lower()]
    raise ValueError(f"Refusing to repair: unknown table {table!r}.")


def _validate_target(conn: sqlite3.Connection, action: dict) -> tuple[str, str]:
    """Whitelist target_table, column and target_pk against the live schema.
    Returns (table, column); raises ValueError on any mismatch."""
    table = _resolve_table(conn, action["target_table"])
    cols = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    col = action.get("column")
    if col is not None and col not in cols:
        raise ValueError(f"Refusing to repair: unknown column {col!r} in {table!r}.")
    bad_pk = set(action["target_pk"]) - cols
    if bad_pk:
        raise ValueError(f"Refusing to repair: target_pk uses unknown column(s) {bad_pk!r}.")
    return table, col


def _validate_insert(conn: sqlite3.Connection, entry: dict) -> tuple[str, dict[str, Any]]:
    """Whitelist an insert entry against the live schema.

    Returns (resolved_table, coerced_columns). Every column in `entry["columns"]`
    must exist on the table; values are coerced through SQLite affinity so we
    don't silently re-introduce a datatype violation via the fix path.
    """
    if not isinstance(entry, dict) or "table" not in entry or "columns" not in entry:
        raise ValueError(f"Refusing to insert: malformed entry {entry!r}.")
    if not isinstance(entry["columns"], dict) or not entry["columns"]:
        raise ValueError(f"Refusing to insert: entry has no columns: {entry!r}.")
    table = _resolve_table(conn, entry["table"])
    schema_cols = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    unknown = set(entry["columns"]) - schema_cols
    if unknown:
        raise ValueError(
            f"Refusing to insert: unknown column(s) {sorted(unknown)!r} in {table!r}."
        )
    coerced = {
        col: _coerce_for_affinity(val, _column_affinity(conn, table, col))
        for col, val in entry["columns"].items()
    }
    return table, coerced


def apply_repair(
    sqlite_path: str,
    action: dict,
    *,
    dry_run: bool = True,
    override_value: Any = _OVERRIDE_UNSET,
) -> str:
    """Execute (or dry-run) an action dict. Validates table/column names against the schema.

    When `override_value` is provided, it replaces the action's `new_value`
    (after a type-affinity coercion check) and the rationale is stamped with
    a USER OVERRIDE prefix. An override can also rescue a noop -- as long as
    the noop carries a routable target_table + column + target_pk.
    """
    has_override = override_value is not _OVERRIDE_UNSET

    if action["kind"] == "noop":
        if not has_override:
            raise ValueError(f"Refusing to apply noop: {action['rationale']}")
        if not (action.get("target_table") and action.get("column") and action.get("target_pk")):
            raise ValueError(
                "Override cannot be applied: this noop has no routable target "
                "(missing target_table, column, or target_pk)."
            )
    elif action["kind"] == "insert":
        if has_override:
            raise ValueError("override not supported for insert actions")
    elif action["kind"] not in ("update", "delete"):
        raise NotImplementedError(f"action kind {action['kind']!r} not supported.")

    with _connect(sqlite_path) as conn:
        if action["kind"] == "insert":
            entries = action.get("inserts") or []
            if not entries:
                raise ValueError("Refusing to apply insert: no rows to insert.")
            planned: list[tuple[str, dict[str, Any], str, tuple]] = []
            for entry in entries:
                table, coerced = _validate_insert(conn, entry)
                cols = list(coerced)
                sql = (
                    f'INSERT INTO {quote(table)} '
                    f'({", ".join(quote(c) for c in cols)}) '
                    f'VALUES ({", ".join("?" for _ in cols)})'
                )
                planned.append((table, coerced, sql, tuple(coerced[c] for c in cols)))

            rendered = "\n--\n".join(
                f"{sql}\n  with params = {params!r}" for _, _, sql, params in planned
            )
            if dry_run:
                return f"-- DRY RUN (no changes written)\n{rendered}"
            n = 0
            with conn:
                for _, _, sql, params in planned:
                    n += conn.execute(sql, params).rowcount
            return (
                f"Committed: {n} row(s) inserted across "
                f"{len({t for t, *_ in planned})} table(s).\n{rendered}"
            )

        table, col = _validate_target(conn, action)

        if action["kind"] == "delete":
            pk_items = list(action["target_pk"].items())
            if len(pk_items) != 1:
                raise ValueError("delete action requires exactly one primary key column")
            pk_col, pk_val = pk_items[0]
            sql = (
                f"DELETE FROM {quote(table)} WHERE {quote(pk_col)} = ? "
                f"AND rowid NOT IN (SELECT MIN(rowid) FROM {quote(table)} WHERE {quote(pk_col)} = ?)"
            )
            params = (pk_val, pk_val)
            rendered = f"{sql}\n  with params = {params!r}"
            if dry_run:
                return f"-- DRY RUN (no changes written)\n{rendered}"
            with conn:
                n = conn.execute(sql, params).rowcount
            return f"Committed: {n} duplicate row(s) deleted.\n{rendered}"

        if has_override:
            new_value = _coerce_for_affinity(override_value, _column_affinity(conn, table, col))
            llm_rationale = action.get("rationale") or "<no LLM rationale>"
            rationale = f"USER OVERRIDE: {override_value!r}. LLM said: {llm_rationale}"
        else:
            new_value = action["new_value"]
            rationale = action.get("rationale", "")

        where = " AND ".join(f"{quote(c)} = ?" for c in action["target_pk"])
        sql = f'UPDATE {quote(table)} SET {quote(col)} = ? WHERE {where}'
        params = (new_value, *action["target_pk"].values())

        header = f"-- {rationale}\n" if has_override else ""
        rendered = f"{header}{sql}\n  with params = {params!r}"
        if dry_run:
            return f"-- DRY RUN (no changes written)\n{rendered}"
        with conn:
            n = conn.execute(sql, params).rowcount
        return f"Committed: {n} row(s) affected.\n{rendered}"
