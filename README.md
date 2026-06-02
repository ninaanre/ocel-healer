# OCEL-Healer

LLM-based data quality repair for object-centric event logs.

## Goal

This project investigates how data quality issues in OCELs can be detected and repaired using rule-based checks and LLM-based agents.

## Structure

- `src/` – implementation
    - `detection/` - detection of quality issues
    - `repair/` - fix of quality issues
    - `llm/` - llm integration
- `data/` – object-centric event logs

## Dashboard

Running the dashboard of the ocel-healer is possible via executing the command `marimo run src/dashboard.py` in a terminal.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate```