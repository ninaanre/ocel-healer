# src/evaluation/evaluate_hints.py

"""Measure whether exploration hints improve LLM repair accuracy.

Self-contained experiment, generalized across every issue type that has a
real, checkable groundtruth in the clean log (as opposed to issues like
missing_event/missing_object/dangling_* where the corruption fabricates a
reference to something that never existed, so there is no true answer to
compare against -- those need a plausibility metric, not this harness):

  - missing_attribute_value       (numeric or categorical, per column)
  - incorrect_attribute_datatype  (numeric; corrupted with a recoverable
                                   comma-decimal, not the demo's 'unknown'/blob
                                   recipes -- those discard the value entirely
                                   and would just test refusal, not repair)
  - missing_object_type           (categorical: the object's true type)
  - missing_event_type            (categorical: the event's true type)
  - missing_event_timestamp       (temporal: scored by mean |Δ hours|, never
                                   exact -- an interpolated time is not
                                   expected to land on the original instant)
  - incorrect_object_type         (categorical: the object's true type. A
                                   DetectionTask, not a ResolutionTask -- the
                                   LLM decides whether the row is even wrong,
                                   via `detect_with_llm`, not `suggest_repair`.
                                   Not covered by `detect_all()`, so its
                                   violation rows are built directly from the
                                   known injection, same shape the dashboard
                                   itself uses: {ocel_id, ocel_type, issue}.)

Steps:
  1. Copy the clean log and inject known-groundtruth violations across all
     the issue types above.
  2. Run exploration on the corrupted copy (guide lands next to it, where
     the repair tasks' hint lookup expects it).
  3. For every matched violation, ask the repair agent twice — with
     exploration hints (arm A) and without (arm B).
  4. Score both arms: coverage, exact match, and — within each issue
     type/column, never mixed across units — mean absolute/relative
     deviation (numeric) or mean |Δ hours| (timestamps). No pass/fail
     threshold anywhere.

Both arms see identical violations on the same database; the only variable
is the presence of `exploration_hints` in the repair context.

Usage:
    python -m src.evaluation.evaluate_hints --model mistral-small3.2:latest
"""

import argparse
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.corruption._common import inject_missing_attribute_value
from src.detection.error_detection import _event_type_tables, detect_all
from src.exploration import explore_database
from src.llm.client import set_active_model
from src.llm.resolution import detect_with_llm, suggest_repair

# (issue_key, object_type, table, column, n). `object_type` is the lowercase
# plural used in `object.ocel_type` (SQLite table names are case-insensitive,
# so `object_{object_type}` still resolves to e.g. `object_Products`).
#
# Sample sizes below are chosen to cover interesting regimes while keeping
# total runtime reasonable (~140 LLM calls across both arms):
#   products.weight  — real-world fact + name-bearing ids → hints should
#                      enable domain knowledge; >half injected so peers alone
#                      can't fill the gap
#   products.price   — dataset-specific value; domain knowledge may mislead
#   employees.role   — categorical, recoverable from event qualifiers
#   packages.weight  — opaque ids (p-######) → domain knowledge inapplicable
#   products.{weight,price} datatype — comma-decimal corruption, recoverable
#                      by construction (unlike the demo's 'unknown'/blob)
ATTRIBUTE_INJECTIONS: list[tuple[str, str, str, str, int]] = [
    ("missing_attribute_value", "products", "object_Products", "weight", 12),
    ("missing_attribute_value", "products", "object_Products", "price", 5),
    ("missing_attribute_value", "employees", "object_Employees", "role", 8),
    ("missing_attribute_value", "packages", "object_Packages", "weight", 8),
    ("incorrect_attribute_datatype", "products", "object_Products", "weight", 6),
    ("incorrect_attribute_datatype", "products", "object_Products", "price", 6),
]

# Sample sizes for the issue types with no single (table, column) target --
# they're sampled across every object/event type in the log.
TYPE_INJECTIONS: dict[str, int] = {
    "missing_object_type": 8,
    "missing_event_type": 8,
    "missing_event_timestamp": 8,
    "incorrect_object_type": 8,
}


def _sample_evenly(rows: list, n: int) -> list:
    """Deterministic spread over eligible rows instead of the first N:
    contiguous rows often share a category (employees are grouped by role),
    and wiping out a whole category is an edge case, not the realistic
    random-missingness we want to model. Same rows every run."""
    if len(rows) > n:
        step = len(rows) / n
        rows = [rows[int(i * step)] for i in range(n)]
    return rows


# --- per-issue injectors -----------------------------------------------------
# Each returns a list of groundtruth dicts: {issue_key, ocel_id, true_value,
# ...match-key fields}. Groundtruth is captured BEFORE corrupting.

def _inject_missing_attribute_value_gt(
    conn: sqlite3.Connection, table: str, column: str, n: int, object_type: str
) -> list[dict]:
    has_changed_field = conn.execute(
        f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = 'ocel_changed_field'"
    ).fetchone()[0]
    where = (
        'WHERE "ocel_changed_field" IS NULL AND "{c}" IS NOT NULL'
        if has_changed_field else 'WHERE "{c}" IS NOT NULL'
    ).format(c=column)
    rows = _sample_evenly(
        conn.execute(f'SELECT ocel_id, "{column}" FROM "{table}" {where}').fetchall(), n
    )
    ids = [r[0] for r in rows]
    affected = inject_missing_attribute_value(conn, table, column, ocel_ids=ids)
    assert sorted(affected) == sorted(ids), f"injection mismatch in {table}.{column}"
    return [
        {"issue_key": "missing_attribute_value", "object_type": object_type,
         "ocel_id": oid, "attribute": column, "true_value": val}
        for oid, val in rows
    ]


def _inject_incorrect_attribute_datatype_gt(
    conn: sqlite3.Connection, table: str, column: str, n: int, object_type: str
) -> list[dict]:
    """Comma-decimal corruption (8.484 -> "8,484"): recoverable by
    construction, unlike the demo corruption's 'unknown'/blob recipes, which
    discard the value entirely and would make this eval measure refusal
    rather than repair quality.

    The UPDATE is scoped to the initial-state row (ocel_changed_field IS
    NULL), matching the SELECT above -- unlike missing_attribute_value's
    detector (which only ever looks at initial-state rows), the datatype
    detector scans every row including delta rows, so an unscoped `WHERE
    ocel_id = ?` would corrupt every later delta row sharing that id too
    (e.g. a product with several price-change rows) and inflate the count
    of "injected" violations well past `n`.
    """
    rows = _sample_evenly(
        conn.execute(
            f'SELECT ocel_id, "{column}" FROM "{table}" '
            f'WHERE "ocel_changed_field" IS NULL AND "{column}" IS NOT NULL'
        ).fetchall(),
        n,
    )
    for oid, val in rows:
        conn.execute(
            f'UPDATE "{table}" SET "{column}" = ? '
            f'WHERE ocel_id = ? AND "ocel_changed_field" IS NULL',
            (str(val).replace(".", ","), oid),
        )
    return [
        {"issue_key": "incorrect_attribute_datatype", "object_type": object_type,
         "ocel_id": oid, "attribute": column, "true_value": val}
        for oid, val in rows
    ]


def _inject_missing_object_type_gt(conn: sqlite3.Connection, n: int) -> list[dict]:
    rows = _sample_evenly(
        conn.execute(
            "SELECT ocel_id, ocel_type FROM object WHERE ocel_type IS NOT NULL AND ocel_type != ''"
        ).fetchall(),
        n,
    )
    for oid, _ in rows:
        conn.execute("UPDATE object SET ocel_type = NULL WHERE ocel_id = ?", (oid,))
    return [{"issue_key": "missing_object_type", "ocel_id": oid, "true_value": t} for oid, t in rows]


def _inject_missing_event_type_gt(conn: sqlite3.Connection, n: int) -> list[dict]:
    rows = _sample_evenly(
        conn.execute(
            "SELECT ocel_id, ocel_type FROM event WHERE ocel_type IS NOT NULL AND ocel_type != ''"
        ).fetchall(),
        n,
    )
    for oid, _ in rows:
        conn.execute("UPDATE event SET ocel_type = NULL WHERE ocel_id = ?", (oid,))
    return [{"issue_key": "missing_event_type", "ocel_id": oid, "true_value": t} for oid, t in rows]


def _inject_incorrect_object_type_gt(conn: sqlite3.Connection, n: int) -> list[dict]:
    """Retype `n` real objects to a different, still-valid type -- rotating
    through the schema's distinct types in sorted order (customers ->
    employees -> items -> ... -> products -> customers) so the corrupted
    type is always wrong but never NULL/empty. Fully deterministic: no
    randomness, same swaps every run.

    Unlike missing_object_type, the corrupted value survives as a real (if
    wrong) type, so groundtruth is the object's TRUE original type; the
    corrupted value is kept too since the violation row this task expects
    ({ocel_id, ocel_type, issue}) needs the CURRENT (wrong) type, not the
    true one -- see match_violations, which builds this row directly since
    incorrect_object_type isn't covered by detect_all()."""
    types = sorted(
        r[0] for r in conn.execute(
            "SELECT DISTINCT ocel_type FROM object WHERE ocel_type IS NOT NULL AND ocel_type != ''"
        ).fetchall()
    )
    swap = {t: types[(i + 1) % len(types)] for i, t in enumerate(types)}
    rows = _sample_evenly(
        conn.execute(
            "SELECT ocel_id, ocel_type FROM object WHERE ocel_type IS NOT NULL AND ocel_type != ''"
        ).fetchall(),
        n,
    )
    out = []
    for oid, true_type in rows:
        wrong_type = swap[true_type]
        conn.execute("UPDATE object SET ocel_type = ? WHERE ocel_id = ?", (wrong_type, oid))
        out.append({
            "issue_key": "incorrect_object_type", "ocel_id": oid,
            "true_value": true_type, "corrupted_value": wrong_type,
        })
    return out


def _inject_missing_event_timestamp_gt(conn: sqlite3.Connection, n: int) -> list[dict]:
    candidates = []
    for _, table in _event_type_tables(conn):
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if "ocel_time" not in cols:
            continue
        for oid, ts in conn.execute(
            f'SELECT ocel_id, ocel_time FROM "{table}" WHERE ocel_time IS NOT NULL'
        ).fetchall():
            candidates.append((table, oid, ts))
    picked = _sample_evenly(candidates, n)
    for table, oid, _ in picked:
        conn.execute(f'UPDATE "{table}" SET ocel_time = NULL WHERE ocel_id = ?', (oid,))
    return [
        {"issue_key": "missing_event_timestamp", "ocel_id": oid, "true_value": ts}
        for _, oid, ts in picked
    ]


# --- dataset preparation ----------------------------------------------------

def prepare_eval_db(
    clean_path: str | Path,
    eval_dir: str | Path,
    attribute_injections: list[tuple[str, str, str, str, int]] = ATTRIBUTE_INJECTIONS,
    type_injections: dict[str, int] = TYPE_INJECTIONS,
) -> tuple[Path, list[dict]]:
    """Copy the clean DB into eval_dir and inject known-groundtruth violations
    across every covered issue type. Groundtruth is captured BEFORE corrupting."""
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    db_path = eval_dir / "eval-run.sqlite"
    shutil.copy2(clean_path, db_path)

    groundtruth: list[dict] = []
    with sqlite3.connect(db_path) as conn:
        for issue_key, object_type, table, column, n in attribute_injections:
            inject_fn = (
                _inject_missing_attribute_value_gt if issue_key == "missing_attribute_value"
                else _inject_incorrect_attribute_datatype_gt
            )
            groundtruth += inject_fn(conn, table, column, n, object_type)
        if "missing_object_type" in type_injections:
            groundtruth += _inject_missing_object_type_gt(conn, type_injections["missing_object_type"])
        if "incorrect_object_type" in type_injections:
            # Must run after missing_object_type: both sample from the same
            # "ocel_type IS NOT NULL" pool, and missing_object_type's NULLs
            # naturally remove its picks from later pools -- no overlap risk
            # as long as this order is preserved.
            groundtruth += _inject_incorrect_object_type_gt(
                conn, type_injections["incorrect_object_type"]
            )
        if "missing_event_type" in type_injections:
            groundtruth += _inject_missing_event_type_gt(conn, type_injections["missing_event_type"])
        if "missing_event_timestamp" in type_injections:
            groundtruth += _inject_missing_event_timestamp_gt(
                conn, type_injections["missing_event_timestamp"]
            )
        conn.commit()

    (eval_dir / "groundtruth.json").write_text(
        json.dumps(groundtruth, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return db_path, groundtruth


def _match_key(issue_key: str, entry: dict) -> tuple:
    """Same key shape from a groundtruth entry and from a detect_all() row --
    both carry these fields under the same names. `issue_key` is always the
    first element so two issue types can never collide even if they happen
    to sample the same underlying ocel_id (e.g. missing_event_type and
    missing_event_timestamp both sample from event ids)."""
    if issue_key in ("missing_attribute_value", "incorrect_attribute_datatype"):
        return (issue_key, entry["object_type"], entry["ocel_id"], entry["attribute"])
    return (issue_key, entry["ocel_id"])


def match_violations(db_path: Path, groundtruth: list[dict]) -> list[dict]:
    """Detected violations (across every covered issue type) that correspond
    to a known injection. Each returned row keeps its own 'issue' field
    (already present on every detect_all() row) so callers know which task
    repairs it.

    incorrect_object_type is a DetectionTask, not covered by detect_all() at
    all (there is no rule that can tell a wrong-but-valid type from a right
    one without judgment) -- its violation rows are built directly from the
    known injection instead, in the same shape the dashboard's own sweep
    uses: {ocel_id, ocel_type, issue}.
    """
    by_issue_keys: dict[str, set] = {}
    for g in groundtruth:
        if g["issue_key"] == "incorrect_object_type":
            continue
        by_issue_keys.setdefault(g["issue_key"], set()).add(_match_key(g["issue_key"], g))

    all_detected = detect_all(str(db_path))
    matched: list[dict] = []
    for issue_key, keys in by_issue_keys.items():
        df = all_detected.get(issue_key)
        if df is None:
            continue
        matched += [row for row in df.to_dicts() if _match_key(issue_key, row) in keys]

    matched += [
        {"ocel_id": g["ocel_id"], "ocel_type": g["corrupted_value"], "issue": "incorrect_object_type"}
        for g in groundtruth if g["issue_key"] == "incorrect_object_type"
    ]
    return matched


# --- scoring ------------------------------------------------------------------

def _as_float(value: Any) -> float | None:
    """Parse a number, tolerating a comma decimal separator (e.g. "0,8").
    The prompts ask for a bare period-decimal number, but models sometimes
    answer in comma-decimal style anyway; without this fallback those
    replies would be scored as an unparseable string mismatch instead of
    the real (often small) numeric deviation they actually represent."""
    s = str(value).strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def score_one(proposed: Any, true_value: Any) -> dict[str, Any]:
    """Exact match plus, for numeric fields, absolute/relative deviation.

    No pass/fail threshold: deviation is reported as a continuous value so
    the report can show how far off a miss was, not just whether it cleared
    an arbitrary cutoff."""
    p_num, t_num = _as_float(proposed), _as_float(true_value)
    if p_num is not None and t_num is not None:
        abs_err = abs(p_num - t_num)
        rel_err = abs_err / abs(t_num) if t_num != 0 else (0.0 if p_num == 0 else float("inf"))
        return {"exact": abs_err == 0, "abs_err": abs_err, "rel_err": rel_err, "abs_hours": None}
    exact = str(proposed).strip().lower() == str(true_value).strip().lower()
    return {"exact": exact, "abs_err": None, "rel_err": None, "abs_hours": None}


def score_temporal(proposed: Any, true_value: Any) -> dict[str, Any]:
    """Mean-|Δ hours| for timestamp fields. No exact-match concept: an
    interpolated time is never expected to land on the original instant."""
    a, t = _parse_dt(proposed), _parse_dt(true_value)
    if a is None or t is None:
        return {"exact": False, "abs_err": None, "rel_err": None, "abs_hours": None}
    delta_h = abs((a - t).total_seconds()) / 3600.0
    return {"exact": delta_h == 0, "abs_err": None, "rel_err": None, "abs_hours": delta_h}


def summarize(records: list[dict]) -> dict[str, Any]:
    """Aggregate one arm's records. Deviation fields (mean_abs_err/
    mean_rel_err/mean_abs_hours) are only meaningful when every record shares
    the same unit (one issue_key/column) -- never call this on a mix of
    different columns/issue types and read those fields."""
    total = len(records)
    attempted = [r for r in records if r["kind"] == "update"]
    exact = [r for r in attempted if r["exact"]]
    abs_errs = [r["abs_err"] for r in attempted if r.get("abs_err") is not None and r["abs_err"] != float("inf")]
    rel_errs = [r["rel_err"] for r in attempted if r.get("rel_err") is not None and r["rel_err"] != float("inf")]
    abs_hours = [r["abs_hours"] for r in attempted if r.get("abs_hours") is not None]
    confidences = [r["confidence"] for r in attempted]
    return {
        "total": total,
        "attempted": len(attempted),
        "coverage": len(attempted) / total if total else 0.0,
        "exact": len(exact),
        "exact_of_attempted": len(exact) / len(attempted) if attempted else 0.0,
        "mean_abs_err": sum(abs_errs) / len(abs_errs) if abs_errs else None,
        "mean_rel_err": sum(rel_errs) / len(rel_errs) if rel_errs else None,
        "mean_abs_hours": sum(abs_hours) / len(abs_hours) if abs_hours else None,
        "mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "errors": sum(1 for r in records if r["kind"] == "error"),
    }


# --- experiment ---------------------------------------------------------------

def run_arm(
    arm: str,
    use_hints: bool,
    violations: list[dict],
    db_path: Path,
    groundtruth_by_key: dict[tuple, dict],
) -> list[dict]:
    records = []
    for i, v in enumerate(violations, 1):
        issue_key = v["issue"]
        gt = groundtruth_by_key[_match_key(issue_key, v)]
        true_value = gt["true_value"]
        t0 = time.time()
        try:
            if issue_key == "incorrect_object_type":
                # DetectionTask: the LLM decides whether the row is even
                # wrong, so the "action" is a DetectionResult, not an
                # ActionResult -- adapt it into the same local shape.
                verdict = detect_with_llm(issue_key, v, str(db_path), use_hints=use_hints)
                kind = "update" if verdict.flagged and verdict.suggested_value else "noop"
                proposed = verdict.suggested_value
                confidence = verdict.confidence
                rationale = verdict.rationale
            else:
                action = suggest_repair(issue_key, v, str(db_path), use_hints=use_hints)
                kind = action["kind"]
                proposed = action.get("new_value") if kind == "update" else action.get("proposed_value")
                confidence = float(action.get("confidence") or 0.0)
                rationale = action.get("rationale", "")
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the run
            kind, proposed, confidence, rationale = "error", None, 0.0, str(e)

        score_fn = score_temporal if issue_key == "missing_event_timestamp" else score_one
        scores = (
            score_fn(proposed, true_value) if kind == "update"
            else {"exact": False, "abs_err": None, "rel_err": None, "abs_hours": None}
        )
        records.append({
            "arm": arm,
            "issue_key": issue_key,
            "object_type": v.get("object_type"),
            "ocel_id": v.get("ocel_id"),
            "attribute": v.get("attribute"),
            "true_value": true_value,
            "proposed": proposed,
            "kind": kind,
            "confidence": confidence,
            "rationale": rationale,
            **scores,
        })
        label = f"{v['object_type']}.{v['attribute']}" if v.get("attribute") else issue_key
        print(
            f"  [{arm} {i}/{len(violations)}] {label} ({v.get('ocel_id')}): "
            f"{proposed!r} vs {true_value!r} ({time.time() - t0:.0f}s)",
            flush=True,
        )
    return records


def _fmt_pct(x: float) -> str:
    return f"{x:.0%}"


def _summary_row(label: str, s: dict) -> str:
    """Coverage/exact-rate only -- unit-independent, safe to combine across
    issue types. Deviation numbers belong in the per-issue breakdown, where
    every row shares the same unit."""
    return (
        f"| {label} | {s['total']} | {_fmt_pct(s['coverage'])} | "
        f"{s['exact']}/{s['attempted']} ({_fmt_pct(s['exact_of_attempted'])}) | "
        f"{s['mean_confidence']:.2f} |"
    )


def _metrics_row(label: str, s: dict) -> str:
    if s["mean_abs_hours"] is not None:
        dev, rel = f"{s['mean_abs_hours']:.1f}h", "—"
    elif s["mean_abs_err"] is not None:
        dev = f"{s['mean_abs_err']:.3g}"
        rel = f"{s['mean_rel_err']:.1%}" if s["mean_rel_err"] is not None else "—"
    else:
        dev, rel = "—", "—"
    return (
        f"| {label} | {s['total']} | {_fmt_pct(s['coverage'])} | "
        f"{s['exact']}/{s['attempted']} ({_fmt_pct(s['exact_of_attempted'])}) | "
        f"{dev} | {rel} | {s['mean_confidence']:.2f} |"
    )


def _row_winner(a: dict, b: dict) -> tuple[bool, bool]:
    """Which side(s) get the comparative checkmark in the per-violation table:
    whichever proposal is closer to groundtruth, both on a tie. Neither side
    is marked when either declined (kind != 'update') -- there is nothing to
    compare a non-answer against."""
    if a.get("kind") != "update" or b.get("kind") != "update":
        return False, False
    if a.get("abs_hours") is not None and b.get("abs_hours") is not None:
        return (a["abs_hours"] <= b["abs_hours"], b["abs_hours"] <= a["abs_hours"])
    a_err, b_err = a.get("abs_err"), b.get("abs_err")
    if a_err is not None and b_err is not None:
        return (a_err <= b_err, b_err <= a_err)
    # Categorical (or unparseable numeric): correctness is binary, not a distance.
    return (bool(a.get("exact")), bool(b.get("exact")))


def _detail_cell(r: dict, mark: bool) -> tuple[str, str]:
    """(proposed-value text, delta text) for one side of a per-violation row."""
    if r.get("kind") != "update":
        return str(r.get("kind", "—")), "—"
    check = "✓ " if mark else ""
    if r.get("abs_hours") is not None:
        delta = f"{check}{r['abs_hours']:.1f}h"
    elif r.get("abs_err") is not None:
        pct = f" ({r['rel_err']:.1%})" if r.get("rel_err") not in (None, float("inf")) else ""
        delta = f"{check}{r['abs_err']:.3g}{pct}"
    else:
        delta = f"{check}{'exact' if r.get('exact') else 'mismatch'}"
    return str(r.get("proposed")), delta


def render_eval_report(meta: dict, records: list[dict]) -> str:
    arms = {"with_hints": [], "without_hints": []}
    for r in records:
        arms[r["arm"]].append(r)

    summary_header = "| arm | n | coverage | exact (of attempted) | mean conf |"
    summary_sep = "|---|---|---|---|---|"
    detail_header = "| arm | n | coverage | exact (of attempted) | mean abs. dev. | mean rel. dev. | mean conf |"
    detail_sep = "|---|---|---|---|---|---|---|"

    lines = [
        "# Exploration Hints Evaluation",
        "",
        f"*Model:* `{meta['model']}` — *date:* {meta['date']} — *duration:* {meta['duration_s']:.0f}s",
        f"*Database:* `{meta['db_path']}` — *injected violations:* {meta['n_injected']}",
        "",
        "## Summary (all covered issue types)",
        "",
        "_Deviation isn't shown here — price, weight and time are different units;_ "
        "_averaging them would be meaningless. See the per-issue breakdown below._",
        "",
        summary_header,
        summary_sep,
        _summary_row("A: with hints", summarize(arms["with_hints"])),
        _summary_row("B: without hints", summarize(arms["without_hints"])),
        "",
        "## Per-issue breakdown",
        "",
    ]
    groups = sorted({(r["issue_key"], r.get("object_type"), r.get("attribute")) for r in records})
    for issue_key, obj_type, attr in groups:
        label = issue_key if attr is None else f"{issue_key}: {obj_type}.{attr}"
        lines += [f"### {label}", "", detail_header, detail_sep]
        for arm, alabel in (("with_hints", "A: with hints"), ("without_hints", "B: without hints")):
            subset = [
                r for r in arms[arm]
                if (r["issue_key"], r.get("object_type"), r.get("attribute")) == (issue_key, obj_type, attr)
            ]
            lines.append(_metrics_row(alabel, summarize(subset)))
        lines.append("")

    lines += [
        "## Per-violation detail",
        "",
        "_✓ marks whichever arm landed closer to groundtruth for that row (both marked on a tie)._",
        "",
        "| issue | object | attribute | groundtruth | A: with hints | Δ (A) | B: without hints | Δ (B) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    b_by_key = {(r["issue_key"], r["ocel_id"], r.get("attribute")): r for r in arms["without_hints"]}
    for a in arms["with_hints"]:
        b = b_by_key.get((a["issue_key"], a["ocel_id"], a.get("attribute")), {})
        mark_a, mark_b = _row_winner(a, b)
        val_a, delta_a = _detail_cell(a, mark_a)
        val_b, delta_b = _detail_cell(b, mark_b)
        lines.append(
            f"| {a['issue_key']} | {a['ocel_id']} | {a.get('attribute') or '—'} | {a['true_value']} "
            f"| {val_a} | {delta_a} | {val_b} | {delta_b} |"
        )

    n_errors = sum(1 for r in records if r["kind"] == "error")
    if n_errors:
        lines += [
            "",
            f"⚠️ **{n_errors} call(s) failed with errors** (LLM host issues?) — "
            "metrics above undercount both arms; consider re-running.",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="mistral-small3.2:latest")
    parser.add_argument("--clean-db", default="data/order-management-clean.sqlite")
    parser.add_argument("--eval-dir", default="data/evaluation")
    args = parser.parse_args()

    t0 = time.time()
    set_active_model(args.model)

    # Every run gets its own directory so results accumulate and stay
    # comparable across models/settings instead of overwriting each other.
    model_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", args.model)
    run_id = datetime.now().strftime("%Y%m%d-%H%M") + "-" + model_slug
    run_dir = Path(args.eval_dir) / "runs" / run_id
    print(f"== run dir: {run_dir} ==", flush=True)

    print("== preparing corrupted copy ==", flush=True)
    db_path, groundtruth = prepare_eval_db(args.clean_db, run_dir)
    print(f"   {len(groundtruth)} violations injected in {db_path}", flush=True)

    print("== running exploration ==", flush=True)
    explore_database(
        db_path,
        model=args.model,
        base_dir=run_dir / "exploration",
        on_progress=lambda name, i, total: print(f"   [{i}/{total}] {name}", flush=True),
    )

    print("== detecting violations ==", flush=True)
    violations = match_violations(db_path, groundtruth)
    print(f"   {len(violations)} of {len(groundtruth)} injections detected", flush=True)

    groundtruth_by_key = {_match_key(g["issue_key"], g): g for g in groundtruth}

    records = []
    print("== arm A: with hints ==", flush=True)
    records += run_arm("with_hints", True, violations, db_path, groundtruth_by_key)
    print("== arm B: without hints ==", flush=True)
    records += run_arm("without_hints", False, violations, db_path, groundtruth_by_key)

    meta = {
        "model": args.model,
        "run_id": run_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "db_path": str(db_path),
        "n_injected": len(groundtruth),
        "duration_s": time.time() - t0,
    }
    (run_dir / "results.json").write_text(
        json.dumps({"meta": meta, "records": records}, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report = render_eval_report(meta, records)
    (run_dir / "eval_report.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    print(f"Done in {meta['duration_s']:.0f}s. Artifacts in {run_dir}/", flush=True)


if __name__ == "__main__":
    main()
