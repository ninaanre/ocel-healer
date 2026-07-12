# src/exploration/report_store.py

"""Per-database storage for exploration artifacts.

Each explored database gets its own directory under data/exploration/, keyed
by the sqlite file's stem, so reports for different logs never overwrite each
other:

    data/exploration/<db_stem>/exploration_profile.json
    data/exploration/<db_stem>/exploration_guide.json
    data/exploration/<db_stem>/exploration_report.md
"""

import json
from pathlib import Path
from typing import Any

DEFAULT_BASE_DIR = Path("data/exploration")


def exploration_dir(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> Path:
    return Path(base_dir) / Path(db_path).stem


def profile_path(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> Path:
    return exploration_dir(db_path, base_dir) / "exploration_profile.json"


def guide_path(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> Path:
    return exploration_dir(db_path, base_dir) / "exploration_guide.json"


def report_path(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> Path:
    return exploration_dir(db_path, base_dir) / "exploration_report.md"


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(p: Path) -> dict[str, Any] | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_guide(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> dict[str, Any] | None:
    return _load_json(guide_path(db_path, base_dir))


def load_profile(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> dict[str, Any] | None:
    return _load_json(profile_path(db_path, base_dir))


def load_report(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> str:
    p = report_path(db_path, base_dir)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def guide_is_stale(db_path: str | Path, base_dir: str | Path = DEFAULT_BASE_DIR) -> bool:
    """True when there is no guide or the log's *structure* changed since it
    was built (tables, object types, qualifiers). Value-level repairs do not
    invalidate the guide — its knowledge is about semantics, not values."""
    from src.exploration.db_profiler import connect_readonly, schema_fingerprint

    guide = load_guide(db_path, base_dir)
    if guide is None:
        return True
    try:
        conn = connect_readonly(db_path)
        try:
            current = schema_fingerprint(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — unreadable db == can't trust the guide
        return True
    return current != guide.get("source_fingerprint")
