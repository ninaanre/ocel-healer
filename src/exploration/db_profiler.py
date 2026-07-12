# src/exploration/db_profiler.py

"""Deterministic profiling of an OCEL 2.0 SQLite database.

Gathers verifiable facts — schemas, counts, value samples, null rates, ID
patterns, qualifier/type mappings — for the exploration agent to interpret.
No LLM involved here: everything in the profile is ground truth, so the
agent's claims can later be validated against it.

The database is opened read-only; profiling can never modify the log.
"""

import hashlib
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
    return [r[1] for r in rows]


def sample_values(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    limit: int = 15,
) -> list[Any]:
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT {quote(column)}
            FROM {quote(table)}
            WHERE {quote(column)} IS NOT NULL
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def null_rate(conn: sqlite3.Connection, table: str, column: str) -> float | None:
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
        if total == 0:
            return None
        nulls = conn.execute(
            f"SELECT COUNT(*) FROM {quote(table)} WHERE {quote(column)} IS NULL"
        ).fetchone()[0]
        return round(nulls / total, 3)
    except sqlite3.Error:
        return None


_HUMAN_NAME = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)+$")
_PREFIXED_ID = re.compile(r"^[a-zA-Z]+[-_:]\d+")
_NUMERIC_ID = re.compile(r"^\d+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# MVP heuristic for recognisable consumer product names.
_PRODUCT_KEYWORDS = (
    "ipad", "iphone", "kindle", "echo", "macbook", "galaxy",
    "watch", "airpods", "camera", "laptop", "tablet", "fire",
)


def classify_id(value: str) -> str:
    """Classify one identifier into a coarse shape bucket."""
    v = value.strip()
    lower = v.lower()
    if _EMAIL.match(v):
        return "email_like"
    if _PREFIXED_ID.match(v):
        return "prefixed_id_like"
    if _NUMERIC_ID.match(v):
        return "numeric_id_like"
    if any(k in lower for k in _PRODUCT_KEYWORDS):
        return "product_name_like"
    if _HUMAN_NAME.match(v):
        return "human_name_like"
    if " " in v:
        return "natural_language_like"
    return "opaque_token_like"


# Buckets whose values carry a real-world entity name usable for lookups.
NAME_LIKE_BUCKETS = {"human_name_like", "product_name_like", "natural_language_like"}

# Fraction of name-like ids above which we assert "the id is an entity name".
NAME_LIKE_THRESHOLD = 0.5

# A string column whose sample has at most this many distinct values is treated
# as categorical, and the sample as its observed vocabulary.
VOCAB_MAX = 10


def known_values_from_samples(samples: list[Any]) -> list[str] | None:
    """Observed value vocabulary for low-cardinality *string* columns, or None.

    Two deliberate restrictions, both learned from dirty data:
      - strings only — numeric columns with few distinct values are usually
        continuous quantities thinned out by missing values, not categories;
      - callers must treat the list as "observed", never "complete": the very
        corruption being repaired may have wiped a whole category out of the
        sample (e.g. all 'Sales' employees had their role nulled).
    """
    if not (0 < len(samples) <= VOCAB_MAX):
        return None
    if not all(isinstance(v, str) for v in samples):
        return None
    return samples


def detect_id_patterns(values: list[Any]) -> dict[str, Any]:
    """Bucket identifiers by shape and estimate how name-like they are.

    Returns {"buckets": {bucket: {"count": n, "examples": [...]}},
             "name_like_fraction": float} — the fraction of sampled ids whose
    value looks like a real-world entity name (usable for domain lookups).
    """
    buckets: dict[str, dict[str, Any]] = {}
    total = 0
    name_like = 0
    for raw in values:
        if raw is None:
            continue
        total += 1
        bucket = classify_id(str(raw))
        entry = buckets.setdefault(bucket, {"count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < 8:
            entry["examples"].append(str(raw))
        if bucket in NAME_LIKE_BUCKETS:
            name_like += 1
    return {
        "buckets": buckets,
        "name_like_fraction": round(name_like / total, 3) if total else 0.0,
    }


_DIGIT_RUN = re.compile(r"\d+")


def id_template(value: str) -> str:
    """Collapse digit runs into '#' so ids with a shared shape align:
    'o-990001' -> 'o-######'. Non-digit text is kept verbatim."""
    return _DIGIT_RUN.sub(lambda m: "#" * len(m.group()), value.strip())


def id_templates(values: list[Any], top: int = 3) -> list[dict[str, Any]]:
    """Most frequent id templates with their share of the sample. A dominant
    template (share near 1.0) means the type has a stable technical id format;
    scattered templates mean free-form ids (e.g. entity names)."""
    counts = Counter(id_template(str(v)) for v in values if v is not None)
    total = sum(counts.values())
    return [
        {"template": t, "count": n, "share": round(n / total, 3)}
        for t, n in counts.most_common(top)
    ]


def per_type_id_patterns(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """ID pattern analysis per object type — far more useful than a global mix
    because ID semantics differ by type (products carry names, orders carry codes)."""
    out: dict[str, dict[str, Any]] = {}
    types = conn.execute(
        "SELECT DISTINCT ocel_type FROM object WHERE ocel_type IS NOT NULL AND ocel_type != ''"
    ).fetchall()
    for (t,) in types:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT ocel_id FROM object WHERE ocel_type = ? LIMIT 300",
                (t,),
            ).fetchall()
        ]
        pattern = detect_id_patterns(ids)
        pattern["templates"] = id_templates(ids)
        pattern["id_is_entity_name"] = (
            pattern["name_like_fraction"] >= NAME_LIKE_THRESHOLD
        )
        out[t] = pattern
    return out


def _grouped_counts(
    conn: sqlite3.Connection, sql: str
) -> dict[str, dict[str, int]]:
    """Run a (key, subkey, count) query into {key: {subkey: count}}."""
    out: dict[str, dict[str, int]] = {}
    try:
        for key, subkey, count in conn.execute(sql).fetchall():
            if key is None or subkey is None:
                continue
            out.setdefault(key, {})[subkey] = count
    except sqlite3.Error:
        pass
    return out


def qualifier_context(conn: sqlite3.Connection, tables: list[str]) -> dict[str, Any]:
    """Deterministic qualifier semantics evidence: which object types and event
    types each qualifier connects. Dangling references simply drop out of the joins."""
    ctx: dict[str, Any] = {}
    if "event_object" in tables:
        ctx["e2o_qualifier_object_types"] = _grouped_counts(conn, """
            SELECT eo.ocel_qualifier, o.ocel_type, COUNT(*)
            FROM event_object eo JOIN object o ON o.ocel_id = eo.ocel_object_id
            GROUP BY 1, 2
        """)
        ctx["e2o_qualifier_event_types"] = _grouped_counts(conn, """
            SELECT eo.ocel_qualifier, e.ocel_type, COUNT(*)
            FROM event_object eo JOIN event e ON e.ocel_id = eo.ocel_event_id
            GROUP BY 1, 2
        """)
    if "object_object" in tables:
        ctx["o2o_qualifier_type_pairs"] = _grouped_counts(conn, """
            SELECT oo.ocel_qualifier, s.ocel_type || ' -> ' || t.ocel_type, COUNT(*)
            FROM object_object oo
            JOIN object s ON s.ocel_id = oo.ocel_source_id
            JOIN object t ON t.ocel_id = oo.ocel_target_id
            GROUP BY 1, 2
        """)
    return ctx


# A qualifier is considered semantically "owned" by one type when that type
# receives at least this share of its links; the rest are outliers.
DOMINANT_SHARE = 0.8


def qualifier_outliers(
    conn: sqlite3.Connection, e2o_qualifier_object_types: dict[str, dict[str, int]]
) -> dict[str, Any]:
    """Qualifiers whose links overwhelmingly point to one object type, plus the
    minority objects that break the pattern. Those objects either carry a wrong
    ocel_type (corruption) or the qualifier is legitimately polysemous — the
    exploration guide records the fact; judging it is the detectors' job."""
    out: dict[str, Any] = {}
    for qualifier, per_type in e2o_qualifier_object_types.items():
        total = sum(per_type.values())
        if total == 0 or len(per_type) < 2:
            continue
        dominant, dom_count = max(per_type.items(), key=lambda kv: kv[1])
        if dom_count / total < DOMINANT_SHARE:
            continue
        outliers = []
        for obj_type, count in per_type.items():
            if obj_type == dominant:
                continue
            ids = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT eo.ocel_object_id
                    FROM event_object eo JOIN object o ON o.ocel_id = eo.ocel_object_id
                    WHERE eo.ocel_qualifier = ? AND o.ocel_type = ?
                    LIMIT 50
                    """,
                    (qualifier, obj_type),
                ).fetchall()
            ]
            outliers.append({"object_type": obj_type, "count": count, "object_ids": ids})
        if outliers:
            out[qualifier] = {
                "dominant_type": dominant,
                "dominant_share": round(dom_count / total, 3),
                "outliers": outliers,
            }
    return out


def type_tables(conn: sqlite3.Connection, tables: list[str]) -> dict[str, str]:
    """Map each object type to its per-type attribute table via object_map_type."""
    if "object_map_type" not in tables:
        return {}
    try:
        rows = conn.execute("SELECT ocel_type, ocel_type_map FROM object_map_type").fetchall()
    except sqlite3.Error:
        return {}
    return {t: f"object_{m}" for t, m in rows if t and f"object_{m}" in tables}


def schema_fingerprint(conn: sqlite3.Connection) -> str:
    """Structural identity of the log: tables+columns, object types, qualifier
    names. Value-level repairs leave it unchanged, so a guide built for this
    structure stays valid; schema or type-set changes invalidate it."""
    tables = list_tables(conn)
    parts = [f"{t}:{','.join(table_columns(conn, t))}" for t in tables]

    def _distinct(sql: str) -> list[str]:
        try:
            return sorted(str(r[0]) for r in conn.execute(sql).fetchall() if r[0])
        except sqlite3.Error:
            return []

    parts.append("types=" + ",".join(_distinct("SELECT DISTINCT ocel_type FROM object")))
    parts.append("e2o_quals=" + ",".join(_distinct("SELECT DISTINCT ocel_qualifier FROM event_object")))
    parts.append("o2o_quals=" + ",".join(_distinct("SELECT DISTINCT ocel_qualifier FROM object_object")))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _type_counts(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT ocel_type, COUNT(*) FROM {quote(table)} GROUP BY ocel_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    return [{"type": r[0], "count": r[1]} for r in rows]


def profile_database(db_path: str | Path) -> dict[str, Any]:
    db_path = Path(db_path)
    conn = connect_readonly(db_path)
    try:
        tables = list_tables(conn)

        profile: dict[str, Any] = {
            "db_path": db_path.as_posix(),
            "tables": {},
            "object_types": [],
            "event_types": [],
            "type_tables": {},
            "qualifiers": {},
            "object_id_patterns_by_type": {},
            "qualifier_context": {},
            "attribute_null_rates": {},
            "attribute_samples": {},
            "attribute_known_values": {},
        }

        for table in tables:
            cols = table_columns(conn, table)
            count = conn.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
            profile["tables"][table] = {"row_count": count, "columns": cols}
            for col in cols:
                key = f"{table}.{col}"
                samples = sample_values(conn, table, col)
                profile["attribute_null_rates"][key] = null_rate(conn, table, col)
                profile["attribute_samples"][key] = samples
                vocabulary = known_values_from_samples(samples)
                if vocabulary is not None:
                    profile["attribute_known_values"][key] = vocabulary

        if "object" in tables and "ocel_type" in profile["tables"]["object"]["columns"]:
            profile["object_types"] = _type_counts(conn, "object")
            profile["object_id_patterns_by_type"] = per_type_id_patterns(conn)
            profile["type_tables"] = type_tables(conn, tables)

        if "event" in tables and "ocel_type" in profile["tables"]["event"]["columns"]:
            profile["event_types"] = _type_counts(conn, "event")

        for rel_table, key in (("event_object", "event_object"), ("object_object", "object_object")):
            if rel_table in tables and "ocel_qualifier" in profile["tables"][rel_table]["columns"]:
                rows = conn.execute(
                    f"SELECT ocel_qualifier, COUNT(*) FROM {quote(rel_table)} "
                    "GROUP BY ocel_qualifier ORDER BY COUNT(*) DESC"
                ).fetchall()
                profile["qualifiers"][key] = [
                    {"qualifier": r[0], "count": r[1]} for r in rows
                ]

        profile["qualifier_context"] = qualifier_context(conn, tables)
        profile["qualifier_outliers"] = qualifier_outliers(
            conn, profile["qualifier_context"].get("e2o_qualifier_object_types", {})
        )
        profile["schema_fingerprint"] = schema_fingerprint(conn)
        return profile
    finally:
        conn.close()
