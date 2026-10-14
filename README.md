# Agent Army Challenge

## Deliverables
- [INVESTIGATION.md](INVESTIGATION.md) — Task A: what the auto-rules did, impact, data issues
- [ARCHITECTURE.md](ARCHITECTURE.md) — Task B: agent system design
- [RESULTS.md](RESULTS.md) — Task C: agent vs rules vs buyers, evaluation, weaknesses
- [DECISIONS.md](DECISIONS.md) — Task D: AI usage log

## How to run
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock.txt   # Windows; use .venv/bin/python on macOS/Linux
```
<!-- per-step commands to be added as scripts are written -->

`ANTHROPIC_API_KEY` must be set in the environment for the agent step (never stored in the repo).

## Layout
- `data/` — the original CSVs, untouched
- `sql/` — investigation queries (DuckDB)
- `src/` — Python: loading/cleaning, investigation, agent
- `out/` — generated outputs (decisions, cost log)

## Corners cut
<!-- fill in at the end -->

## Time spent
<!-- fill in at the end -->
