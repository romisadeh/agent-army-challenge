-- Task A, step 1: ROI the rule saw at action time vs. what the same (adset, day) matured to.
-- Join key: rule_executions.action_date = perf.date. action_date (not the UTC date of action_time)
-- because action_time rows at 21:00-23:59 UTC already carry the next action_date -> action_date is the
-- reporting-day (UTC+3) and matches perf.date. Perf is de-duplicated first (72 exact dup rows on 2026-06-09).

with perf as (
    select distinct adset_id, date, spend::double as spend, revenue::double as revenue,
           roi::double as roi, spend_day_no::int as spend_day_no
    from daily_adset_performance
),
r as (
    select row_number() over (order by action_time) as exec_no,
           action_date, action_time, rule_id, rule_name, action_name, adset_id, response,
           old_budget::double as old_budget, new_budget::double as new_budget,
           spend_at_action::double as spend_seen,
           today_roi_at_action::double as roi_seen,
           total_days_at_action::int as total_days_seen,
           last_3_days_roi_at_action::double as l3d_roi_seen
    from rule_executions
)
select r.exec_no, r.action_date, r.action_time, r.rule_id, r.action_name, r.adset_id,
       r.response = 'SUCCESS' as applied,
       r.spend_seen, p.spend as spend_final,
       round(r.spend_seen / nullif(p.spend, 0), 2) as share_of_day_spend_seen,
       round(r.spend_seen * (1 + r.roi_seen), 2) as revenue_seen_implied,
       p.revenue as revenue_final,
       r.roi_seen, p.roi as roi_final,
       round(p.roi - r.roi_seen, 4) as roi_delta,
       r.total_days_seen, p.spend_day_no
from r
left join perf p on p.adset_id = r.adset_id and p.date = r.action_date
order by abs(p.roi - r.roi_seen) desc nulls last;
