"""Coverage tests for the corruption injectors.

Each level of `corrupt_database` should produce, at minimum, one rule-detectable
row per detector on top of the clean-DB baseline.  Values are asserted as
``>=`` because injecting a NULL object type causes a cascade in the two
dangling-relationship detectors (every relationship referencing the retyped
object also becomes dangling), and that cascade grows/shrinks with the
dataset. The `missing_object` injectors also add rows that trip
``dangling_e2o_relationship`` — this is intentional: the same violation
surfaces in both detectors and the dashboard routes it to the appropriate
grid cell. The new ``missing_event`` injectors are analogous: they trip
both ``missing_event`` and ``dangling_e2o_relationship`` (from opposite
sides of the same E2O row).  Regression on the `legacy` level is asserted
with a hardcoded snapshot so accidental changes to the pre-existing dirty
output are caught.
"""

from __future__ import annotations

import pytest

from src.corruption import DEFAULT_CLEAN_PATH, corrupt_database
from src.detection.error_detection import detect_all

RULE_DETECTORS = (
    "missing_object_type",
    "missing_attribute_value",
    "missing_event",
    "missing_event_type",
    "missing_event_timestamp",
    "missing_object",
    "duplicate_objects_on_ids",
    "duplicate_objects_on_attributes",
    "incorrect_attribute_datatype",
    "dangling_o2o_relationship",
    "dangling_e2o_relationship",
)

# Counts on the pristine clean DB.  Only `duplicate_objects_on_attributes` is
# non-zero — OCEL change rows legitimately share (time, changed_field, value)
# fingerprints, so the detector picks them up as attribute duplicates.
CLEAN_BASELINE = {
    "missing_object_type":              0,
    "missing_attribute_value":          0,
    "missing_event":                    0,
    "missing_event_type":               0,
    "missing_event_timestamp":          0,
    "missing_object":                   0,
    "duplicate_objects_on_ids":         0,
    "duplicate_objects_on_attributes":  384,
    "incorrect_attribute_datatype":     0,
    "dangling_o2o_relationship":        0,
    "dangling_e2o_relationship":        0,
}

# Snapshot of the counts produced by the pre-tier `corrupt_database` body,
# captured after regenerating the dirty DB fresh.  If this changes, the
# legacy path has drifted and existing demos may need re-recording. The
# `missing_event=1` entry comes from the legacy
# `inject_dangling_e2o_relationship_event` injector, which inserts an E2O
# row referencing a fake event id — the new `detect_missing_event` picks
# that up as well as the existing `dangling_e2o_relationship` detector.
LEGACY_SNAPSHOT = {
    "missing_object_type":              1,
    "missing_attribute_value":          0,
    "missing_event":                    1,
    "missing_event_type":               0,
    "missing_event_timestamp":          0,
    "missing_object":                   0,
    "duplicate_objects_on_ids":         1,
    "duplicate_objects_on_attributes":  385,
    "incorrect_attribute_datatype":     0,
    "dangling_o2o_relationship":        230,
    "dangling_e2o_relationship":        489,
}


def _counts(path: str) -> dict[str, int]:
    return {k: v.height for k, v in detect_all(path).items()}


@pytest.fixture(scope="module")
def clean_counts() -> dict[str, int]:
    return _counts(DEFAULT_CLEAN_PATH)


@pytest.fixture(scope="module")
def dirty_paths(tmp_path_factory) -> dict[str, str]:
    """Corrupt the clean DB once per level, share across tests."""
    out: dict[str, str] = {}
    for level in ("legacy", "easy", "medium", "hard", "all"):
        dst = tmp_path_factory.mktemp(level) / f"{level}.sqlite"
        out[level] = corrupt_database(DEFAULT_CLEAN_PATH, str(dst), level=level)
    return out


def test_clean_baseline(clean_counts):
    """Guard the baseline so a change in the source DB is caught here."""
    assert clean_counts == CLEAN_BASELINE


def test_legacy_matches_snapshot(dirty_paths):
    """Pre-tier corruption sequence produces the same counts as before."""
    assert _counts(dirty_paths["legacy"]) == LEGACY_SNAPSHOT


@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
@pytest.mark.parametrize("detector", RULE_DETECTORS)
def test_tier_triggers_each_detector(dirty_paths, level, detector):
    """Every tier fires every rule-based detector at least once above baseline."""
    counts = _counts(dirty_paths[level])
    assert counts[detector] >= CLEAN_BASELINE[detector] + 1, (
        f"level={level} detector={detector}: got {counts[detector]}, "
        f"expected >= {CLEAN_BASELINE[detector] + 1}. Full counts: {counts}"
    )


@pytest.mark.parametrize("detector", RULE_DETECTORS)
def test_all_level_triggers_each_detector(dirty_paths, detector):
    """`level='all'` produces at least 3 rows per detector above baseline."""
    counts = _counts(dirty_paths["all"])
    # duplicate_objects_on_ids from `duplicate_objects_on_ids_triple_null_type`
    # counts as a single group of size 3, so the detector row-count
    # contribution is 1 (one group), not 3 — total >= 3 groups (one per flavor).
    expected_new = 3
    assert counts[detector] >= CLEAN_BASELINE[detector] + expected_new, (
        f"detector={detector}: got {counts[detector]}, "
        f"expected >= {CLEAN_BASELINE[detector] + expected_new}. Full counts: {counts}"
    )
