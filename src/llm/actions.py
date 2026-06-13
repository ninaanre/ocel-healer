"""Everything between the LLM payload and the SQLite UPDATE."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from src.detection.error_detection import _connect, _object_type_tables
from src.llm.client import MIN_CONFIDENCE
from src.llm.sql_utils import quote


# --- Result type returned by IssueTask.parse_payload ----------------------

@dataclass
class ActionResult:
    """Task-side outcome of parsing the LLM payload.

    `kind` is one of:
      "update"   -- proposed concrete value; all fields populated.
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

    @classmethod
    def update(cls, **fields: Any) -> "ActionResult":
        return cls(kind="update", **fields)

    @classmethod
    def delete(cls, *, target_table: str, target_pk: dict, reason: str = "") -> "ActionResult":
        """Delete duplicate rows keeping the one with MIN(rowid)."""
        return cls(kind="delete", target_table=target_table, target_pk=target_pk, reason=reason)

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
# don't silently re-introduce a `wrong_attribute_datatype` violation via the
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


def _validate_target(conn: sqlite3.Connection, action: dict) -> tuple[str, str]:
    """Whitelist target_table, column and target_pk against the live schema.
    Returns (table, column); raises ValueError on any mismatch."""
    allowed_tables = {"object", "event", "object_object", "event_object"} | {
        t for _, t in _object_type_tables(conn)
    }
    table = action["target_table"]
    if table not in allowed_tables:
        # Try case-insensitive match (ocel_type vs ocel_type_map may differ in casing)
        table_map = {t.lower(): t for t in allowed_tables}
        if table.lower() not in table_map:
            raise ValueError(f"Refusing to repair: unknown table {table!r}.")
        table = table_map[table.lower()]
    cols = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    col = action.get("column")
    if col is not None and col not in cols:
        raise ValueError(f"Refusing to repair: unknown column {col!r} in {table!r}.")
    bad_pk = set(action["target_pk"]) - cols
    if bad_pk:
        raise ValueError(f"Refusing to repair: target_pk uses unknown column(s) {bad_pk!r}.")
    return table, col


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
    elif action["kind"] not in ("update", "delete"):
        raise NotImplementedError(f"action kind {action['kind']!r} not supported.")

    with _connect(sqlite_path) as conn:
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
