"""Per-log configuration hints for the domain-expert LLM.

Each SQLite log can carry a YAML sidecar (`<db-stem>.hints.yaml`) that
supplies:
  - a `data_semantics` paragraph describing how the tables encode state
    (e.g. delta encoding of object-type tables), and
  - per-attribute hints keyed by attribute name — extra guidance and a
    flag saying whether the object's `ocel_id` doubles as its name.

The prompts stay dataset-agnostic; anything log-specific lives here and is
threaded into the task context via `build_context(..., hints=hints)`.

Resolution order for `DatasetHints.load(sqlite_path)`:
  1. `<sqlite_path stem>.hints.yaml` next to the .sqlite file
  2. `data/dataset_hints.yaml` (relative to cwd)
  3. `$OCEL_DATASET_HINTS` env var (path to a yaml file)
  4. Empty hints (the dataset-agnostic default)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


DEFAULT_NAME_COLUMNS: tuple[str, ...] = ("name", "title", "product_name", "label")


@dataclass(frozen=True)
class AttributeHint:
    """Guidance for one attribute (by column name).

    `object_types` narrows the hint to specific object types — an empty
    tuple means the hint applies to any type. `id_is_name` says the
    object's `ocel_id` should be used as its human-readable name when no
    `name`-shaped column is present (helpful for logs like
    order-management where product names are stored in ocel_id).
    """

    guidance: str = ""
    object_types: tuple[str, ...] = ()
    id_is_name: bool = False

    def applies_to(self, object_type: str | None) -> bool:
        if not self.object_types:
            return True
        if object_type is None:
            return False
        needle = object_type.lower()
        return any(needle == t.lower() for t in self.object_types)


@dataclass(frozen=True)
class DatasetHints:
    """Bundle of per-log guidance. Empty instance = fully dataset-agnostic."""

    data_semantics: str | None = None
    attribute_hints: dict[str, AttributeHint] = field(default_factory=dict)
    name_columns: tuple[str, ...] = DEFAULT_NAME_COLUMNS

    def hint_for(self, attribute_name: str | None, object_type: str | None) -> AttributeHint | None:
        if not attribute_name:
            return None
        hint = self.attribute_hints.get(attribute_name)
        if hint is None or not hint.applies_to(object_type):
            return None
        return hint

    # ---- loading -----------------------------------------------------

    @classmethod
    def empty(cls) -> "DatasetHints":
        return cls()

    @classmethod
    def load(cls, sqlite_path: str | os.PathLike[str] | None) -> "DatasetHints":
        """Try each source in order; return `empty()` if nothing is found."""
        for candidate in _candidate_paths(sqlite_path):
            if candidate and candidate.is_file():
                return cls.from_yaml(candidate)
        return cls.empty()

    @classmethod
    def from_yaml(cls, path: Path) -> "DatasetHints":
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetHints":
        data_semantics = data.get("data_semantics")
        if data_semantics is not None:
            data_semantics = str(data_semantics).strip() or None

        raw_hints = data.get("attribute_hints") or {}
        attribute_hints: dict[str, AttributeHint] = {}
        for name, spec in raw_hints.items():
            spec = spec or {}
            attribute_hints[str(name)] = AttributeHint(
                guidance=str(spec.get("guidance", "")).strip(),
                object_types=tuple(spec.get("object_types") or ()),
                id_is_name=bool(spec.get("id_is_name", False)),
            )

        name_cols = data.get("name_columns")
        name_columns = tuple(name_cols) if name_cols else DEFAULT_NAME_COLUMNS

        return cls(
            data_semantics=data_semantics,
            attribute_hints=attribute_hints,
            name_columns=name_columns,
        )


def _candidate_paths(sqlite_path: str | os.PathLike[str] | None) -> list[Path | None]:
    candidates: list[Path | None] = []
    if sqlite_path:
        p = Path(sqlite_path)
        candidates.append(p.with_suffix(".hints.yaml"))
    candidates.append(Path("data") / "dataset_hints.yaml")
    env = os.getenv("OCEL_DATASET_HINTS")
    if env:
        candidates.append(Path(env))
    return candidates


@lru_cache(maxsize=8)
def load_cached(sqlite_path: str) -> DatasetHints:
    """Memoised loader for use inside a resolution turn."""
    return DatasetHints.load(sqlite_path)


__all__ = ["AttributeHint", "DatasetHints", "load_cached", "DEFAULT_NAME_COLUMNS"]
