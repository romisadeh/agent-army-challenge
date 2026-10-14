# Dataset — README

Snapshot exports from a production media-buying system. One week of activity, six Meta ad accounts (`ACC-01` … `ACC-06`).

As stated in the brief: this is production-grade data. It is not guaranteed to be clean, consistent, or complete. Treat it accordingly.

## Files

### `daily_adset_performance.csv`
Daily adset performance as reported by the internal revenue pipeline.

| column | meaning |
|---|---|
| adset_id | adset identifier |
| fb_ad_account_id | ad account id |
| account_name | ad account label |
| date | reporting date |
| spend, impressions, clicks, revenue | daily totals |
| fb_conversions | conversions as reported by Meta |
| estimated_conversions | conversions per the internal revenue attribution model |
| profit | revenue − spend |
| roi | profit / spend |
| rpc | revenue per estimated conversion |
| cpa | spend per estimated conversion |
| ctr, cr | click-through rate, conversion rate |
| first_spend_date | first date the adset ever spent |
| spend_day_no | how many spend-days old the adset is on this row's date |

### `campaign_adset_metadata.csv`
Campaign/adset configuration: status, budgets, bid strategy, targeting, objective, creation time. Naming conventions encode operational metadata (channel, vertical slug, buyer, bid type, etc.).

### `auto_rules.csv`
The 12 automation rules currently active, with their action, schedule and observed firing counts during the week. Rule logic is encoded in the rule name (this is how it is in the real system too).

### `rule_executions.csv`
Full log of rule engine evaluations that resulted in an action during the week. Key columns: `action_time` (execution time) and `action_date` (reporting date), rule/condition/action identifiers, budget values as recorded by the reporting system (`old_budget`, `new_budget`), the target budget the rule attempted to set (`set_budget`), and the live budget read from Meta (`current_budget_from_fb`), the metrics the rule engine saw at evaluation time (`spend_at_action`, `today_roi_at_action`, `total_spend_at_action`, `last_3_days_roi_at_action`, …), `budget_level`, and the API `response`.

### `buyer_actions.csv`
Log of manual actions taken by media buyers through the internal UI during the same week: timestamp, adset, event type, old/new budget, and an optional free-text note.

## Metric conventions

- **ROI is a ratio, not a percentage**: `roi = profit / spend`. So `0.52` = +52% return, `0` = break-even, `-0.30` = lost 30% of spend, `-1.0` = spent with zero revenue. The same convention applies to `today_roi_at_action` and the other `*_roi_at_action` columns in `rule_executions.csv`, and to `roas_target` in the metadata. ROI thresholds inside rule names (e.g. `ROI <= -50`) are written as percentages — `-50` there means a ratio of `-0.50`.
- **ctr** and **cr** are fractions as well: `0.1077` = 10.77%.
- **estimated_conversions** can be fractional — the attribution model splits credit across sources. `cpa` and `cr` are computed from estimated conversions.
- All monetary columns (`spend`, `revenue`, `profit`, budgets, `rpc`, `cpa`) are USD.
- `rpc` = revenue per estimated conversion; `cpa` = spend per estimated conversion.

## Notes

- Adset identifiers may appear in more than one format across systems. Welcome to production.
- You may load everything into PostgreSQL/SQLite or work with the files directly — your choice.
