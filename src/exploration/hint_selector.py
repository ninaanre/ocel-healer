# src/exploration/hint_selector.py

"""Select the exploration slice relevant to one violation row.

Hints merge two layers with different trust levels:
  - deterministic FACTS from exploration_profile.json — id templates, value
    vocabularies, null rates. Available after any exploration run, even when
    every LLM section failed;
  - LLM INTERPRETATIONS from exploration_guide.json — what a type represents,
    attribute semantics, repair hints. Optional: `guide=None` degrades to
    facts-only hints.

Selection is driven by the fields the violation row already has (object_type,
attribute, ocel_qualifier), so one generic selector serves all issue types;
tasks with special needs override IssueTask.select_hints.
"""

from typing import Any

_GUIDE_ATTR_KEYS = (
    "meaning",
    "value_source",
    "domain_knowledge_applicable",
    "null_expected_by_design",
    "repair_hint",
)


def _type_facts(profile: dict, obj_type: str) -> dict[str, Any]:
    patterns = profile.get("object_id_patterns_by_type", {}).get(obj_type, {})
    if not patterns:
        return {}
    facts: dict[str, Any] = {
        "id_is_entity_name": patterns.get("id_is_entity_name", False),
    }
    # Templates are only informative when one shape dominates (a technical id
    # format like `o-######`); for free-form name ids they are noise.
    templates = patterns.get("templates", [])
    if templates and templates[0].get("share", 0) >= 0.5:
        facts["id_template"] = templates[0]["template"]
    return facts


def select_hints(profile: dict, guide: dict | None, row: dict) -> dict[str, Any]:
    """Generic slice: the row's object type, attribute, and qualifier."""
    guide = guide or {}
    hints: dict[str, Any] = {}

    obj_type = row.get("object_type") or row.get("source_type")
    table = profile.get("type_tables", {}).get(obj_type) if obj_type else None
    guide_type = guide.get("object_types", {}).get(obj_type, {}) if obj_type else {}

    if obj_type and (table or guide_type):
        type_hint: dict[str, Any] = {"type": obj_type, **_type_facts(profile, obj_type)}
        if guide_type:
            type_hint["represents"] = guide_type.get("represents", "")
            type_hint["id_note"] = guide_type.get("id_note", "")
        hints["object_type"] = type_hint

        attr = row.get("attribute") or row.get("attribute_name")
        if attr and table:
            key = f"{table}.{attr}"
            attr_hint: dict[str, Any] = {"name": attr}
            null_rate = profile.get("attribute_null_rates", {}).get(key)
            if null_rate is not None:
                attr_hint["null_rate"] = null_rate
            vocabulary = profile.get("attribute_known_values", {}).get(key)
            if vocabulary:
                attr_hint["known_values"] = vocabulary
            guide_attr = guide_type.get("attributes", {}).get(attr, {})
            attr_hint.update({k: guide_attr[k] for k in _GUIDE_ATTR_KEYS if k in guide_attr})
            if len(attr_hint) > 1:
                hints["attribute"] = attr_hint

    qualifier = row.get("ocel_qualifier")
    role = (guide.get("qualifiers") or {}).get(qualifier) if qualifier else None
    if role:
        hints["qualifier"] = {"name": qualifier, "role": role}

    return hints


def all_type_summaries(profile: dict, guide: dict | None) -> dict[str, Any]:
    """Compact overview of every object type — for tasks that must choose or
    validate an object type (missing_object_type, incorrect_object_type)."""
    guide = guide or {}
    types = set(profile.get("type_tables", {})) | set(guide.get("object_types", {}))
    out: dict[str, Any] = {}
    for t in sorted(types):
        entry = _type_facts(profile, t)
        guide_type = guide.get("object_types", {}).get(t, {})
        if guide_type:
            entry["represents"] = guide_type.get("represents", "")
        out[t] = entry
    return out
