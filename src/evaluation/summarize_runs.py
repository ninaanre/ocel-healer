# src/evaluation/summarize_runs.py

"""Cross-run summary of hint evaluations.

Aggregates every data/evaluation/runs/<run>/results.json into one
data/evaluation/summary.md. Records are RE-SCORED from their raw
`proposed`/`true_value` using the current scoring logic in evaluate_hints.py
-- this keeps old and new-format runs comparable even as the scoring method
evolves (e.g. the switch from a 10%-tolerance threshold to mean deviation),
since a single scoring definition is always in effect.

Usage:
    python -m src.evaluation.summarize_runs
"""

import json
from pathlib import Path

from src.evaluation.evaluate_hints import score_one, score_temporal, summarize

DEFAULT_RUNS_DIR = Path("data/evaluation/runs")

ARM_LABELS = {"with_hints": "A: с хинтами", "without_hints": "B: без хинтов"}


def load_runs(runs_dir: Path = DEFAULT_RUNS_DIR) -> list[dict]:
    runs = []
    for run_dir in sorted(runs_dir.iterdir()):
        results = run_dir / "results.json"
        if not results.exists():
            continue
        try:
            data = json.loads(results.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data["run_dir"] = run_dir.name
        data["records"] = [_rescore(r) for r in data["records"]]
        runs.append(data)
    return runs


def _rescore(r: dict) -> dict:
    """Re-derive scoring fields from the stable raw fields (proposed,
    true_value, kind) so runs produced by any past version of
    evaluate_hints.py score identically under the current definition.
    Older runs predate `issue_key` -- they only ever tested
    missing_attribute_value."""
    issue_key = r.get("issue_key", "missing_attribute_value")
    if r["kind"] != "update":
        scores = {"exact": False, "abs_err": None, "rel_err": None, "abs_hours": None}
    elif issue_key == "missing_event_timestamp":
        scores = score_temporal(r["proposed"], r["true_value"])
    else:
        scores = score_one(r["proposed"], r["true_value"])
    return {**r, "issue_key": issue_key, **scores}


def _columns(records: list[dict]) -> list[tuple[str, str, str]]:
    return sorted({(r["issue_key"], r.get("object_type"), r.get("attribute")) for r in records})


def _ok(records: list[dict]) -> int:
    """Strict hits: committed proposals that exactly matched groundtruth."""
    return sum(1 for r in records if r["kind"] == "update" and r["exact"])


def render_summary(runs: list[dict]) -> str:
    lines = [
        "# Hints Evaluation — Cross-Run Summary",
        "",
        "Every run is re-scored here under the current definition (exact match; "
        "for numeric/temporal columns, mean deviation is reported per issue "
        "type/column in each run's own eval_report.md — different columns use "
        "different units, so they aren't combined here).",
        "",
        "## Runs",
        "",
        "| run | model | injected | duration | errors |",
        "|---|---|---|---|---|",
    ]
    for run in runs:
        meta = run["meta"]
        n_err = sum(1 for r in run["records"] if r["kind"] == "error")
        lines.append(
            f"| {run['run_dir']} | {meta['model']} | {meta['n_injected']} "
            f"| {meta['duration_s']:.0f}s | {n_err} |"
        )

    lines += ["", "## Per-issue exact-match hits (of injected)", ""]
    all_cols = sorted({c for run in runs for c in _columns(run["records"])})
    col_labels = [f"{i}: {o}.{a}" if a else i for i, o, a in all_cols]
    header = "| run | arm | " + " | ".join(col_labels) + " |"
    lines += [header, "|---" * (len(all_cols) + 2) + "|"]
    for run in runs:
        for arm, label in ARM_LABELS.items():
            cells = []
            for col in all_cols:
                recs = [
                    r for r in run["records"]
                    if r["arm"] == arm
                    and (r["issue_key"], r.get("object_type"), r.get("attribute")) == col
                ]
                cells.append(f"{_ok(recs)}/{len(recs)}" if recs else "—")
            lines.append(f"| {run['run_dir']} | {label} | " + " | ".join(cells) + " |")

    lines += ["", "## Overall metrics per run", ""]
    for run in runs:
        lines += [f"### {run['run_dir']}", ""]
        lines += [
            "| arm | n | coverage | exact (of attempted) | mean conf |",
            "|---|---|---|---|---|",
        ]
        for arm, label in ARM_LABELS.items():
            s = summarize([r for r in run["records"] if r["arm"] == arm])
            lines.append(
                f"| {label} | {s['total']} | {s['coverage']:.0%} | "
                f"{s['exact']}/{s['attempted']} ({s['exact_of_attempted']:.0%}) | "
                f"{s['mean_confidence']:.2f} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    runs = load_runs()
    if not runs:
        print("No runs found under", DEFAULT_RUNS_DIR)
        return
    out = DEFAULT_RUNS_DIR.parent / "summary.md"
    out.write_text(render_summary(runs), encoding="utf-8")
    print(f"{len(runs)} run(s) summarised -> {out}")


if __name__ == "__main__":
    main()
