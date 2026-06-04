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
    "You are a domain expert for object-centric event data in the OCEL2.0 format. "
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


def _column_affinity(conn: sqlite3.Connection, table: str, column: str) -> str:
    """Look up the declared SQLite affinity for `table.column`, or '' if unknown."""
    for _, name, dtype, *_ in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name == column:
            return (dtype or "").upper()
    return ""


def _coerce_for_affinity(raw: Any, affinity: str) -> Any:
    """Coerce `raw` to a value compatible with `affinity` (a SQLite column type
    string like 'INTEGER', 'REAL', 'TEXT'). Mirrors the buckets used by
    `_value_matches_type` so the apply path agrees with the detector.

    Raises ValueError when no meaning-preserving coercion exists.
    """
    if raw is None:
        return None
    t = (affinity or "").upper()

    # No declared affinity -> accept the value as-is (BLOB-affinity column).
    if not t:
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
        # Allow simple stringifications -- numbers, bools.
        if isinstance(raw, (int, float, bool)):
            return str(raw)
        raise ValueError(f"override {raw!r} is not compatible with {affinity} affinity")

    if "BLOB" in t:
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if isinstance(raw, str):
            return raw.encode("utf-8")
        raise ValueError(f"override {raw!r} is not compatible with BLOB affinity")

    # Unknown affinity -> pass through.
    return raw


def _value_for_column(conn: sqlite3.Connection, table: str, column: str, raw: Any) -> Any:
    """Return `raw` coerced to a value compatible with `table.column`'s affinity,
    or raise ValueError with a clear message. Used by apply_repair for both
    LLM- and user-supplied values."""
    affinity = _column_affinity(conn, table, column)
    return _coerce_for_affinity(raw, affinity)


def _build_context(conn: sqlite3.Connection, issue_key: str, row: dict) -> dict:
    """Assemble the JSON context block sent in the user prompt."""
    ctx: dict[str, Any] = {"issue_key": issue_key, "violation": dict(row)}
    ctx["candidate_types"] = [t for t, _ in _object_type_tables(conn)]

    # Pick the anchor object/event for this issue.  For dangling_o2o the anchor
    # is the *known* side -- if the source is missing, the target is what we
    # know about, and vice versa.
    if issue_key == "dangling_o2o_relations":
        if row.get("missing_side") == "source":
            anchor_id = row.get("ocel_target_id")
            anchor_type = row.get("target_type")
        else:
            anchor_id = row.get("ocel_source_id")
            anchor_type = row.get("source_type")
    else:
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

        # For attribute-level issues: add peer objects of the same type so the
        # LLM can reason about typical values.  Without these, the model has
        # nothing to compare against and will decline.
        if issue_key in ("missing_attributes", "incorrect_datatypes") and table and cols:
            quoted = ", ".join(_quote(c) for c in cols)
            peers = conn.execute(
                f"SELECT {quoted} FROM {_quote(table)} "
                f"WHERE ocel_id != ? LIMIT 5",
                (anchor_id,),
            ).fetchall()
            ctx["peer_objects"] = [dict(zip(cols, p)) for p in peers]

    # Candidate id lists for dangling-relation issues.  We attach `ocel_type`
    # alongside each id so the LLM can filter candidates by plausibility
    # instead of staring at 200 bare strings.
    if issue_key == "dangling_o2o_relations":
        ctx["candidate_objects"] = [
            {"ocel_id": r[0], "ocel_type": r[1]}
            for r in conn.execute(
                "SELECT ocel_id, ocel_type FROM object "
                "WHERE ocel_id IS NOT NULL LIMIT 200"
            ).fetchall()
        ]
    elif issue_key == "dangling_e2o_relations":
        side = row.get("missing_side")
        if side == "object":
            ctx["candidate_objects"] = [
                {"ocel_id": r[0], "ocel_type": r[1]}
                for r in conn.execute(
                    "SELECT ocel_id, ocel_type FROM object "
                    "WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]
        else:
            ctx["candidate_events"] = [
                {"ocel_id": r[0], "ocel_type": r[1]}
                for r in conn.execute(
                    "SELECT ocel_id, ocel_type FROM event "
                    "WHERE ocel_id IS NOT NULL LIMIT 200"
                ).fetchall()
            ]
    # For duplicate issues: fetch full attribute rows for all duplicated IDs so
    # the LLM can compare them and decide which to keep / how to merge.
    elif issue_key == "duplicate_object_ids":
        dup_ids = row.get("ocel_ids", "")
        ids = [i.strip() for i in str(dup_ids).split(",") if i.strip()]
        if ids:
            placeholders = ", ".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT ocel_id, ocel_type FROM object WHERE ocel_id IN ({placeholders})",
                ids,
            ).fetchall()
            ctx["duplicate_rows"] = [{"ocel_id": r[0], "ocel_type": r[1]} for r in rows]
    elif issue_key == "duplicate_object_attributes":
        dup_vals = row.get("attribute_values", "")
        ctx["duplicate_attribute_values"] = [
            v.strip() for v in str(dup_vals).split(",") if v.strip()
        ]
        # Also provide the full attribute row for the anchor object.
        anchor_type2 = row.get("object_type")
        table2 = _table_for_type(conn, anchor_type2)
        if table2:
            cols2 = [c for c, _ in _column_info(conn, table2)]
            if cols2 and anchor_id:
                quoted2 = ", ".join(_quote(c) for c in cols2)
                r2 = conn.execute(
                    f"SELECT {quoted2} FROM {_quote(table2)} WHERE ocel_id = ? LIMIT 1",
                    (anchor_id,),
                ).fetchone()
                if r2:
                    ctx["object_attributes"] = dict(zip(cols2, r2))

    return ctx


_TASKS = {
    "missing_object_types": (
        "An object row in the `object` table has a NULL or empty `ocel_type`. "
        "Infer the most likely type for this object.\n\n"
        "Reasoning recipe:\n"
        "  1. The object's id is in `violation.ocel_id`. Its existing attributes "
        "(if any were stored in a per-type table under that id) are in "
        "`object.attributes`.\n"
        "  2. `events` lists up to 8 events touching this object together with "
        "the qualifier under which the event references it. Activity names and "
        "qualifiers (e.g. 'place_order' + 'customer' strongly imply Customer) "
        "are the strongest signal.\n"
        "  3. Pick exactly one value from `candidate_types` -- a verbatim "
        "string, never a fabrication. If multiple candidates fit, pick the one "
        "whose name best matches the activities/qualifiers seen.\n"
        "  4. Return null only when no candidate is a plausible fit, and put "
        "the specific reason in `rationale` (e.g. 'no events touch this "
        "object and attribute set is empty -- no signal to disambiguate').\n\n"
        "Return JSON: "
        '{"inferred_type": str|null, "rationale": str, "confidence": number}.'
    ),
    "missing_attributes": (
        "An object row has a missing (NULL or empty) value for the attribute "
        "named in `violation.attribute` (or `violation.attribute_name`). "
        "Infer the most likely value.\n\n"
        "Reasoning recipe:\n"
        "  1. `peer_objects` shows up to 5 other objects of the same type with "
        "their full attribute rows -- use these to learn the typical shape, "
        "format, and value distribution of the missing attribute.\n"
        "  2. The anchor object's other (non-missing) attributes are in "
        "`object.attributes`. They often correlate with the missing one "
        "(e.g. country implies currency, product_id implies category).\n"
        "  3. `events` lists activities touching this object; activity names "
        "and qualifiers can pin down the value (e.g. activity 'pay_in_eur' "
        "implies currency='EUR').\n"
        "  4. Match the data type, units, and formatting of the peer values "
        "exactly. Do not invent ids, codes, or names that are not supported "
        "by the evidence.\n"
        "  5. Return null only when peers and events together give no signal "
        "for this attribute, and put the specific reason in `rationale` "
        "(e.g. 'all peer values are distinct free-text and no event activity "
        "narrows them').\n\n"
        "Return JSON: "
        '{"inferred_value": any|null, "rationale": str, "confidence": number}.'
    ),
    "incorrect_datatypes": (
        "Coerce `violation.actual_value` so that it matches the SQL affinity in "
        "`violation.expected_type`.\n\n"
        "IMPORTANT: `violation.actual_value` is a Python `repr()` of the original "
        "cell. A wrapping pair of single quotes means the cell currently holds a "
        "string -- strip those quotes before reasoning about the underlying value. "
        "`violation.actual_python_type` tells you the current Python type.\n\n"
        "SQL affinity -> target Python type:\n"
        "  INTEGER                         -> int\n"
        "  REAL / FLOA / DOUB / NUMERIC /  -> float\n"
        "    DECIMAL\n"
        "  TEXT / CHAR / CLOB              -> str\n"
        "  BLOB                            -> bytes\n\n"
        "Worked examples (actual_value, expected_type) -> coerced_value:\n"
        "  ('42', INTEGER)         -> 42\n"
        "  ('3.14', REAL)          -> 3.14\n"
        "  ('true', INTEGER)       -> 1\n"
        "  ('false', INTEGER)      -> 0\n"
        "  (42, TEXT)              -> \"42\"\n"
        "  ('2024-01-01', TEXT)    -> \"2024-01-01\"\n"
        "  ('banana', INTEGER)     -> null   # no meaning-preserving coercion\n\n"
        "Prefer a coercion whenever one preserves the value's meaning, even at "
        "modest confidence. Use `peer_objects` to confirm what correctly-typed "
        "values look like for this attribute. Return null ONLY when no coercion "
        "preserves the meaning -- and in that case, set `rationale` to the "
        "specific reason coercion is impossible.\n\n"
        "Return JSON: "
        '{"coerced_value": any|null, "rationale": str, "confidence": number}.'
    ),
    "dangling_o2o_relations": (
        "An object_object relation references an object that does not exist in "
        "the `object` table. Pick the most likely intended referent from "
        "`candidate_objects` (each entry has `ocel_id` and `ocel_type`), or null "
        "if no candidate is a plausible match.\n\n"
        "Reasoning recipe:\n"
        "  1. `violation.missing_side` tells you which end is missing "
        "('source' or 'target').\n"
        "  2. The known end is described in `object` (its ocel_id, type, and "
        "attributes). Use its type and attributes plus `violation.ocel_qualifier` "
        "to narrow candidates -- the qualifier names the relationship and often "
        "implies a plausible target type (e.g. 'belongs_to', 'part_of', 'parent').\n"
        "  3. Filter `candidate_objects` to those whose `ocel_type` is plausible "
        "for that qualifier and the known end's type. Among the survivors, "
        "prefer ids that share a naming prefix or convention with the known end.\n"
        "  4. Return the single best `ocel_id` -- a verbatim value from "
        "`candidate_objects`, never a fabrication. Return null only when no "
        "candidate is plausible, and put the specific reason in `rationale`.\n\n"
        "Return JSON: "
        '{"inferred_referent": str|null, "rationale": str, "confidence": number}.'
    ),
    "dangling_e2o_relations": (
        "An event_object relation references an event or object that does not "
        "exist. Pick the most likely intended referent from the candidate list "
        "-- `candidate_objects` if `violation.missing_side` is 'object', "
        "otherwise `candidate_events` -- or null. Use the known end's type, "
        "attributes (in `object`), and `violation.ocel_qualifier` to narrow "
        "candidates. Return a verbatim id from the list; never invent one. "
        "Return null only when no candidate is plausible, and put the specific "
        "reason in `rationale`.\n\n"
        "Return JSON: "
        '{"inferred_referent": str|null, "rationale": str, "confidence": number}.'
    ),
    "duplicate_object_ids": (
        "Multiple rows in the `object` table share the same `ocel_id`. The "
        "duplicated rows are listed in `duplicate_rows` (each entry has "
        "`ocel_id` and `ocel_type`). Decide which single row is canonical and "
        "which should be deleted.\n\n"
        "Reasoning recipe:\n"
        "  1. Prefer rows with a non-null, non-empty `ocel_type`. A row with "
        "an explicit type is almost always the canonical one.\n"
        "  2. If multiple rows have a type, prefer the one whose type is "
        "consistent with the activities in `events` (events touching this "
        "ocel_id are listed under `events`).\n"
        "  3. The canonical id itself is the shared value -- the choice is "
        "really about which TYPE to keep. Set `canonical_id` to that ocel_id, "
        "and list the duplicate rows that should be removed in "
        "`ids_to_delete` using their (ocel_id, ocel_type) tuple style is fine "
        "but a list of ocel_ids is what gets used by the suggested DELETE.\n"
        "  4. Put the specific reason for your pick (and for any rejected "
        "alternatives) in `rationale`. Never invent an ocel_id that is not "
        "in `duplicate_rows`.\n\n"
        "Return JSON: "
        '{"canonical_id": str, "ids_to_delete": [str], "rationale": str, "confidence": number}.'
    ),
    "duplicate_object_attributes": (
        "Two or more objects of the same type share an identical attribute "
        "fingerprint but have different `ocel_id`s. The duplicated values "
        "are listed in `duplicate_attribute_values`; the anchor object's "
        "full attribute row is in `object_attributes`.\n\n"
        "Reasoning recipe:\n"
        "  1. Look at `violation.attribute_name` (or `violation.attribute`) "
        "to know which column has the conflicting value.\n"
        "  2. Compare the candidate values in `duplicate_attribute_values`. "
        "Prefer the one that matches the formatting/casing/units of the "
        "anchor object's other attributes in `object_attributes`.\n"
        "  3. Use `events` (activities touching the anchor object) as a "
        "tiebreaker -- e.g. an activity name often implies the correct "
        "value (currency, country, category).\n"
        "  4. Return null only when the candidate values are equally "
        "plausible and no other attribute or event narrows them; put the "
        "specific reason in `rationale`. Never invent a value that is not "
        "in `duplicate_attribute_values`.\n\n"
        "Return JSON: "
        '{"canonical_value": any, "rationale": str, "confidence": number}.'
    ),
}


def _suppressed_target(issue_key: str, row: dict) -> dict:
    """Return {target_table, target_pk, column, old_value} for a noop that was
    suppressed by the confidence gate, so the override UI can still route it.
    Returns empty fields when the issue type has no clean override target
    (currently `duplicate_object_ids`)."""
    empty = {"target_table": "", "target_pk": {}, "column": None, "old_value": None}

    if issue_key == "missing_object_types":
        return {
            "target_table": "object",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"),
        }

    if issue_key in ("missing_attributes", "incorrect_datatypes"):
        attr_col = row.get("attribute_name") or row.get("attribute")
        object_type = row.get("object_type")
        if not attr_col or not object_type:
            return empty
        return {
            "target_table": f"object_{object_type}",
            "target_pk": {"ocel_id": row.get("ocel_id")},
            "column": attr_col,
            "old_value": row.get("actual_value"),
        }

    if issue_key == "dangling_o2o_relations":
        side = row.get("missing_side")
        if side == "source":
            return {
                "target_table": "object_object",
                "target_pk": {
                    "ocel_target_id": row.get("ocel_target_id"),
                    "ocel_qualifier": row.get("ocel_qualifier"),
                },
                "column": "ocel_source_id",
                "old_value": row.get("ocel_source_id"),
            }
        if side == "target":
            return {
                "target_table": "object_object",
                "target_pk": {
                    "ocel_source_id": row.get("ocel_source_id"),
                    "ocel_qualifier": row.get("ocel_qualifier"),
                },
                "column": "ocel_target_id",
                "old_value": row.get("ocel_target_id"),
            }
        return empty

    if issue_key == "dangling_e2o_relations":
        side = row.get("missing_side")
        if side == "event":
            return {
                "target_table": "event_object",
                "target_pk": {
                    "ocel_object_id": row.get("ocel_object_id"),
                    "ocel_qualifier": row.get("ocel_qualifier"),
                },
                "column": "ocel_event_id",
                "old_value": row.get("ocel_event_id"),
            }
        if side == "object":
            return {
                "target_table": "event_object",
                "target_pk": {
                    "ocel_event_id": row.get("ocel_event_id"),
                    "ocel_qualifier": row.get("ocel_qualifier"),
                },
                "column": "ocel_object_id",
                "old_value": row.get("ocel_object_id"),
            }
        return empty

    if issue_key == "duplicate_object_attributes":
        attr_col = row.get("attribute_name") or row.get("attribute")
        anchor_id = row.get("ocel_id") or row.get("ocel_object_id")
        object_type = row.get("object_type")
        if not attr_col or not anchor_id or not object_type:
            return empty
        return {
            "target_table": f"object_{object_type}",
            "target_pk": {"ocel_id": anchor_id},
            "column": attr_col,
            "old_value": row.get("attribute_values"),
        }

    # duplicate_object_ids has no single-column override target.
    return empty


def _to_action(issue_key: str, row: dict, payload: dict) -> dict:
    """Translate the LLM JSON payload into an action dict, or a noop."""
    confidence = float(payload.get("confidence", 0.0))
    rationale = str(payload.get("rationale", ""))

    def noop(reason: str, proposed: Any = None) -> dict:
        return {
            "kind": "noop", "target_table": "", "target_pk": {}, "column": None,
            "old_value": None, "new_value": None,
            "rationale": reason, "confidence": confidence, "issue_key": issue_key,
            "proposed_value": proposed,
        }

    # Pull the model's suggested value (whichever payload key carries it for
    # this issue) up front -- both the confidence-gate noop and the per-issue
    # decline branches surface it on the action so the override UI can default
    # to it without re-parsing the rationale string.
    proposed_keys = (
        "coerced_value", "inferred_value", "inferred_type",
        "inferred_referent", "canonical_id", "canonical_value",
    )
    proposed_value = next(
        (payload[k] for k in proposed_keys if payload.get(k) is not None),
        None,
    )

    if confidence < MIN_CONFIDENCE:
        # Surface the model's would-be answer + its own rationale so the user can
        # see why the suggestion was suppressed (vs a flat threshold message).
        bits = [f"Confidence {confidence:.2f} below threshold {MIN_CONFIDENCE:.2f}."]
        if proposed_value is not None:
            bits.append(f"Would have proposed: {proposed_value!r}.")
        if rationale.strip():
            bits.append(f"Rationale: {rationale.strip()}")
        # Reconstruct the noop so the dashboard can route an override -- we
        # need target_table + column populated where they're known, otherwise
        # the override field stays hidden.
        suppressed = _suppressed_target(issue_key, row)
        return {
            "kind": "noop",
            "target_table": suppressed["target_table"],
            "target_pk": suppressed["target_pk"],
            "column": suppressed["column"],
            "old_value": suppressed["old_value"],
            "new_value": None,
            "rationale": " ".join(bits),
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": proposed_value,
        }

    # Per-issue declines re-use _suppressed_target to attach a routable target
    # (when there is one) so the override UI can still patch the row even when
    # the LLM said null. `proposed_value` stays None here because the LLM
    # didn't actually propose anything.
    def routable_decline(reason: str) -> dict:
        target = _suppressed_target(issue_key, row)
        return {
            "kind": "noop",
            "target_table": target["target_table"],
            "target_pk": target["target_pk"],
            "column": target["column"],
            "old_value": target["old_value"],
            "new_value": None,
            "rationale": reason,
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": None,
        }

    if issue_key == "missing_object_types":
        new = payload.get("inferred_type")
        if not new:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM declined to infer an object type: {reason}")
        return {
            "kind": "update", "target_table": "object",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": "ocel_type",
            "old_value": row.get("ocel_type"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            "proposed_value": new,
        }

    if issue_key == "missing_attributes":
        new = payload.get("inferred_value")
        if new is None:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM declined to infer a value: {reason}")
        # The detector stores the attribute name in "attribute_name", not "attribute".
        attr_col = row.get("attribute_name") or row.get("attribute")
        if not attr_col:
            return noop("Could not determine attribute column name from violation row.")
        return {
            "kind": "update", "target_table": f"object_{row['object_type']}",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": row["attribute"],
            "old_value": row.get("actual_value"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            "proposed_value": new,
        }

    if issue_key == "incorrect_datatypes":
        new = payload.get("coerced_value")
        if new is None:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM declined to coerce: {reason}")
        # Same fix: attribute column name may be "attribute_name".
        attr_col = row.get("attribute_name") or row.get("attribute")
        if not attr_col:
            return noop("Could not determine attribute column name from violation row.")
        return {
            "kind": "update", "target_table": f"object_{row['object_type']}",
            "target_pk": {"ocel_id": row["ocel_id"]},
            "column": row["attribute"],
            "old_value": row.get("actual_value"), "new_value": new,
            "rationale": rationale, "confidence": confidence, "issue_key": issue_key,
            "proposed_value": new,
        }

    if issue_key == "dangling_o2o_relations":
        new = payload.get("inferred_referent")
        if not new:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM declined to infer a referent: {reason}")
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
                "proposed_value": new,
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
                "proposed_value": new,
            }
        return noop("Both ends of the O2O relation missing; cannot patch.")

    if issue_key == "dangling_e2o_relations":
        new = payload.get("inferred_referent")
        if not new:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM declined to infer a referent: {reason}")
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
                "proposed_value": new,
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
                "proposed_value": new,
            }
        return noop("Both ends of the E2O relation missing; cannot patch.")

    if issue_key == "duplicate_object_ids":
        canonical = payload.get("canonical_id")
        ids_to_delete = payload.get("ids_to_delete") or []
        if not canonical:
            reason = rationale.strip() or "no reason provided"
            return noop(f"LLM could not determine a canonical object ID: {reason}")
        # We surface this as a special "deduplicate" action kind.
        # apply_repair handles "update" only, so we encode the intent as a
        # delete of the non-canonical rows via a synthetic action.  For now
        # we return an informational noop with the recommendation embedded in
        # the rationale so the user can act on it manually via the dry-run SQL.
        ids_str = ", ".join(repr(i) for i in ids_to_delete)
        return {
            "kind": "noop",
            "target_table": "object",
            "target_pk": {},
            "column": None,
            "old_value": None,
            "new_value": None,
            "rationale": (
                f"{rationale}  |  Canonical ID: {canonical!r}.  "
                f"Suggested DELETE: DELETE FROM object WHERE ocel_id IN ({ids_str})."
            ),
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": canonical,
        }

    if issue_key == "duplicate_object_attributes":
        canonical_val = payload.get("canonical_value")
        if canonical_val is None:
            reason = rationale.strip() or "no reason provided"
            return routable_decline(f"LLM could not determine a canonical attribute value: {reason}")
        attr_col = row.get("attribute_name") or row.get("attribute")
        if not attr_col:
            return noop("Could not determine attribute column name from violation row.")
        anchor_id = row.get("ocel_id") or row.get("ocel_object_id")
        object_type = row.get("object_type")
        if not anchor_id or not object_type:
            return noop("Missing ocel_id or object_type in violation row.")
        return {
            "kind": "update",
            "target_table": f"object_{object_type}",
            "target_pk": {"ocel_id": anchor_id},
            "column": attr_col,
            "old_value": row.get("attribute_values"),
            "new_value": canonical_val,
            "rationale": rationale,
            "confidence": confidence,
            "issue_key": issue_key,
            "proposed_value": canonical_val,
        }

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
        # Override-on-noop: require a routable target.
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

        # Resolve the value to write. Overrides go through a type-affinity
        # check so we don't silently re-introduce an `incorrect_datatypes`
        # violation via the fix path.
        if has_override:
            new_value = _value_for_column(conn, table, col, override_value)
            llm_rationale = action.get("rationale", "") or "<no LLM rationale>"
            effective_rationale = (
                f"USER OVERRIDE: {override_value!r}. LLM said: {llm_rationale}"
            )
        else:
            new_value = action["new_value"]
            effective_rationale = action.get("rationale", "")

        where = " AND ".join(f"{_quote(c)} = ?" for c in action["target_pk"])
        sql = f'UPDATE {_quote(table)} SET {_quote(col)} = ? WHERE {where}'
        params = (new_value, *action["target_pk"].values())

        header = ""
        if has_override:
            header = f"-- {effective_rationale}\n"
        rendered = f"{header}{sql}\n  with params = {params!r}"
        if dry_run:
            return f"-- DRY RUN (no changes written)\n{rendered}"
        with conn:
            cur = conn.execute(sql, params)
            n = cur.rowcount
        return f"Committed: {n} row(s) affected.\n{rendered}"
