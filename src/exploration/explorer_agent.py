# src/exploration/explorer_agent.py

import json
from pathlib import Path
from typing import Any

from src.exploration.db_profiler import profile_database
from src.llm.client import MODEL, call_ollama_text


EXPLORER_SYSTEM_PROMPT = """\
You are an analyst exploring an OCEL 2.0 event log to understand its domain and structure.

RULES:
1. Replace every [fill:...] placeholder with content derived from DATABASE SIGNALS.
2. Do NOT invent facts not supported by the signals.
3. Keep exactly the Markdown structure given — do not add or remove sections or headings.
4. Be concise. One sentence per bullet is enough.
5. Label every non-obvious claim as (evidence) or (hypothesis).
6. Output Markdown only. No preamble, no commentary outside the skeleton.
"""


def build_explorer_prompt(profile: dict[str, Any]) -> str:
    obj_types = [t["type"] for t in profile["object_types"] if t["type"]]
    e2o_quals = [q["qualifier"] for q in profile["qualifiers"].get("event_object", [])]
    o2o_quals = [q["qualifier"] for q in profile["qualifiers"].get("object_object", [])]
    all_quals = e2o_quals + [q for q in o2o_quals if q not in e2o_quals]

    cols_with_nulls = [
        k for k, v in profile["attribute_null_rates"].items() if v and v > 0
    ]

    obj_type_skeleton = "\n\n".join(
        f"### {t}\n"
        "- **What does this object represent?** [fill: real-world entity — customer, product, order, etc.]\n"
        "- **ID format:** [fill: what do ocel_ids look like? human name, product name, code, numeric?]\n"
        "- **Key attributes:** [fill: which attributes characterise objects of this type? what values do they hold?]\n"
        "- **Connected via qualifiers:** [fill: which qualifiers link events or other objects to this type?]\n"
        "- **Domain knowledge applicable?** [fill: yes/no — are any attributes stable real-world facts (weight, brand, category) that could be looked up?]"
        for t in obj_types
    ) or "### [fill: infer object types from qualifiers and ID patterns]"

    attr_skeleton = "\n\n".join(
        f"### `{col}`  (null rate: {profile['attribute_null_rates'].get(col, '?')})\n"
        f"- **Sample values:** {profile['attribute_samples'].get(col, [])}\n"
        "- **Semantic meaning:** [fill: what does this attribute represent in the real world?]\n"
        "- **Value source:** [fill: where does this value likely come from — event activity, peer objects, external domain knowledge, ID itself?]\n"
        "- **ID as entity name:** [fill: yes/no — does ocel_id of this object contain a recognisable name useful for lookup?]\n"
        "- **Domain knowledge applicable?** [fill: yes/no — is this a stable, entity-specific real-world fact? explain why or why not]\n"
        "- **When is null a real defect?** [fill: conditions under which a missing value here is genuinely wrong]"
        for col in cols_with_nulls
    ) or "### [fill: no missing values detected — note any attributes that look structurally fragile]"

    qualifier_skeleton = "\n".join(
        f"- `{q}`: [fill: what role does the linked object play? what does this relationship mean in the process?]"
        for q in all_quals
    ) or "- [fill: no qualifiers found]"

    return f"""\
Fill in the skeleton below. Replace every [fill:...] with content derived from DATABASE SIGNALS.
Do not change headings. Do not add sections. Output Markdown only.

Your goal is EXPLORATION, not repair. Focus on understanding:
- what business process this log represents
- what each object type, attribute, and qualifier means
- what value patterns and ID formats exist
- which attributes are stable real-world facts vs. log-derived values
- where repair agents should and should NOT apply domain knowledge

DATABASE SIGNALS:
```json
{json.dumps(profile, indent=2, ensure_ascii=False)}
```

---

# OCEL Exploration Report

## 1. Domain Hypothesis

**Process:** [fill: one sentence — what business process does this log likely represent?]

**Evidence:**
- [fill: signal 1 — e.g. object types, event types, activity names]
- [fill: signal 2 — e.g. qualifier names, ID patterns]
- [fill: signal 3 — e.g. attribute names and sample values]

**Confidence:** [fill: high / medium / low — one-line reason]

## 2. Object Type Analysis

{obj_type_skeleton}

## 3. Attribute Semantics

{attr_skeleton}

## 4. Qualifier Semantics

{qualifier_skeleton}

## 5. Data Quality Overview

- **Attributes with missing values:** {cols_with_nulls or ['none detected']}
- [fill: for each attribute with nulls — is the null a defect or a structural property of the column? explain the difference]
- [fill: identify any columns where NULL is expected by design (e.g. delta-encoding fields, optional metadata) — these should be excluded from repair]
- [fill: any attributes that look suspicious even without nulls (e.g. constant values, unexpected formats)?]

## 6. Repair Guidance

- [fill: for which attributes can repair agents look up values from peer objects of the same type?]
- [fill: for which attributes is domain knowledge (common sense / world knowledge) appropriate?]
- [fill: for which attributes should repair agents NOT guess — and why?]
- [fill: any log-specific patterns repair agents must respect (ID formats, naming conventions, units)?]
"""


def create_exploration_report(
    db_path: str | Path,
    output_dir: str | Path = "data/exploration",
    model: str = MODEL,
) -> Path:
    """Profile the database, call the LLM, and write the exploration report.

    Returns the path to the generated exploration_report.md.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    profile = profile_database(db_path)

    profile_path = output_dir / "exploration_profile.json"
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

    prompt = build_explorer_prompt(profile)
    report_text = call_ollama_text(EXPLORER_SYSTEM_PROMPT, prompt, model=model)

    report_path = output_dir / "exploration_report.md"
    report_path.write_text(report_text, encoding="utf-8")

    return report_path
