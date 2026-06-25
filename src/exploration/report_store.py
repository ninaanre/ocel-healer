# src/exploration/report_store.py

from pathlib import Path

DEFAULT_REPORT_PATH = Path("data/exploration/exploration_report.md")


def has_exploration_report(path: str | Path | None = None) -> bool:
    return Path(path or DEFAULT_REPORT_PATH).exists()


def load_exploration_report(path: str | Path | None = None) -> str:
    p = Path(path or DEFAULT_REPORT_PATH)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")
