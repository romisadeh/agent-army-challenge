"""Task A step 1: ROI seen by each rule execution vs. matured same-day ROI. Writes out/rule_roi_delta.csv."""
from db import ROOT, connect, run_sql_file

df = run_sql_file(connect(), "02_rule_roi_delta.sql")
df.to_csv(ROOT / "out" / "rule_roi_delta.csv", index=False)
print(f"{len(df)} executions, {df['roi_final'].notna().sum()} matched to perf")
print(df.head(25).to_string(index=False))
