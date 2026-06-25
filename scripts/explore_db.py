"""CLI entry point for the OCEL exploration agent.

Usage:
    python scripts/explore_db.py --db data/your_ocel.sqlite
    python scripts/explore_db.py --db data/your_ocel.sqlite --out data/exploration --model qwen2.5-coder:7b
"""

import argparse

from src.exploration.explorer_agent import create_exploration_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OCEL exploration agent on a SQLite database.")
    parser.add_argument("--db", required=True, help="Path to the OCEL SQLite database.")
    parser.add_argument("--out", default="data/exploration", help="Output directory for profile and report.")
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama model to use.")
    args = parser.parse_args()

    report_path = create_exploration_report(
        db_path=args.db,
        output_dir=args.out,
        model=args.model,
    )

    print(f"Exploration report created: {report_path}")


if __name__ == "__main__":
    main()
