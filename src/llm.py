from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any

from src.detection.error_detection import (
    _column_info,
    _connect,
    _object_type_tables,
)


MODEL = os.getenv("OCEL_LLM_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MIN_CONFIDENCE = float(os.getenv("OCEL_LLM_MIN_CONFIDENCE", "0.5"))


def ollama_ready() -> tuple[bool, list[str]]:
    """Return (reachable, available_models). Never raises."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False, []
    return True, [m.get("name", "") for m in data.get("models", []) if m.get("name")]


SYSTEM_PROMPT = (
    "You are a domain expert in object-centric process mining (OCEL2). "
    "You receive one data-quality violation plus a small slice of local context "
    "(the affected object's attributes, the events touching it, neighbouring "
    "objects, and a few peers of the same type). Reason from attribute names, "
    "activity sequences, and qualifiers — not outside knowledge. Never invent "
    "ocel_ids that don't appear in the context. Always include a `confidence` "
    "in [0,1]. Reply with ONLY a JSON object — no prose, no markdown fences."
)


def _call_ollama(user_prompt: str) -> dict[str, Any]:
    """One JSON-mode call to Ollama. Returns the parsed dict."""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        # Strip fenced wrappers just in case the model adds them.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text.strip())


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_for_type(conn: sqlite3.Connection, ocel_type: str | None) -> str | None:
    if not ocel_type:
        return None
    for t, table in _object_type_tables(conn):
        if t == ocel_type:
            return table
    return None


def _build_context(conn: sqlite3.Connection, issue_key: str, row: dict) -> dict:
    """Assemble the JSON context block sent in the user prompt."""
    ctx: dict[str, Any] = {"issue_key": issue_key, "violation": dict(row)}
    ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]

    # Pick the anchor object/event for this issue.
    anchor_id = row.get("ocel_id") or row.get("ocel_object_id") or row.get("ocel_source_id")
    anchor_type = row.get("object_type") or row.get("source_type")

    if anchor_id:
        # Attributes from the per-type table.
        attrs: dict[str, Any] = {}
        table = _table_for_type(conn, anchor_type)
        if table:
            cols = [c for c, _ in _column_info(conn, table)]
            if cols:
                quoted = ", ".join(_quote(c) for c in cols)
                row_data = conn.execute(
                    f"SELECT {quoted} FROM {_quote(table)} WHERE ocel_id = ? LIMIT 1",
                    (anchor_id,),
                ).fetchone()
                if row_data:
                    attrs = dict(zip(cols, row_data))
        ctx["object"] = {"ocel_id": anchor_id, "ocel_type": anchor_type, "attributes": attrs}

        # Up to 8 events touching this object.
        ctx["events"] = [
            {"ocel_id": eid, "ocel_type": etype, "qualifier": qual}
            for eid, etype, qual in conn.execute(
                "SELECT e.ocel_id, e.ocel_type, eo.ocel_qualifier "
                "FROM event e JOIN event_object eo ON eo.ocel_event_id = e.ocel_id "
                "WHERE eo.ocel_object_id = ? LIMIT 8",
                (anchor_id,),
            ).fetchall()
        ]

    # Candidate id lists for dangling-relation issues.
    if issue_key == "dangling_o2o_relations":
        ctx["candidate_object_ids"] = [
            r[0] for r in conn.execute(
                "SELECT ocel_id FROM object WHERE ocel_id IS NOT NULL LIMIT 200"
            ).fetchall()
        ]
    elif issue_key == "dangling_e2o_relations":
        side = row.get("missing_side")
        if side == "object":
            ctx["candidate_object_ids"] = [
                r[0] for r in conn.execute(
                    "SELECT ocel_id FROM object WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]
        else:
            ctx["candidate_event_ids"] = [
                r[0] for r in conn.execute(
                    "SELECT ocel_id FROM event WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]

    return ctx


_TASKS = {
    "missing_object_types": (
        "Infer the missing `ocel_type`. Pick exactly one value from "
        "`candidate_types`, or null. Return JSON: "
        '{"inferred_type": str|null, "rationale": str, "confidence": number}.'
    ),
    "missing_attributes": (
        "Suggest a value for the missing attribute named in `violation.attribute`. "
        "Use peer values and event activities as evidence. Return JSON: "
        '{"inferred_value": any|null, "rationale": str, "confidence": number}.'
    ),
    "incorrect_datatypes": (
        "Coerce `violation.actual_value` to the SQL type in `violation.expected_type` "
        "if a semantically meaningful coercion exists, else null. Return JSON: "
        '{"coerced_value": any|null, "rationale": str, "confidence": number}.'
    ),
    "dangling_o2o_relations": (
        "Pick the most likely missing referent from `candidate_object_ids`, or null. "
        "Return JSON: "
        '{"inferred_referent": str|null, "rationale": str, "confidence": number}.'
    ),
    "dangling_e2o_relations": (
        "Pick the most likely missing referent from the candidate id list "
        "(`candidate_object_ids` if missing_side is 'object', else "
        "`candidate_event_ids`), or null. Return JSON: "
        '{"inferred_referent": str|null, "rationale": str, "confidence": number}.'
    ),
}


def _to_action(issue_key: str, row: dict, payload: dict) -> dict:
    """Translate the LLM JSON payload into an action dict, or a noop."""
    confidence = float(payload.get("confidence", 0.0))
    rationale = str(payload.get("rationale", ""))

    def noop(reason: str) -> dict:
        return {
            "kind": "noop", "target_table": "", "target_pk": {}, "column": None,
            "old_value": None, "new_value": None,
            "rationale": reason, "confidence": confidence, "issue_key": issue_key,
        }

    if confidence < MIN_CONFIDENCE:
        return noop(f"Confidence {confidence:.2f} below threshold {MIN_CONFIDENCE:.2f}.")

    if issue_key == "missing_object_types":
        new = payload.get("inferred_type")
        if not new:
            return noop("LLM declined to infer an object type.")
        return {
            "kind": "update", "target_table": "object",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
        }

    if issue_key == "missing_attributes":
        new = payload.get("inferred_value")
        if new is None:
            return noop("LLM declined to infer a value.")
        return {
            "kind": "update", "target_table": f"object_{row['object_type']}",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": row["attribute"],
            "old_value": row.get("actual_value"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
        }

    if issue_key == "incorrect_datatypes":
        new = payload.get("coerced_value")
        if new is None:
            return noop("LLM declined to coerce the value.")
        return {
            "kind": "update", "target_table": f"object_{row['object_type']}",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": row["attribute"],
            "old_value": row.get("actual_value"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
        }

    if issue_key == "dangling_o2o_relations":
        new = payload.get("inferred_referent")
        if not new:
            return noop("LLM declined to infer a referent.")
        side = row.get("missing_side")
        if side == "source":
            return {
                "kind": "update", "target_table": "object_object",
                "target_pk": {
                    "ocel_target_id": row["ocel_target_id"],
                    "ocel_qualifier": row["ocel_qualifier"],
                },
                "column": "ocel_source_id",
                "old_value": row.get("ocel_source_id"), "new_value": new,
                "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            }
        if side == "target":
            return {
                "kind": "update", "target_table": "object_object",
                "target_pk": {
                    "ocel_source_id": row["ocel_source_id"],
                    "ocel_qualifier": row["ocel_qualifier"],
                },
                "column": "ocel_target_id",
                "old_value": row.get("ocel_target_id"), "new_value": new,
                "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            }
        return noop("Both ends of the O2O relation missing; cannot patch.")

    if issue_key == "dangling_e2o_relations":
        new = payload.get("inferred_referent")
        if not new:
            return noop("LLM declined to infer a referent.")
        side = row.get("missing_side")
        if side == "event":
            return {
                "kind": "update", "target_table": "event_object",
                "target_pk": {
                    "ocel_object_id": row["ocel_object_id"],
                    "ocel_qualifier": row["ocel_qualifier"],
                },
                "column": "ocel_event_id",
                "old_value": row.get("ocel_event_id"), "new_value": new,
                "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            }
        if side == "object":
            return {
                "kind": "update", "target_table": "event_object",
                "target_pk": {
                    "ocel_event_id": row["ocel_event_id"],
                    "ocel_qualifier": row["ocel_qualifier"],
                },
                "column": "ocel_object_id",
                "old_value": row.get("ocel_object_id"), "new_value": new,
                "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            }
        return noop("Both ends of the E2O relation missing; cannot patch.")

    return noop(f"No repair mapping for issue_key {issue_key!r}.")


def suggest_repair(issue_key: str, row: dict, sqlite_path: str) -> dict:
    """Ask the LLM how to repair `row`. Returns an action dict (kind='noop' if unsure)."""
    if issue_key not in _TASKS:
        return {
            "kind": "noop", "target_table": "", "target_pk": {}, "column": None,
            "old_value": None, "new_value": None,
            "rationale": f"No LLM task defined for {issue_key!r}.",
            "confidence": 0.0, "issue_key": issue_key,
        }
    with _connect(sqlite_path) as conn:
        ctx = _build_context(conn, issue_key, row)
    user_prompt = (
        _TASKS[issue_key]
        + "\n\nContext:\n```json\n"
        + json.dumps(ctx, default=str, indent=2)
        + "\n```"
    )
    payload = _call_ollama(user_prompt)
    return _to_action(issue_key, row, payload)


def apply_repair(sqlite_path: str, action: dict, *, dry_run: bool = True) -> str:
    """Execute (or dry-run) an action dict. Validates table/column names against the schema."""
    if action["kind"] == "noop":
        raise ValueError(f"Refusing to apply noop: {action['rationale']}")
    if action["kind"] != "update":
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

        where = " AND ".join(f"{_quote(c)} = ?" for c in action["target_pk"])
        sql = f'UPDATE {_quote(table)} SET {_quote(col)} = ? WHERE {where}'
        params = (action["new_value"], *action["target_pk"].values())

        rendered = f"{sql}\n  with params = {params!r}"
        if dry_run:
            return f"-- DRY RUN (no changes written)\n{rendered}"
        with conn:
            cur = conn.execute(sql, params)
            n = cur.rowcount
        return f"Committed: {n} row(s) affected.\n{rendered}"
