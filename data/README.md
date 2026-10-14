# data/

The raw CSVs are **not included** in this repository. They were provided with the assignment brief and are not ours to publish.

To run the scripts, place these five files in this folder (`data/`), with exactly these names:

| File | Used by |
|---|---|
| `auto_rules.csv` | all scripts (via `src/db.py`) |
| `buyer_actions.csv` | all scripts |
| `campaign_adset_metadata.csv` | all scripts |
| `daily_adset_performance.csv` | all scripts |
| `rule_executions.csv` | all scripts |

`README_DATA.md` (the column documentation that came with the data) is already here.

`src/db.py` loads every file from `data/<name>.csv` into an in-memory DuckDB. A missing file stops all scripts. Outputs go to `out/`, which is committed so the results can be read without re-running anything.
