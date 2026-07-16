"""Corruption injectors for the order-management OCEL SQLite log.

Each `inject_*` function introduces exactly one flavor of a known data-quality
issue and returns the affected ocel_id(s) so callers can log or reference them.
Injectors are grouped by OCEL dimension:

    * `src/corruption/object_issues.py`   — object-side (N2/N3a/N6a/N6b/N7a,
                                            incorrect_attribute_datatype).
    * `src/corruption/event_issues.py`    — event-side missing_event,
                                            missing_event_type,
                                            missing_event_timestamp.
    * `src/corruption/relation_issues.py` — relation-borne (N10, O2O, M5).

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
    inject_n3a_missing_attribute,
)

# Legacy top-level injectors — historic notebooks import these directly.
from src.corruption.object_issues import (
    inject_n2,
    inject_n6a,
    inject_n6b,
    inject_n7a_incorrect_object_type,
)
from src.corruption.relation_issues import (
    inject_n10_event,
    inject_n10_object,
)
from src.corruption.stages import corrupt_database


__all__ = [
    "corrupt_database",
    "DEFAULT_CLEAN_PATH",
    "DEFAULT_DIRTY_PATH",
    "DEFAULT_FULL_PATH",
    # Legacy injector shims for notebooks
    "inject_n2",
    "inject_n3a_missing_attribute",
    "inject_n6a",
    "inject_n6b",
    "inject_n7a_incorrect_object_type",
    "inject_n10_event",
    "inject_n10_object",
]
