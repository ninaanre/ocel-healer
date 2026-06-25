# src/exploration/hint_selector.py

from src.exploration.report_store import load_exploration_report

# Future: map issue types to specific report sections for more targeted hints.
ISSUE_TO_SECTIONS: dict[str, list[str]] = {
    "missing_object_type": [
        "Object Type Signals",
        "Qualifier Semantics",
        "Repair Policy",
        "Unsafe Assumptions",
    ],
    "missing_attribute_value": [
        "Attribute Repair Hints",
        "Object Type Signals",
        "Repair Policy",
        "Unsafe Assumptions",
    ],
    "dangling_e2o_relationship": [
        "Qualifier Semantics",
        "Object Type Signals",
        "Repair Policy",
    ],
    "dangling_o2o_relationship": [
        "Qualifier Semantics",
        "Object Type Signals",
        "Repair Policy",
    ],
}


def select_hints_for_issue(issue_key: str) -> str:
    """Return exploration hints relevant to the given issue type.

    MVP: returns the full report for all issue types.
    Future: extract only the sections listed in ISSUE_TO_SECTIONS.
    """
    report = load_exploration_report()
    return report if report else "No exploration report available."
