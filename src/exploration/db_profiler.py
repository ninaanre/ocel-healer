# src/exploration/db_profiler.py

import sqlite3
import re
from collections import Counter
from pathlib import Path
from typing import Any

""" 
Without LLM gather information about DB 
for giving it to Explorer Agent (explorer_agent.py)
"""


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


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
    limit: int = 20,
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


def detect_id_patterns(values: list[str]) -> dict[str, list[str]]:
    patterns: dict[str, list[str]] = {
        "human_name_like": [],
        "product_name_like": [],
        "prefixed_id_like": [],
        "numeric_id_like": [],
        "email_like": [],
        "natural_language_like": [],
    }

    human_name = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$")
    prefixed_id = re.compile(r"^[a-zA-Z]+[-_]\d+")
    numeric_id = re.compile(r"^\d+$")
    email = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    # very simple MVP heuristic
    product_keywords = [
        "ipad", "iphone", "kindle", "echo", "macbook", "galaxy",
        "watch", "airpods", "camera", "laptop", "tablet"
    ]

    for raw in values:
        if raw is None:
            continue

        value = str(raw).strip()
        lower = value.lower()

        if human_name.match(value):
            patterns["human_name_like"].append(value)
        elif email.match(value):
            patterns["email_like"].append(value)
        elif prefixed_id.match(value):
            patterns["prefixed_id_like"].append(value)
        elif numeric_id.match(value):
            patterns["numeric_id_like"].append(value)
        elif any(k in lower for k in product_keywords):
            patterns["product_name_like"].append(value)
        elif " " in value:
            patterns["natural_language_like"].append(value)

    return {k: v[:10] for k, v in patterns.items() if v}


def portable_db_path(db_path: Path) -> str:
    """Return a portable database identifier for serialized profiles."""
    if not db_path.is_absolute():
        return db_path.as_posix()

    try:
        return db_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return db_path.name


def profile_database(db_path: str | Path) -> dict[str, Any]:
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)

    tables = list_tables(conn)

    profile: dict[str, Any] = {
        "db_path": portable_db_path(db_path),
        "tables": {},
        "object_types": [],
        "event_types": [],
        "qualifiers": {},
        "object_id_patterns": {},
        "attribute_null_rates": {},
        "attribute_samples": {},
    }

    for table in tables:
        cols = table_columns(conn, table)
        count = conn.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]

        profile["tables"][table] = {
            "row_count": count,
            "columns": cols,
        }

        for col in cols:
            nr = null_rate(conn, table, col)
            samples = sample_values(conn, table, col, limit=15)

            profile["attribute_null_rates"][f"{table}.{col}"] = nr
            profile["attribute_samples"][f"{table}.{col}"] = samples

    # OCEL core tables
    if "object" in tables:
        cols = table_columns(conn, "object")

        if "ocel_type" in cols:
            rows = conn.execute(
                """
                SELECT ocel_type, COUNT(*)
                FROM object
                GROUP BY ocel_type
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
            profile["object_types"] = [
                {"type": r[0], "count": r[1]} for r in rows
            ]

        if "ocel_id" in cols:
            ids = sample_values(conn, "object", "ocel_id", limit=300)
            profile["object_id_patterns"] = detect_id_patterns(ids)

    if "event" in tables:
        cols = table_columns(conn, "event")

        if "ocel_type" in cols:
            rows = conn.execute(
                """
                SELECT ocel_type, COUNT(*)
                FROM event
                GROUP BY ocel_type
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
            profile["event_types"] = [
                {"type": r[0], "count": r[1]} for r in rows
            ]

    if "event_object" in tables:
        cols = table_columns(conn, "event_object")

        if "ocel_qualifier" in cols:
            rows = conn.execute(
                """
                SELECT ocel_qualifier, COUNT(*)
                FROM event_object
                GROUP BY ocel_qualifier
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
            profile["qualifiers"]["event_object"] = [
                {"qualifier": r[0], "count": r[1]} for r in rows
            ]

    if "object_object" in tables:
        cols = table_columns(conn, "object_object")

        if "ocel_qualifier" in cols:
            rows = conn.execute(
                """
                SELECT ocel_qualifier, COUNT(*)
                FROM object_object
                GROUP BY ocel_qualifier
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
            profile["qualifiers"]["object_object"] = [
                {"qualifier": r[0], "count": r[1]} for r in rows
            ]

    conn.close()
    return profile
