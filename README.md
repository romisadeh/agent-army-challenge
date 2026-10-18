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

Task A (investigation): see the Reproduce section of [INVESTIGATION.md](INVESTIGATION.md).

Task C (Adset Decision Agent, shadow mode; outputs in `out/`):
```bash
.venv/Scripts/python src/agent/run.py --dry-run          # cards + routes, no LLM          -> out/routes_dry.txt
.venv/Scripts/python src/agent/run.py --replay           # the agent from stored responses (no API key needed) -> out/decisions.jsonl
.venv/Scripts/python src/agent/run.py --no-llm           # ablation: code rule instead of the LLM -> out/decisions_nollm.jsonl
.venv/Scripts/python src/agent/run.py --snapshot-pass --replay   # partial-day cases (RESULTS §5.3) -> out/snapshot_pass.txt
.venv/Scripts/python src/agent/run.py --replay --prompt v3 --tag prompt_v3            # prompt revision (RESULTS §4)
.venv/Scripts/python src/agent/run.py --replay --ds-min 0.25 --tag sens_ds025         # sensitivity check (RESULTS §3.2)
.venv/Scripts/python src/agent/compare.py                # evaluation + comparison -> out/evaluation.txt, out/comparison.txt
```
Without `--replay`, run.py calls the Anthropic API (needs `ANTHROPIC_API_KEY`) and stops at $3 per run. The stored raw responses are in `out/llm_raw/`, so every result reproduces at $0.

`ANTHROPIC_API_KEY` must be set in the environment for the agent step (never stored in the repo).

## Layout
- `data/` — the original CSVs, untouched
- `sql/` — investigation queries (DuckDB)
- `src/` — Python: loading/cleaning, investigation, agent
- `out/` — generated outputs (decisions, cost log)
- `out/*_dev*`, `out/*v3dev*`, `out/*v3b_dev*` — the 06-10 development iterations from RESULTS §4 (the 5-card smoke test, prompt v3 and the rejected v3b); `out/*_sens_*` is the sensitivity check (RESULTS §3.2); `out/llm_raw/` holds every stored LLM response, which is what makes `--replay` work without an API key

## Corners cut
- Task A: the items listed as “seen but not investigated” in INVESTIGATION.md §5 (Meta conversions > clicks, ROI > 5, naming fields beyond the `own_rsoc`/`slg:` prefix) were not used. The kill counterfactual is weak at the single-adset level (validation n = 13), so R04’s impact is reported as not measurable rather than as a number.
- Task B: the architecture’s estimates were tested only in part by Task C, which ran one window, not three. Measured LLM cost was $0.21–0.33 per window (vs $0.47/day estimated for three) and approvals were 9 in three days (vs the 15–25/day assumed). The Executor, kill switch, alerts and rollout plan are design only.
- Task C decides at one window (10:00) on matured data only. The architecture's 14:00 window is demonstrated on 4 snapshot cases, not at scale (RESULTS §5.3).
- No Meta status read: the agent can't see paused adsets (RESULTS §3.4).
- Budgets are reconstructed from the logs on most cards; the deterministic route never fired this week.
- Lessons are written by a template and don't expire automatically; the feedback loop is a stub apart from grading and calibration.
- A network failure that outlasts the SDK retries stops the run instead of escalating that card.

## Time spent
About 9.5 hours in total. Of that, ~8 hours were reconstructed afterwards from timestamps (Claude Code session transcripts, git commits, file modification times, the API cost log), not tracked live; sessions are split wherever there was a gap of more than 2 hours. Time spent driving Claude Code counts as my time. The other ~1.5 hours are my own estimate of thinking time outside those sessions.

| Task | From timestamps | Added (my estimate) | Total |
|---|---|---|---|
| A: investigation | ~2 | +1 (reading on industry practice, planning and checking before and after the sessions) | ~3 |
| B: architecture | ~1 | +0.5 (planning and thinking about the design) | ~1.5 |
| C: agent build, runs and evaluation | ~3.5 | | ~3.5 |
| Write-up (RESULTS.md, final review) | ~1.5 | | ~1.5 |

The AI usage log (DECISIONS.md) was written during all of the above, so it has no separate figure.
