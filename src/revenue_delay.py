"""Task A: characterize revenue delay. Writes out/revenue_delay.txt.

Measure: for each rule execution, what the engine saw for TODAY vs the final perf row for that day.
  spend_share   = spend_seen / spend_final              (how far through the day's delivery)
  revenue_share = revenue_seen / revenue_final          (how much of the day's revenue was visible)
  roas_complete = (revenue_seen/spend_seen) / (revenue_final/spend_final)
                  -> revenue per $ visible vs final. 1.0 = no lag. This separates "revenue arrives late"
                     from "the day isn't over yet" (assumes revenue-per-$ is roughly flat within the day).
revenue_seen = spend * rpc / cpa when both present (4-decimal precision), else spend * (1 + roi) (roi has 2 decimals).
Hour = reporting-day hour in UTC+3.
"""
from db import ROOT, connect

con = connect()
con.execute("set TimeZone = 'UTC'")
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


con.execute("""create view p as select distinct adset_id, date::date as d, spend::double as spend,
               coalesce(revenue::double, 0) as revenue from daily_adset_performance""")
con.execute("""
create view x as
select row_number() over (order by r.action_time) as ex, r.adset_id, r.action_date::date as ad, r.rule_id, r.response,
       (hour(r.action_time::timestamptz) + 3) % 24 as hour_local,
       r.response <> 'SUCCESS' or r.action_name ilike 'turn on%' as unaffected,   -- failed / no-op: action did not change outcome
       r.spend_at_action::double as spend_seen,
       case when r.today_rpc_at_action is not null and r.today_cpa_at_action::double > 0
            then r.spend_at_action::double * r.today_rpc_at_action::double / r.today_cpa_at_action::double
            else r.spend_at_action::double * (1 + r.today_roi_at_action::double) end as rev_seen,
       p.spend as spend_final, p.revenue as rev_final,
       row_number() over (partition by r.adset_id, r.action_date order by r.action_time) = 1 as first_of_day
from rule_executions r join p on p.adset_id = r.adset_id and p.d = r.action_date::date
""")
BUCKET = ("case when hour_local < 6 then 'a 00-06' when hour_local < 12 then 'b 06-12' "
          "when hour_local < 18 then 'c 12-18' else 'd 18-24' end")
AGG = ("count(*) as n, count(distinct adset_id) as adsets, round(sum(spend_seen) / sum(spend_final), 2) as spend_share, "
       "round(sum(rev_seen) / sum(rev_final), 2) as revenue_share, "
       "round((sum(rev_seen) / sum(spend_seen)) / (sum(rev_final) / sum(spend_final)), 2) as roas_complete, "
       "round(median((rev_seen / spend_seen) / (rev_final / spend_final)), 2) as med_roas_complete")
FILT = "spend_final >= 1 and rev_final > 0 and spend_seen > 0"   # drop micro-spend noise ($0.01-revenue rows)

show(f"1a. by local hour, ALL executions (one per adset-day, first firing), {FILT}",
     f"select {BUCKET} as hour_local, {AGG} from x where first_of_day and {FILT} group by 1 order by 1")
show("1b. by local hour, UNAFFECTED only (failed / no-op / turn-on attempts: action didn't change the outcome)",
     f"select {BUCKET} as hour_local, {AGG} from x where unaffected and {FILT} group by 1 order by 1")
show("1c. by local hour, ALL executions incl. repeats",
     f"select {BUCKET} as hour_local, {AGG} from x where {FILT} group by 1 order by 1")
show("1d. intraday series, one adset (31626016833981, 2026-06-09: 13 failed R02 attempts = untouched day)",
     "select hour_local, round(spend_seen, 2) as spend_seen, round(rev_seen, 2) as rev_seen, round(spend_seen / spend_final, 2) as spend_share, "
     "round(rev_seen / rev_final, 2) as revenue_share, round((rev_seen / spend_seen) / (rev_final / spend_final), 2) as roas_complete "
     "from x where adset_id = '31626016833981' and ad = '2026-06-09' order by ex")
show("1e. is the FINAL perf row complete? executions where spend_seen > spend_final (perf day ended before the engine's reading)",
     "select ad, count(*) as n, count(*) filter (where spend_seen > spend_final + 0.01) as seen_gt_final, "
     "round(max(spend_seen - spend_final), 2) as max_excess from x group by 1 order by 1")
show("1f. revenue on zero-spend days (revenue booked after delivery stopped)",
     """select z.adset_id, z.d, round(z.revenue, 4) as revenue_on_zero_spend_day,
          (select max(d) from p where p.adset_id = z.adset_id and p.d < z.d and p.spend > 0) as last_spend_day,
          (select round(revenue, 2) from p where p.adset_id = z.adset_id and p.d = z.d - 1) as revenue_prev_day,
          (select round(spend, 2) from p where p.adset_id = z.adset_id and p.d = z.d - 1) as spend_prev_day
        from p z where z.spend = 0 and z.revenue <> 0 order by z.d""")
show("1g. daily revenue per $ (perf snapshot): does the last day look under-reported?",
     "select d, round(sum(spend)) as spend, round(sum(revenue)) as revenue, round(sum(revenue) / sum(spend), 3) as rev_per_spend "
     "from p group by 1 order by 1")
show("1h. last_3_days_revenue_at_action vs matching perf window (field doesn't match its name)",
     """select count(*) as n_with_both, round(median(r.last_3_days_revenue_at_action::double /
          nullif((select sum(revenue) from p where p.adset_id = r.adset_id and p.d between r.action_date::date - 3 and r.action_date::date - 1), 0)), 2) as median_ratio_l3rev,
          round(median(r.last_3_days_spend_at_action::double /
          nullif((select sum(spend) from p where p.adset_id = r.adset_id and p.d between r.action_date::date - 3 and r.action_date::date - 1), 0)), 3) as median_ratio_l3spend
        from rule_executions r where r.action_date >= '2026-06-09' and r.last_3_days_revenue_at_action is not null
          and r.last_3_days_spend_at_action is not null""")

out = "\n".join(lines)
(ROOT / "out" / "revenue_delay.txt").write_text(out, encoding="utf-8")
print(out)
