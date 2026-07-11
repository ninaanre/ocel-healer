# src/exploration/explorer_agent.py

"""LLM Exploration Agent v2.

Interprets the deterministic profile produced by db_profiler and writes a
structured *guide* (JSON) plus a human-readable report (Markdown). Design
principles, learned from v1's failure:

  1. Facts come from the profiler, interpretation from the LLM. The agent is
     never asked to restate names, counts, or patterns — those are copied into
     the guide deterministically.
  2. Sectioned calls instead of one monolith: each call sees only the evidence
     slice it needs (domain, one object-type table, qualifiers, policy), so a
     mid-size model isn't drowned in a 30KB JSON dump.
  3. Every LLM answer is validated against the profile: unknown column names
     are dropped, enums coerced, failures recorded as warnings instead of
     poisoning the guide.

The agent never modifies the database (the profiler opens it read-only).
"""

import json
from pathlib import Path
from typing import Any, Callable

from src.exploration import report_store
from src.exploration.db_profiler import profile_database
from src.llm.client import call_llm

GUIDE_VERSION = 2

# Fraction of name-like ids above which we assert "the id is an entity name".
NAME_LIKE_THRESHOLD = 0.5

# Stop calling the LLM after this many consecutive section failures — when the
# host is down or the model too slow, every remaining call would fail the same way.
ABORT_AFTER = 2

EXPLORER_SYSTEM_PROMPT = """\
You are a data analyst exploring an OCEL 2.0 event log (object-centric process data).
You receive DATABASE EVIDENCE that was extracted deterministically from the log —
every name, count, and sample in it is verified ground truth.

Your job is to INTERPRET the evidence, not to restate or extend it:
- Never invent table names, column names, types, or qualifiers that are absent
  from the evidence.
- Never contradict the evidence.
- Distinguish what the evidence shows from what you hypothesise; mark
  hypotheses with the word "likely".

Respond with ONE JSON object exactly matching the requested schema.
No prose, no markdown fences, no extra keys.
"""

VALUE_SOURCES = (
    "peer_objects",
    "events",
    "object_id",
    "domain_knowledge",
    "derived_from_other_attributes",
    "unknown",
)

ProgressFn = Callable[[str, int, int], None]


# --- validation helpers -----------------------------------------------------

def _as_str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _as_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _as_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _as_str_list(value: Any, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value[:max_items] if isinstance(v, str) and v.strip()]


# --- evidence slices ---------------------------------------------------------

def _column_evidence(profile: dict, table: str) -> dict[str, Any]:
    """Per-column null rates + samples for one table, compact."""
    out = {}
    for col in profile["tables"][table]["columns"]:
        key = f"{table}.{col}"
        out[col] = {
            "null_rate": profile["attribute_null_rates"].get(key),
            "samples": profile["attribute_samples"].get(key, [])[:8],
        }
    return out


def _qualifiers_touching_type(profile: dict, ocel_type: str) -> dict[str, int]:
    out = {}
    e2o = profile.get("qualifier_context", {}).get("e2o_qualifier_object_types", {})
    for qualifier, per_type in e2o.items():
        if ocel_type in per_type:
            out[qualifier] = per_type[ocel_type]
    return out


# --- section calls ------------------------------------------------------------

def _explore_domain(profile: dict, model: str | None) -> dict[str, Any]:
    evidence = {
        "object_types": profile["object_types"],
        "event_types": profile["event_types"],
        "qualifiers": {
            kind: [q["qualifier"] for q in quals]
            for kind, quals in profile["qualifiers"].items()
        },
        "id_examples_by_type": {
            t: {b: info["examples"][:3] for b, info in pat["buckets"].items()}
            for t, pat in profile["object_id_patterns_by_type"].items()
        },
    }
    prompt = f"""\
Based on the DATABASE EVIDENCE below, describe the business process this event
log records.

DATABASE EVIDENCE:
```json
{json.dumps(evidence, ensure_ascii=False, indent=1)}
```

Schema:
{{"process": "<one sentence: what business process is this?>",
 "evidence": ["<up to 4 short bullets citing concrete evidence — real type/qualifier names>"],
 "confidence": "high|medium|low"}}
"""
    payload = call_llm(prompt, system_prompt=EXPLORER_SYSTEM_PROMPT, model=model, timeout=180.0)
    return {
        "process": _as_str(payload.get("process")),
        "evidence": _as_str_list(payload.get("evidence"), 4),
        "confidence": _as_enum(payload.get("confidence"), ("high", "medium", "low"), "low"),
    }


def _explore_object_type(
    profile: dict,
    ocel_type: str,
    table: str,
    domain_process: str,
    model: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    columns = _column_evidence(profile, table)
    id_patterns = profile["object_id_patterns_by_type"].get(ocel_type, {})
    describable = [c for c in columns if c != "ocel_id"]

    evidence = {
        "object_type": ocel_type,
        "row_count": profile["tables"][table]["row_count"],
        "attribute_table": table,
        "columns": columns,
        "ocel_id_patterns": id_patterns,
        "qualifiers_referencing_this_type": _qualifiers_touching_type(profile, ocel_type),
        "domain_hypothesis": domain_process,
    }
    prompt = f"""\
Interpret the object type `{ocel_type}` using the DATABASE EVIDENCE below.

Notes on OCEL 2.0 storage you may rely on:
- Per-type tables often use delta encoding: one initial-state row per object
  plus one row per attribute change, where only the changed column is filled.
  Check the evidence (null rates, a change-marker column) to decide whether
  this table works that way.

DATABASE EVIDENCE:
```json
{json.dumps(evidence, ensure_ascii=False, indent=1, default=str)}
```

Describe EVERY column in this list and no others: {describable}

Schema:
{{"represents": "<one sentence: what real-world entity is this?>",
 "id_note": "<one sentence: what do the ocel_id values of this type contain / how can they be used?>",
 "attributes": {{
   "<column>": {{
     "meaning": "<one sentence>",
     "value_source": "peer_objects|events|object_id|domain_knowledge|derived_from_other_attributes|unknown",
     "domain_knowledge_applicable": <true if this is a stable real-world fact (weight, brand, category) that could be looked up by entity name>,
     "null_expected_by_design": <true if NULL here is a structural property of the storage, not a data defect>,
     "repair_hint": "<one sentence: how should a repair agent fill a genuinely missing value here?>"
   }}
 }}}}
"""
    payload = call_llm(prompt, system_prompt=EXPLORER_SYSTEM_PROMPT, model=model, timeout=180.0)

    raw_attrs = payload.get("attributes")
    raw_attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
    unknown = set(raw_attrs) - set(describable)
    if unknown:
        warnings.append(f"{ocel_type}: dropped hallucinated columns {sorted(unknown)}")

    attributes = {}
    for col in describable:
        a = raw_attrs.get(col)
        a = a if isinstance(a, dict) else {}
        attributes[col] = {
            "meaning": _as_str(a.get("meaning")),
            "value_source": _as_enum(a.get("value_source"), VALUE_SOURCES, "unknown"),
            "domain_knowledge_applicable": _as_bool(a.get("domain_knowledge_applicable")),
            "null_expected_by_design": _as_bool(a.get("null_expected_by_design")),
            "repair_hint": _as_str(a.get("repair_hint")),
            "null_rate": columns[col]["null_rate"],
        }

    name_like = id_patterns.get("name_like_fraction", 0.0)
    return {
        "table": table,
        "represents": _as_str(payload.get("represents")),
        "id_note": _as_str(payload.get("id_note")),
        # Deterministic: computed from ID shape buckets, not LLM opinion.
        "id_is_entity_name": name_like >= NAME_LIKE_THRESHOLD,
        "id_name_like_fraction": name_like,
        "id_patterns": id_patterns.get("buckets", {}),
        "id_templates": id_patterns.get("templates", []),
        "attributes": attributes,
    }


def _explore_qualifiers(profile: dict, domain_process: str, model: str | None,
                        warnings: list[str]) -> dict[str, str]:
    known = [
        q["qualifier"]
        for quals in profile["qualifiers"].values()
        for q in quals
        if q["qualifier"]
    ]
    if not known:
        return {}
    evidence = {
        "domain_hypothesis": domain_process,
        "qualifier_connects_object_types": profile.get("qualifier_context", {}).get(
            "e2o_qualifier_object_types", {}
        ),
        "qualifier_appears_in_event_types": profile.get("qualifier_context", {}).get(
            "e2o_qualifier_event_types", {}
        ),
        "o2o_qualifier_type_pairs": profile.get("qualifier_context", {}).get(
            "o2o_qualifier_type_pairs", {}
        ),
    }
    prompt = f"""\
Explain the role of each relationship qualifier using the DATABASE EVIDENCE
below (which object types each qualifier links and in which event types it appears).

DATABASE EVIDENCE:
```json
{json.dumps(evidence, ensure_ascii=False, indent=1)}
```

Describe EVERY qualifier in this list and no others: {known}

Schema:
{{"qualifiers": {{"<qualifier>": "<one sentence: role of the linked object>"}}}}
"""
    payload = call_llm(prompt, system_prompt=EXPLORER_SYSTEM_PROMPT, model=model, timeout=180.0)
    raw = payload.get("qualifiers")
    raw = raw if isinstance(raw, dict) else {}
    unknown = set(raw) - set(known)
    if unknown:
        warnings.append(f"qualifiers: dropped hallucinated entries {sorted(unknown)}")
    return {q: _as_str(raw.get(q)) for q in known}


# --- orchestration -----------------------------------------------------------

def build_guide(
    profile: dict,
    *,
    model: str | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run all exploration sections over a profile and assemble the guide.

    Section failures are recorded in guide["warnings"] and leave that section
    empty — one bad LLM call never sinks the whole exploration.
    """
    warnings: list[str] = []
    typed = list(profile.get("type_tables", {}).items())
    total_steps = 2 + len(typed)
    step = 0
    failures_in_a_row = 0

    def _tick(name: str) -> None:
        nonlocal step
        step += 1
        if on_progress:
            on_progress(name, step, total_steps)

    def _llm_section(name: str, fn):
        """Run one LLM section; give up on the rest after repeated failures.

        When the host is down or the model can't answer in time, every section
        fails the same way — aborting after ABORT_AFTER consecutive failures
        turns a ~24-minute timeout parade into one fast, clearly-reported stop.
        Returns the section result or None when failed/skipped.
        """
        nonlocal failures_in_a_row
        if failures_in_a_row >= ABORT_AFTER:
            return None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 — degrade, don't die
            failures_in_a_row += 1
            warnings.append(f"{name} failed: {e}")
            if failures_in_a_row >= ABORT_AFTER:
                warnings.append(
                    "aborted remaining LLM sections after "
                    f"{ABORT_AFTER} consecutive failures — check that the LLM host "
                    "is reachable and the model answers within the timeout "
                    "(reasoning-heavy models may be too slow), then re-run exploration"
                )
            return None
        failures_in_a_row = 0
        return result

    guide: dict[str, Any] = {
        "version": GUIDE_VERSION,
        "db_path": profile["db_path"],
        "model": model,
        "source_fingerprint": profile.get("schema_fingerprint", ""),
        "domain": {"process": "", "evidence": [], "confidence": "low"},
        "object_types": {},
        "qualifiers": {},
        # Deterministic facts from the profiler, recorded verbatim: qualifiers
        # dominated by one object type plus the minority objects breaking the
        # pattern — ready-made candidates for incorrect_object_type detection.
        "qualifier_outliers": profile.get("qualifier_outliers", {}),
        "warnings": warnings,
    }

    _tick("domain hypothesis")
    domain = _llm_section("domain exploration", lambda: _explore_domain(profile, model))
    if domain is not None:
        guide["domain"] = domain

    for ocel_type, table in typed:
        _tick(f"object type: {ocel_type}")
        section = _llm_section(
            f"object type {ocel_type!r} exploration",
            lambda t=ocel_type, tb=table: _explore_object_type(
                profile, t, tb, guide["domain"]["process"], model, warnings
            ),
        )
        if section is not None:
            guide["object_types"][ocel_type] = section

    _tick("qualifier semantics")
    qualifiers = _llm_section(
        "qualifier exploration",
        lambda: _explore_qualifiers(profile, guide["domain"]["process"], model, warnings),
    )
    if qualifiers is not None:
        guide["qualifiers"] = qualifiers

    return guide


def _is_notable(attr: dict) -> bool:
    """An attribute earns a report row only when a reader must know something
    about it: it has missing values, domain knowledge applies, or its NULLs
    are structural. Boilerplate columns stay in the guide JSON only."""
    return bool(
        (attr["null_rate"] or 0) > 0
        or attr["domain_knowledge_applicable"]
        or attr["null_expected_by_design"]
    )


def render_report(guide: dict) -> str:
    """Render the validated guide as a compact human-readable Markdown report.

    The report is a summary for people; the machine-facing source of truth is
    exploration_guide.json, which keeps every attribute in full detail.
    """
    d = guide["domain"]
    lines = [
        "# OCEL Exploration Report",
        "",
        f"*Database:* `{guide['db_path']}` — *model:* `{guide.get('model') or 'default'}`",
        "",
        "## 1. Domain",
        "",
        f"**Process:** {d['process'] or '_unknown_'} (confidence: {d['confidence']})",
        "",
    ]
    lines += [f"- {e}" for e in d["evidence"]]

    lines += ["", "## 2. Object Types", ""]
    for t, info in guide["object_types"].items():
        templates = info.get("id_templates", [])
        template_note = (
            f" — dominant pattern `{templates[0]['template']}` ({templates[0]['share']:.0%})"
            if templates and templates[0]["share"] >= 0.5 else ""
        )
        lines += [
            f"### {t}  (`{info['table']}`)",
            "",
            f"- **Represents:** {info['represents'] or '_?_'}",
            f"- **IDs:** {info['id_note'] or '_?_'}{template_note} "
            f"(usable as entity name: **{'yes' if info['id_is_entity_name'] else 'no'}**)",
        ]
        notable = {c: a for c, a in info["attributes"].items() if _is_notable(a)}
        if notable:
            lines += [
                "",
                "| attribute | null rate | meaning | value source | domain knowledge | NULL by design | repair hint |",
                "|---|---|---|---|---|---|---|",
            ]
            for col, a in notable.items():
                lines.append(
                    f"| `{col}` | {a['null_rate']} | {a['meaning']} | {a['value_source']} | "
                    f"{'yes' if a['domain_knowledge_applicable'] else 'no'} | "
                    f"{'yes' if a['null_expected_by_design'] else 'no'} | {a['repair_hint']} |"
                )
        else:
            lines.append("- _No attributes need special attention._")
        lines.append("")

    lines += ["## 3. Qualifier Semantics", ""]
    lines += [f"- `{q}`: {role or '_?_'}" for q, role in guide["qualifiers"].items()]

    outliers = guide.get("qualifier_outliers", {})
    if outliers:
        lines += ["", "## 4. Qualifier Anomalies (deterministic)", ""]
        for q, info in outliers.items():
            for o in info["outliers"]:
                lines.append(
                    f"- `{q}` points to **{info['dominant_type']}** in "
                    f"{info['dominant_share']:.0%} of links, but {o['count']} link(s) "
                    f"reference type **{o['object_type']}** — candidate wrong-type objects "
                    f"(first ids: {', '.join(map(str, o['object_ids'][:5]))})"
                )

    if guide["warnings"]:
        lines += ["", "## Validation Warnings", ""]
        lines += [f"- {w}" for w in guide["warnings"]]

    return "\n".join(lines) + "\n"


def explore_database(
    db_path: str | Path,
    *,
    model: str | None = None,
    base_dir: str | Path = report_store.DEFAULT_BASE_DIR,
    on_progress: ProgressFn | None = None,
) -> Path:
    """Full pipeline: profile → guide → report. Returns the report path.

    Writes exploration_profile.json, exploration_guide.json and
    exploration_report.md into data/exploration/<db_stem>/.
    """
    profile = profile_database(db_path)
    report_store.save_json(report_store.profile_path(db_path, base_dir), profile)

    guide = build_guide(profile, model=model, on_progress=on_progress)

    # A run where every LLM section failed must not clobber a previous guide
    # that has real content — keep the old files and report the failure loudly.
    has_llm_content = bool(
        guide["domain"]["process"] or guide["object_types"] or guide["qualifiers"]
    )
    previous = report_store.load_guide(db_path, base_dir)
    previous_has_content = bool(previous and previous.get("object_types"))
    if not has_llm_content and previous_has_content:
        raise RuntimeError(
            "exploration produced no LLM content — kept the previous guide. "
            + (guide["warnings"][-1] if guide["warnings"] else "")
        )

    report_store.save_json(report_store.guide_path(db_path, base_dir), guide)
    report = render_report(guide)
    path = report_store.report_path(db_path, base_dir)
    path.write_text(report, encoding="utf-8")
    return path
