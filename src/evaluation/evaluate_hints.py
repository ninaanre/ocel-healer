# src/evaluation/evaluate_hints.py

"""Measure whether exploration hints improve LLM repair accuracy.

Self-contained experiment:
  1. Copy the clean log and inject missing values with known groundtruth.
  2. Run exploration on the corrupted copy (guide lands next to it, where
     the repair tasks' hint lookup expects it).
  3. For every detected missing_attribute_value violation ask the repair
     agent twice — with exploration hints (arm A) and without (arm B).
  4. Score both arms against groundtruth: coverage, exact match, within-10%
     for numerics, mean relative error, mean confidence.

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

from src.corruption import inject_n3a_missing_attribute
from src.detection.error_detection import detect_all
from src.exploration import explore_database
from src.llm.client import set_active_model
from src.llm.resolution import suggest_repair

# (per-type table, column, how many initial-state rows to null out).
# Chosen to cover the interesting regimes:
#   products.weight  — real-world fact + name-bearing ids → hints should enable
#                      domain knowledge; >half injected so peers can't just fill in
#   products.price   — dataset-specific value; domain knowledge may mislead
#   employees.role   — categorical, recoverable from event qualifiers
#   packages.weight  — opaque ids (p-######) → domain knowledge inapplicable
INJECTIONS = [
    ("object_Products", "weight", 12),
    ("object_Products", "price", 5),
    ("object_Employees", "role", 8),
    ("object_Packages", "weight", 8),
]

NUMERIC_TOLERANCE = 0.10  # relative error counted as "within tolerance"


# --- dataset preparation ----------------------------------------------------

def prepare_eval_db(
    clean_path: str | Path,
    eval_dir: str | Path,
    injections: list[tuple[str, str, int]] = INJECTIONS,
) -> tuple[Path, list[dict]]:
    """Copy the clean DB into eval_dir and inject missing values.

    Groundtruth values are captured BEFORE nulling. Injection targets the
    first N eligible initial-state rows, so runs are reproducible.
    """
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    db_path = eval_dir / "eval-run.sqlite"
    shutil.copy2(clean_path, db_path)

    groundtruth: list[dict] = []
    with sqlite3.connect(db_path) as conn:
        for table, column, n in injections:
            rows = conn.execute(
                f'SELECT ocel_id, "{column}" FROM "{table}" '
                f'WHERE "ocel_changed_field" IS NULL AND "{column}" IS NOT NULL',
            ).fetchall()
            # Spread injections evenly over the eligible rows instead of taking
            # the first N: contiguous rows often share a category (employees are
            # grouped by role), and wiping out a whole category is an edge case,
            # not the realistic random-missingness we want to model. Still
            # deterministic — same rows every run.
            if len(rows) > n:
                step = len(rows) / n
                rows = [rows[int(i * step)] for i in range(n)]
            ids = [r[0] for r in rows]
            groundtruth += [
                {"table": table, "column": column, "ocel_id": oid, "true_value": val}
                for oid, val in rows
            ]
            affected = inject_n3a_missing_attribute(conn, table, column, ocel_ids=ids)
            assert sorted(affected) == sorted(ids), f"injection mismatch in {table}.{column}"
        conn.commit()

    (eval_dir / "groundtruth.json").write_text(
        json.dumps(groundtruth, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return db_path, groundtruth


def _table_to_type(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT ocel_type, ocel_type_map FROM object_map_type").fetchall()
    return {f"object_{m}": t for t, m in rows}


def match_violations(db_path: Path, groundtruth: list[dict]) -> list[dict]:
    """Detected missing_attribute_value rows that correspond to injections."""
    table_type = _table_to_type(db_path)
    gt_keys = {
        (table_type.get(g["table"]), g["ocel_id"], g["column"]) for g in groundtruth
    }
    violations = detect_all(str(db_path))["missing_attribute_value"].to_dicts()
    return [
        v for v in violations
        if (v["object_type"], v["ocel_id"], v["attribute"]) in gt_keys
    ]


# --- scoring ------------------------------------------------------------------

def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def score_one(proposed: Any, true_value: Any) -> dict[str, Any]:
    """Exact match, numeric tolerance and relative error for one proposal."""
    p_num, t_num = _as_float(proposed), _as_float(true_value)
    if p_num is not None and t_num is not None:
        rel_err = abs(p_num - t_num) / abs(t_num) if t_num != 0 else (0.0 if p_num == 0 else float("inf"))
        return {
            "exact": p_num == t_num,
            "within_tol": rel_err <= NUMERIC_TOLERANCE,
            "rel_err": rel_err,
        }
    exact = str(proposed).strip().lower() == str(true_value).strip().lower()
    return {"exact": exact, "within_tol": exact, "rel_err": None}


def summarize(records: list[dict]) -> dict[str, Any]:
    """Aggregate one arm's records into the metric set."""
    total = len(records)
    attempted = [r for r in records if r["kind"] == "update"]
    exact = [r for r in attempted if r["exact"]]
    within = [r for r in attempted if r["within_tol"]]
    rel_errs = [r["rel_err"] for r in attempted if r["rel_err"] is not None and r["rel_err"] != float("inf")]
    confidences = [r["confidence"] for r in attempted]
    return {
        "total": total,
        "coverage": len(attempted) / total if total else 0.0,
        "exact_of_attempted": len(exact) / len(attempted) if attempted else 0.0,
        "exact_overall": len(exact) / total if total else 0.0,
        "within_tol_of_attempted": len(within) / len(attempted) if attempted else 0.0,
        "within_tol_overall": len(within) / total if total else 0.0,
        "mean_rel_err": sum(rel_errs) / len(rel_errs) if rel_errs else None,
        "mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "errors": sum(1 for r in records if r["kind"] == "error"),
    }


# --- experiment ---------------------------------------------------------------

def run_arm(
    arm: str,
    use_hints: bool,
    violations: list[dict],
    db_path: Path,
    groundtruth_by_key: dict,
) -> list[dict]:
    records = []
    for i, v in enumerate(violations, 1):
        key = (v["object_type"], v["ocel_id"], v["attribute"])
        true_value = groundtruth_by_key[key]
        t0 = time.time()
        try:
            action = suggest_repair(
                "missing_attribute_value", v, str(db_path), use_hints=use_hints
            )
            kind = action["kind"]
            proposed = action.get("new_value") if kind == "update" else action.get("proposed_value")
            confidence = float(action.get("confidence") or 0.0)
            rationale = action.get("rationale", "")
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the run
            kind, proposed, confidence, rationale = "error", None, 0.0, str(e)
        scores = (
            score_one(proposed, true_value)
            if kind == "update"
            else {"exact": False, "within_tol": False, "rel_err": None}
        )
        records.append({
            "arm": arm,
            "object_type": v["object_type"],
            "ocel_id": v["ocel_id"],
            "attribute": v["attribute"],
            "true_value": true_value,
            "proposed": proposed,
            "kind": kind,
            "confidence": confidence,
            "rationale": rationale,
            **scores,
        })
        print(
            f"  [{arm} {i}/{len(violations)}] {v['object_type']}.{v['attribute']} "
            f"({v['ocel_id']}): {proposed!r} vs {true_value!r} "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )
    return records


def _fmt_pct(x: float) -> str:
    return f"{x:.0%}"


def _metrics_row(label: str, s: dict) -> str:
    rel = f"{s['mean_rel_err']:.2f}" if s["mean_rel_err"] is not None else "—"
    return (
        f"| {label} | {s['total']} | {_fmt_pct(s['coverage'])} | "
        f"{_fmt_pct(s['exact_of_attempted'])} | {_fmt_pct(s['within_tol_of_attempted'])} | "
        f"{_fmt_pct(s['within_tol_overall'])} | {rel} | {s['mean_confidence']:.2f} |"
    )


def render_eval_report(meta: dict, records: list[dict]) -> str:
    arms = {"with_hints": [], "without_hints": []}
    for r in records:
        arms[r["arm"]].append(r)

    header = "| arm | n | coverage | exact (of attempted) | within 10% (of attempted) | within 10% (overall) | mean rel. err | mean conf |"
    sep = "|---|---|---|---|---|---|---|---|"

    lines = [
        "# Exploration Hints Evaluation",
        "",
        f"*Model:* `{meta['model']}` — *date:* {meta['date']} — *duration:* {meta['duration_s']:.0f}s",
        f"*Database:* `{meta['db_path']}` — *injected violations:* {meta['n_injected']}",
        "",
        "## Summary",
        "",
        header,
        sep,
        _metrics_row("A: with hints", summarize(arms["with_hints"])),
        _metrics_row("B: without hints", summarize(arms["without_hints"])),
        "",
        "## Per-column breakdown",
        "",
    ]
    columns = sorted({(r["object_type"], r["attribute"]) for r in records})
    for obj_type, attr in columns:
        lines += [f"### {obj_type}.{attr}", "", header, sep]
        for arm, label in (("with_hints", "A: with hints"), ("without_hints", "B: without hints")):
            subset = [r for r in arms[arm] if (r["object_type"], r["attribute"]) == (obj_type, attr)]
            lines.append(_metrics_row(label, summarize(subset)))
        lines.append("")

    lines += [
        "## Per-violation detail",
        "",
        "| object | attribute | groundtruth | A: with hints | ✓ | B: without hints | ✓ |",
        "|---|---|---|---|---|---|---|",
    ]
    b_by_key = {(r["ocel_id"], r["attribute"]): r for r in arms["without_hints"]}
    for a in arms["with_hints"]:
        b = b_by_key.get((a["ocel_id"], a["attribute"]), {})
        mark = lambda r: "✅" if r.get("within_tol") else ("—" if r.get("kind") != "update" else "❌")
        lines.append(
            f"| {a['ocel_id']} | {a['attribute']} | {a['true_value']} "
            f"| {a['proposed']} | {mark(a)} | {b.get('proposed')} | {mark(b)} |"
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
    print(f"   {len(groundtruth)} values nulled in {db_path}", flush=True)

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

    groundtruth_by_key = {
        (v["object_type"], v["ocel_id"], v["attribute"]):
            next(
                g["true_value"] for g in groundtruth
                if g["ocel_id"] == v["ocel_id"] and g["column"] == v["attribute"]
            )
        for v in violations
    }

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
