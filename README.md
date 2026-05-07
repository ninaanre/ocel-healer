# OCEL-Healer

LLM-based data quality repair for object-centric event logs.

## Goal

This project investigates how data quality issues in OCELs can be detected and repaired using rule-based checks and LLM-based agents.

## Structure

- `src/` – implementation
- `data/` – raw, synthetic, and processed logs
- `notebooks/` – exploratory analysis
- `experiments/` – experiment scripts and results

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt