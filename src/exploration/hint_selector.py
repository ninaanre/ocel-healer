# src/exploration/hint_selector.py

"""Select the guide slice relevant to one violation row.

The exploration guide holds knowledge about the whole log; a repair prompt
should only carry the part that matters for the row being repaired — a few
hundred tokens, not the full report. Selection is driven by the fields the
violation row already has (object_type, attribute, ocel_qualifier), so one
generic selector serves all issue types; tasks with special needs override
IssueTask.select_hints.
"""

from typing import Any


def _object_type_hint(guide: dict, obj_type: str) -> dict[str, Any] | None:
    info = guide.get("object_types", {}).get(obj_type)
    if not info:
        return None
    return {
        "type": obj_type,
        "represents": info.get("represents", ""),
        "id_note": info.get("id_note", ""),
        "id_is_entity_name": info.get("id_is_entity_name", False),
        "id_templates": info.get("id_templates", []),
    }


def select_hints(guide: dict, row: dict) -> dict[str, Any]:
    """Generic slice: the row's object type, attribute, and qualifier."""
    hints: dict[str, Any] = {}

    obj_type = row.get("object_type") or row.get("source_type")
    type_hint = _object_type_hint(guide, obj_type) if obj_type else None
    if type_hint:
        hints["object_type"] = type_hint
        attr = row.get("attribute") or row.get("attribute_name")
        attr_info = guide["object_types"][obj_type].get("attributes", {}).get(attr)
        if attr_info:
            hints["attribute"] = {"name": attr, **attr_info}

    qualifier = row.get("ocel_qualifier")
    role = guide.get("qualifiers", {}).get(qualifier) if qualifier else None
    if role:
        hints["qualifier"] = {"name": qualifier, "role": role}

    return hints


def all_type_summaries(guide: dict) -> dict[str, Any]:
    """Compact overview of every object type — for tasks that must choose or
    validate an object type (missing_object_type, incorrect_object_type)."""
    return {
        t: {
            "represents": info.get("represents", ""),
            "id_is_entity_name": info.get("id_is_entity_name", False),
            "id_templates": info.get("id_templates", []),
        }
        for t, info in guide.get("object_types", {}).items()
    }
