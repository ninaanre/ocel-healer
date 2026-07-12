# src/evaluation/summarize_runs.py

"""Cross-run summary of hint evaluations.

Aggregates every data/evaluation/runs/<run>/results.json into one
data/evaluation/summary.md. Uses the strict scores stored in the records at
run time (what would actually commit to the database) — no re-scoring, so the
summary always agrees with each run's own eval_report.md.

Usage:
    python -m src.evaluation.summarize_runs
"""

import json
from pathlib import Path

from src.evaluation.evaluate_hints import summarize

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
        runs.append(data)
    return runs


def _columns(records: list[dict]) -> list[tuple[str, str]]:
    return sorted({(r["object_type"], r["attribute"]) for r in records})


def _ok(records: list[dict]) -> int:
    """Strict hits: proposals that were committed-shaped AND within tolerance."""
    return sum(1 for r in records if r["kind"] == "update" and r["within_tol"])


def render_summary(runs: list[dict]) -> str:
    lines = [
        "# Hints Evaluation — Cross-Run Summary",
        "",
        "Scoring is strict (as stored by each run): a hit is a proposal that "
        "parses as the column's value and lands within tolerance — i.e. a repair "
        "that would actually commit. Unit-suffixed answers count as misses.",
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

    lines += ["", "## Per-column results (hits / injected)", ""]
    all_cols = sorted({c for run in runs for c in _columns(run["records"])})
    header = "| run | arm | " + " | ".join(f"{t}.{a}" for t, a in all_cols) + " |"
    lines += [header, "|---" * (len(all_cols) + 2) + "|"]
    for run in runs:
        for arm, label in ARM_LABELS.items():
            cells = []
            for col in all_cols:
                recs = [
                    r for r in run["records"]
                    if r["arm"] == arm and (r["object_type"], r["attribute"]) == col
                ]
                cells.append(f"{_ok(recs)}/{len(recs)}" if recs else "—")
            lines.append(f"| {run['run_dir']} | {label} | " + " | ".join(cells) + " |")

    lines += ["", "## Overall metrics per run", ""]
    for run in runs:
        lines += [f"### {run['run_dir']}", ""]
        lines += [
            "| arm | n | coverage | exact (att.) | within tol (att.) | within tol (overall) | mean rel. err | mean conf |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for arm, label in ARM_LABELS.items():
            s = summarize([r for r in run["records"] if r["arm"] == arm])
            rel = f"{s['mean_rel_err']:.2f}" if s["mean_rel_err"] is not None else "—"
            lines.append(
                f"| {label} | {s['total']} | {s['coverage']:.0%} | "
                f"{s['exact_of_attempted']:.0%} | {s['within_tol_of_attempted']:.0%} | "
                f"{s['within_tol_overall']:.0%} | {rel} | {s['mean_confidence']:.2f} |"
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
