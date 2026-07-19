# src/exploration/__main__.py

"""CLI for the exploration agent.

Usage:
    python -m src.exploration data/order-management-dirty.sqlite
    python -m src.exploration data/foo.sqlite --model mistral-small3.2:latest

Writes exploration_profile.json, exploration_guide.json and
exploration_report.md into <base-dir>/<db-stem>/ (default: data/exploration/,
which is where the repair agents look hints up).
"""

import argparse
import time
from pathlib import Path

from src.exploration.explorer_agent import explore_database
from src.llm.client import MODEL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="OCEL 2.0 sqlite file to explore")
    parser.add_argument("--model", default=MODEL, help=f"LLM model (default: {MODEL})")
    parser.add_argument(
        "--base-dir",
        default=None,
        help="artifact directory (default: <db_dir>/exploration — where repair hints are looked up)",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir) if args.base_dir else Path(args.db_path).parent / "exploration"

    t0 = time.time()

    def progress(name: str, i: int, total: int) -> None:
        print(f"[{i}/{total}] {name} ({time.time() - t0:.0f}s)", flush=True)

    report = explore_database(
        args.db_path, model=args.model, base_dir=base_dir, on_progress=progress
    )
    print(f"Done in {time.time() - t0:.0f}s -> {report}")


if __name__ == "__main__":
    main()
