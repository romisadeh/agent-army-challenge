"""Load the raw CSVs into an in-memory DuckDB, all columns as VARCHAR (cast explicitly in SQL,
so nothing gets silently coerced on load)."""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
TABLES = ["auto_rules", "buyer_actions", "campaign_adset_metadata",
          "daily_adset_performance", "rule_executions"]


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for t in TABLES:
        path = (ROOT / "data" / f"{t}.csv").as_posix()
        con.execute(f"create table {t} as select * from read_csv('{path}', all_varchar=true)")
    return con


def run_sql_file(con: duckdb.DuckDBPyConnection, name: str):
    return con.execute((ROOT / "sql" / name).read_text(encoding="utf-8")).df()
