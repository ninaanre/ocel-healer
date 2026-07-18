"""Corruption injectors for the order-management OCEL SQLite log.

Each `inject_*` function introduces exactly one flavor of a known data-quality
issue and returns the affected ocel_id(s) so callers can log or reference them.
Injectors are grouped by OCEL dimension:

    * `src/corruption/object_issues.py`   — object-side: missing_object_type,
                                            missing_attribute_value,
                                            incorrect_object_type,
                                            incorrect_attribute_datatype,
                                            duplicate_objects_on_ids,
                                            duplicate_objects_on_attributes.
    * `src/corruption/event_issues.py`    — event-side: missing_event,
                                            missing_event_type,
                                            missing_event_timestamp.
    * `src/corruption/relation_issues.py` — relation-borne:
                                            dangling_e2o_relationship,
                                            dangling_o2o_relationship,
                                            missing_object.

``corrupt_database`` (in ``stages.py``, re-exported here) is the single entry
point used by the dashboard, notebooks and tests. It copies the source SQLite
file to the destination and runs the selected `level`:

    legacy — the original hardcoded set (kept for backwards compatibility).
    easy   — one gentle flavor per detectable issue.
    medium — one moderate flavor per detectable issue.
    hard   — one adversarial flavor per detectable issue.
    all    — every injector (36 corruptions across 12 issue types).

    from src.corruption import corrupt_database, DEFAULT_CLEAN_PATH, DEFAULT_FULL_PATH
    from src.detection.error_detection import detect_all
    corrupt_database(DEFAULT_CLEAN_PATH, DEFAULT_FULL_PATH, level='all')
    for k, df in detect_all(DEFAULT_FULL_PATH).items():
        print(f'{k}: {df.height}')
"""

from __future__ import annotations

from src.corruption._common import (
    DEFAULT_CLEAN_PATH,
    DEFAULT_DIRTY_PATH,
    DEFAULT_FULL_PATH,
)
from src.corruption.stages import corrupt_database


__all__ = [
    "corrupt_database",
    "DEFAULT_CLEAN_PATH",
    "DEFAULT_DIRTY_PATH",
    "DEFAULT_FULL_PATH",
]
