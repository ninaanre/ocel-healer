"""Unified task-prompt template.

Every task declares four short strings — `TASK`, `INPUTS`, `METHOD`,
`EXAMPLES` — plus a `family` and an `OutputModel`. `render_task_prompt`
splices them into the same seven-section skeleton so all tasks look
identical from the LLM's point of view.

The `<output_schema>` section is derived automatically from the
pydantic model, so prompt and validator can never disagree.

Shared instructions (verbatim-only, JSON-only, closed-set) live in the
`DECISION_RULES` constant here rather than being restated in each task.
"""

from __future__ import annotations

import textwrap
from typing import Any

from pydantic import BaseModel


DECISION_RULES = textwrap.dedent(
    """
    1. Return values verbatim from provided candidate lists (candidate_types,
       candidate_objects, candidate_events, duplicate_attribute_values, …).
       Never invent a value outside the closed set.
    2. Match the format, casing, and units of the peers or the anchor's
       other attributes. If peers use `EUR`, don't reply `Eur`. If peers
       use `kg`, don't reply in `g`.
    3. Your entire reply is one JSON object matching <output_schema> —
       no prose, no markdown fences, no fields outside the schema.
    """
).strip()


TASK_TEMPLATE = """\
<task>
{task}
</task>

<inputs>
{inputs}
</inputs>

<method>
{method}
</method>

<decision_rules>
{decision_rules}
</decision_rules>

<output_schema>
{output_schema}
</output_schema>

<examples>
{examples}
</examples>"""


def render_task_prompt(
    *,
    task: str,
    inputs: str,
    method: str,
    examples: str,
    output_model: type[BaseModel],
) -> str:
    """Assemble one task prompt from its section strings and pydantic model."""
    return TASK_TEMPLATE.format(
        task=_clean(task),
        inputs=_clean(inputs),
        method=_clean(method),
        decision_rules=DECISION_RULES,
        output_schema=schema_bullets(output_model),
        examples=_clean(examples),
    )


def schema_bullets(model: type[BaseModel]) -> str:
    """Flatten a pydantic model into `- name: type (required)` bullets."""
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return "- (no fields)"

    lines: list[str] = []
    for name, spec in props.items():
        type_str = _describe_type(spec)
        req = "required" if name in required else "optional"
        default = spec.get("default")
        default_bit = "" if default is None or name in required else f", default {default!r}"
        lines.append(f"- {name}: {type_str} ({req}{default_bit})")
    return "\n".join(lines)


def _describe_type(spec: dict[str, Any]) -> str:
    """Human-readable type from a JSON-Schema property spec."""
    if "anyOf" in spec:
        parts = [_describe_type(s) for s in spec["anyOf"]]
        return " | ".join(parts)
    if "type" in spec:
        t = spec["type"]
        if isinstance(t, list):
            return " | ".join(t)
        if t == "array":
            item_type = _describe_type(spec.get("items", {}))
            return f"array[{item_type}]"
        if t == "null":
            return "null"
        # number ranges
        if t == "number":
            lo = spec.get("minimum")
            hi = spec.get("maximum")
            if lo is not None and hi is not None:
                return f"number in [{lo}, {hi}]"
        return t
    return "any"


def _clean(text: str) -> str:
    return textwrap.dedent(text).strip()


__all__ = ["render_task_prompt", "schema_bullets", "TASK_TEMPLATE", "DECISION_RULES"]
