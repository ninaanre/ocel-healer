# TODO: refine & fix this file!

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.detection.error_detection import _connect, _object_type_tables
from src.llm.client import MIN_CONFIDENCE
from src.llm.sql_utils import quote


@dataclass
class ActionResult:
    """Task-side outcome of parsing the LLM payload.

    `kind` is one of:
      "update"   -- proposed concrete value; all fields populated.
      "decline"  -- LLM said null; orchestrator attaches a routable target
                    (via task.suppressed_target) so override still works.
      "unrouted" -- no clean override target (e.g. duplicate_object_ids).
    """
    kind: str
    target_table: str = ""
    target_pk: dict = field(default_factory=dict)
    column: str | None = None
    old_value: Any = None
    new_value: Any = None
    reason: str = ""

    @classmethod
    def update(
        cls,
        *,
        target_table: str,
        target_pk: dict,
        column: str,
        old_value: Any,
        new_value: Any,
    ) -> "ActionResult":
        return cls(
            kind="update",
            target_table=target_table, target_pk=target_pk,
            column=column, old_value=old_value, new_value=new_value,
        )

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
    if not object_type or not attr_col or not anchor_id:
        return None
    return {
        "target_table": f"object_{object_type}",
        "target_pk": {"ocel_id": anchor_id},
        "column": attr_col,
        "old_value": row.get("actual_value") if "actual_value" in row else row.get("attribute_values"),
    }


def relation_swap_target(
    row: dict,
    *,
    table: str,
    sides: dict[str, dict],
) -> dict | None:
    """`sides` maps each missing_side value to {"column": <write>, "pk": [...]}."""
    spec = sides.get(row.get("missing_side"))
    if spec is None:
        return None
    pk_cols = spec["pk"]
    if not all(row.get(c) is not None for c in pk_cols):
        return None
    return {
        "target_table": table,
        "target_pk": {c: row[c] for c in pk_cols},
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


def unknown_issue_noop(issue_key: str) -> dict:
    return {
        "kind": "noop", "target_table": "", "target_pk": {}, "column": None,
        "old_value": None, "new_value": None,
        "rationale": f"No LLM task defined for {issue_key!r}.",
        "confidence": 0.0, "issue_key": issue_key,
    }


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
    if confidence < MIN_CONFIDENCE:
        bits = [f"Confidence {confidence:.2f} below threshold {MIN_CONFIDENCE:.2f}."]
        if proposed_value is not None:
            bits.append(f"Would have proposed: {proposed_value!r}.")
        if rationale.strip():
            bits.append(f"Rationale: {rationale.strip()}")
        target = task.suppressed_target(row) or _EMPTY_TARGET
        return {
            "kind": "noop",
            "target_table": target["target_table"],
            "target_pk": target["target_pk"],
            "column": target["column"],
            "old_value": target["old_value"],
            "new_value": None,
            "rationale": " ".join(bits),
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": proposed_value,
        }

    result = task.parse_payload(row, payload)

    if result.kind == "update":
        return {
            "kind": "update",
            "target_table": result.target_table,
            "target_pk": result.target_pk,
            "column": result.column,
            "old_value": result.old_value,
            "new_value": result.new_value,
            "rationale": rationale,
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": result.new_value,
        }

    if result.kind == "decline":
        target = task.suppressed_target(row) or _EMPTY_TARGET
        return {
            "kind": "noop",
            "target_table": target["target_table"],
            "target_pk": target["target_pk"],
            "column": target["column"],
            "old_value": target["old_value"],
            "new_value": None,
            "rationale": result.reason,
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": None,
        }

    # unrouted -- no override target available.
    return {
        "kind": "noop", "target_table": result.target_table,
        "target_pk": {}, "column": None,
        "old_value": None, "new_value": None,
        "rationale": result.reason,
        "confidence": confidence,
        "issue_key": issue_key,
        "proposed_value": proposed_value,
    }


# --- Type-affinity coercion (for the apply path) --------------------------
# Used only by apply_repair when the user supplies an override; ensures we
# don't silently re-introduce an `incorrect_datatypes` violation via the
# fix path. Mirrors the buckets used by the detector's _value_matches_type.

def _column_affinity(conn: sqlite3.Connection, table: str, column: str) -> str:
    for _, name, dtype, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name == column:
            return (dtype or "").upper()
    return ""


def _coerce_for_affinity(raw: Any, affinity: str) -> Any:
    if raw is None:
        return None
    t = (affinity or "").upper()
    if not t:
        # No declared affinity -> accept the value as-is.
        return raw

    if "INT" in t:
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
        if isinstance(raw, str):
            s = raw.strip()
            try:
                return int(s)
            except ValueError:
                # Tolerate "42.0" -> 42 but not "3.14".
                try:
                    f = float(s)
                except ValueError:
                    raise ValueError(f"override {raw!r} is not compatible with INTEGER affinity")
                if f.is_integer():
                    return int(f)
                raise ValueError(f"override {raw!r} is not an integer ({affinity})")
        raise ValueError(f"override {raw!r} is not compatible with INTEGER affinity")

    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        if isinstance(raw, bool):
            return float(int(raw))
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw.strip())
            except ValueError:
                raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")
        raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")

    if any(k in t for k in ("CHAR", "TEXT", "CLOB")):
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (int, float, bool)):
            return str(raw)
        raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")

    if "BLOB" in t:
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if isinstance(raw, str):
            return raw.encode("utf-8")
        raise ValueError(f"override {raw!r} is not compatible with BLOB affinity")

    return raw


# --- Apply path -- writes (or dry-runs) one UPDATE ------------------------

_OVERRIDE_UNSET = object()


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
        if not action.get("target_table") or not action.get("column") or not action.get("target_pk"):
            raise ValueError(
                "Override cannot be applied: this noop has no routable target "
                "(missing target_table, column, or target_pk)."
            )
    elif action["kind"] != "update":
        raise NotImplementedError(f"action kind {action['kind']!r} not supported.")

    with _connect(sqlite_path) as conn:
        # Whitelist table + columns against the live schema.
        allowed_tables = {"object", "event", "object_object", "event_object"} | {
            t for _, t in _object_type_tables(conn)
        }
        table = action["target_table"]
        if table not in allowed_tables:
            raise ValueError(f"Refusing to repair: unknown table {table!r}.")
        cols = {name for _, name, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        col = action["column"]
        if col not in cols:
            raise ValueError(f"Refusing to repair: unknown column {col!r} in {table!r}.")
        bad_pk = set(action["target_pk"]) - cols
        if bad_pk:
            raise ValueError(f"Refusing to repair: target_pk uses unknown column(s) {bad_pk!r}.")

        if has_override:
            new_value = _coerce_for_affinity(override_value, _column_affinity(conn, table, col))
            llm_rationale = action.get("rationale", "") or "<no LLM rationale>"
            effective_rationale = f"USER OVERRIDE: {override_value!r}. LLM said: {llm_rationale}"
        else:
            new_value = action["new_value"]
            effective_rationale = action.get("rationale", "")

        where = " AND ".join(f"{quote(c)} = ?" for c in action["target_pk"])
        sql = f'UPDATE {quote(table)} SET {quote(col)} = ? WHERE {where}'
        params = (new_value, *action["target_pk"].values())

        header = f"-- {effective_rationale}\n" if has_override else ""
        rendered = f"{header}{sql}\n  with params = {params!r}"
        if dry_run:
            return f"-- DRY RUN (no changes written)\n{rendered}"
        with conn:
            cur = conn.execute(sql, params)
            n = cur.rowcount
        return f"Committed: {n} row(s) affected.\n{rendered}"
