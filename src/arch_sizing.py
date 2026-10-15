"""Task B: volume, concentration and LLM-cost numbers cited in ARCHITECTURE.md.
Each section is labelled S<n>; out/arch_sizing.txt is the source file for those numbers."""
from db import ROOT, connect

con = connect()
con.execute("""create view p as select distinct adset_id, account_name, date::date as d, spend::double as spend,
    profit::double as profit, spend_day_no::int as age from daily_adset_performance where spend::double > 0""")
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


show("S1 spending adsets, spend and profit per day", """select d, count(distinct adset_id) as adsets, round(sum(spend)) as spend,
    round(sum(profit)) as profit, count(*) filter (where age = 1) as day1_adsets from p group by 1 order by 1""")
show("S2 spend concentration: adset-days/day at or above a spend level, and their share of spend",
     """select t as min_spend, round(count(*) filter (where spend >= t) / 7.0, 1) as adset_days_per_day,
          round(sum(spend) filter (where spend >= t) / sum(spend), 2) as share_of_spend,
          round(median(spend), 2) as median_adset_day_spend
        from p, (values (1.0), (5.0), (20.0), (50.0), (100.0)) v(t) group by t order by t""")
show("S3 per account: spend, profit, share of spend, buyer ui_* actions, spending adsets on the last day",
     """with acc as (select account_name, round(sum(spend)) as spend, round(sum(profit)) as profit,
                      round(sum(spend) / (select sum(spend) from p), 2) as spend_share,
                      count(distinct adset_id) filter (where d = (select max(d) from p)) as adsets_last_day
                    from p group by 1),
          ids as (select distinct adset_id, account_name from daily_adset_performance),
          ui as (select i.account_name, count(*) as ui_actions from buyer_actions b join ids i using (adset_id)
                 where b.event_type like 'ui_%' group by 1)
        select acc.*, ui.ui_actions from acc left join ui using (account_name) order by 1""")
show("S4 buyer actions per day (ui_* = safely human) and share with a note",
     """select action_time[1:10] as d, count(*) filter (where event_type like 'ui_%') as ui_actions,
          count(*) filter (where note is not null and trim(note) <> '') as with_note from buyer_actions group by 1 order by 1""")
show("S5 currently ACTIVE adsets (delivery_status) by budget band (metadata daily_budget / 100)",
     """select case when b < 20 then 'a <$20' when b < 50 then 'b $20-50' when b < 100 then 'c $50-100' else 'd >=$100' end as band,
          count(*) as adsets from (select try_cast(daily_budget as double) / 100 as b from campaign_adset_metadata
                                   where delivery_status = 'ACTIVE') group by 1 order by 1""")
show("S7 buyer-lock reach: adsets with a ui_* action per day, and their share of that day's spend",
     """with ui as (select distinct adset_id, action_time[1:10]::date as d from buyer_actions where event_type like 'ui_%' and adset_id is not null)
        select (select round(avg(n), 1) from (select count(*) as n from ui group by d)) as touched_adsets_per_day,
          round(sum(p.spend) filter (where ui.adset_id is not null) / sum(p.spend), 2) as share_of_spend_touched_same_day
        from p left join ui on ui.adset_id = p.adset_id and ui.d = p.d""")

# S6: LLM cost model. Prices: $/1M tokens (claude-api skill, cached 2026-10-06). Cache read = 0.1x input.
PRICE = {"haiku-5-5": (0.10, 0.50), "sonnet-5-5": (2.00, 10.00), "opus-5-5": (4.00, 20.00)}
SYS, CARD = 2500, 1200          # cached policy prompt, per-card context (card + peers + lessons)
OUT = {"haiku-5-5": 600, "sonnet-5-5": 1500, "opus-5-5": 1500}   # output incl. thinking


def call_cost(model):
    pin, pout = PRICE[model]
    return (SYS * pin * 0.1 + CARD * pin + OUT[model] * pout) / 1e6


plan = [("Haiku: routed cards, 3 windows", "haiku-5-5", 3 * 60),
        ("Sonnet: second opinions", "sonnet-5-5", 20),
        ("Sonnet: daily digest (8k in / 2k out)", None, 1)]
rows, total = [], 0.0
for label, model, n in plan:
    c = n * call_cost(model) if model else (8000 * 2.0 + 2000 * 10.0) / 1e6
    total += c
    rows.append(f"  {label:<42} calls/day={n:>4}  cost/day=${c:.3f}")
rows.append(f"  {'POC total':<42} {'':>14}  cost/day=${total:.3f}  (budget $30)")
for model in PRICE:
    rows.append(f"  stress: {model} on EVERY spending adset, 3 windows (~300x3 calls): ${900 * call_cost(model):.2f}/day")
lines.append("== S6 LLM cost model (constants in this file)\n" + "\n".join(rows) + "\n")

out = "\n".join(lines)
(ROOT / "out" / "arch_sizing.txt").write_text(out, encoding="utf-8")
print(out)
