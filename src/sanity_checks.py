"""Task A follow-up checks (round 2, after review of the first profiling pass).
  1. Perf row reconciliation (dedup vs raw).
  2. Does spend continue after a successful Turn OFF? Reporting lag vs. turn-off not applied.
  3. Would rule conditions have fired on accounts other than ACC-04? (calibrated on ACC-04 first)
Writes out/sanity_checks.txt."""
from db import ROOT, connect

con = connect()
con.execute("set TimeZone = 'UTC'")  # hours_left math assumes UTC; machine TZ (Israel) shifted it by 3h
con.execute("create view p as select distinct * from daily_adset_performance")
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


# ---- 1. reconciliation --------------------------------------------------------------------
show("1. perf rows by revenue-null x spend>0 (deduped)",
     "select revenue is null as rev_null, spend::double > 0 as spend_pos, count(*) as n, "
     "count(*) filter (where revenue::double <> 0) as rev_nonzero, round(sum(revenue::double), 2) as revenue "
     "from p group by 1, 2 order by 1, 2")

# ---- 2. spend after successful turn-off -----------------------------------------------------
con.execute("""
create view off as
select r.adset_id, r.action_date, r.action_time::timestamptz as t_off, r.rule_id,
       r.spend_at_action::double as spend_seen, p.spend::double as spend_final,
       p.spend::double - r.spend_at_action::double as spend_after,
       -- hours of reporting-day left after the kill (reporting day = UTC+3)
       date_diff('minute', r.action_time::timestamptz,
                 (r.action_date::date + interval 1 day - interval 3 hour)::timestamptz) / 60.0 as hours_left
from rule_executions r
join p on p.adset_id = r.adset_id and p.date = r.action_date
where r.action_name ilike 'turn off%' and r.response = 'SUCCESS'
qualify row_number() over (partition by r.adset_id, r.action_date order by r.action_time) = 1
""")
show("2a. first successful Turn OFF per adset-day: spend after the kill",
     "select count(*) as n_kills, count(*) filter (where spend_after > 0.001) as n_spend_after, "
     "round(sum(spend_after), 2) as total_spend_after, round(median(spend_after / nullif(spend_final, 0)), 2) as med_share_after "
     "from off")
show("2b. spend after kill vs hours left in the day (if the kill failed, spend_after should grow with hours_left)",
     "select case when hours_left < 6 then 'a <6h' when hours_left < 12 then 'b 6-12h' when hours_left < 18 then 'c 12-18h' else 'd 18h+' end as hours_left_bucket, "
     "count(*) as n, round(avg(spend_after), 3) as avg_spend_after, round(max(spend_after), 3) as max_spend_after, "
     "round(avg(spend_after / nullif(spend_final, 0)), 2) as avg_share_after from off group by 1 order by 1")
show("2c. later log rows for the same adset-day after the kill: does spend_at_action keep rising?",
     """select o.adset_id, o.action_date, o.rule_id as kill_rule, o.t_off, l.action_time, l.rule_id, l.response,
               o.spend_seen as spend_at_kill, l.spend_at_action::double as spend_later, o.spend_final,
               round(date_diff('minute', o.t_off, l.action_time::timestamptz) / 60.0, 1) as hours_after_kill
        from off o join rule_executions l on l.adset_id = o.adset_id and l.action_date = o.action_date
         and l.action_time::timestamptz > o.t_off
        order by o.adset_id, l.action_time""")
show("2d. adsets with a successful Turn OFF that spend on any LATER date",
     "select count(distinct o.adset_id) as killed_adsets, count(distinct p.adset_id) as spent_later "
     "from off o left join p on p.adset_id = o.adset_id and p.date > o.action_date and p.spend::double > 0")

# ---- 3. would the conditions qualify outside ACC-04? ------------------------------------------
# End-of-day approximation of rules whose name contains testable thresholds. Rules evaluate intraday,
# so EOD qualification is an approximation (calibrated on ACC-04 below). Budget = metadata daily_budget / 100
# (current snapshot, not historical). positive_days / total profit only counted inside the 7-day window.
con.execute("""
create view d as
select p.account_name, p.adset_id, p.date, p.spend::double as spend, p.roi::double as roi,
       p.profit::double as profit, p.spend_day_no::int as day_no, m.daily_budget::double / 100 as budget,  -- metadata budget is in cents (x100 vs rule log + buyer log, all accounts)
       coalesce(lower(m.campaign_name) like 'own_rsoc%', false) as own_rsoc,
       sum(case when p.roi::double > 0 then 1 else 0 end) over w as positive_days_in_window,
       sum(p.profit::double) over w as profit_in_window,
       p.first_spend_date >= '2026-06-06' as full_history_in_window
from p left join campaign_adset_metadata m using (adset_id)
where p.spend::double > 0
window w as (partition by p.adset_id order by p.date rows between unbounded preceding and current row)
""")
con.execute("""
create view qual as
select *,
  roi > -0.30 and roi <= -0.10                                        as r02,
  positive_days_in_window = 0 and day_no > 2 and full_history_in_window as r03,
  day_no = 1 and spend / nullif(budget, 0) > 0.35 and roi < -0.50     as r04,
  profit_in_window <= -2.5 and spend / nullif(budget, 0) >= 0.15
      and full_history_in_window                                      as r05,
  roi > -0.10 and roi <= 0.05 and budget >= 100                       as r10,
  roi <= -0.50 and budget <= 65                                       as r12
from d
""")
show("3a. CALIBRATION on ACC-04: adset-days qualifying (EOD approx) vs adset-days the rule actually fired",
     """with fired as (select rule_id, count(distinct adset_id || action_date) as n_fired from rule_executions group by 1)
        select q.rule_id, q.n_qualify, f.n_fired, q.n_qualify_and_fired from (
          select 'R02' as rule_id, count(*) filter (where r02) as n_qualify,
                 count(*) filter (where r02 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R02')) as n_qualify_and_fired from qual where account_name='ACC-04'
          union all select 'R03', count(*) filter (where r03), count(*) filter (where r03 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R03')) from qual where account_name='ACC-04'
          union all select 'R04', count(*) filter (where r04), count(*) filter (where r04 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R04')) from qual where account_name='ACC-04'
          union all select 'R05', count(*) filter (where r05), count(*) filter (where r05 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R05')) from qual where account_name='ACC-04'
          union all select 'R10', count(*) filter (where r10), count(*) filter (where r10 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R10')) from qual where account_name='ACC-04'
          union all select 'R12', count(*) filter (where r12), count(*) filter (where r12 and (adset_id, date) in (select adset_id, action_date from rule_executions where rule_id='R12')) from qual where account_name='ACC-04'
        ) q left join fired f using (rule_id) order by 1""")
show("3b. adset-days qualifying per account (all adsets | own_rsoc-named only)",
     """select account_name, count(*) as spend_days,
          count(*) filter (where r02) as r02, count(*) filter (where r03) as r03, count(*) filter (where r04) as r04,
          count(*) filter (where r05) as r05, count(*) filter (where r10) as r10, count(*) filter (where r12) as r12,
          count(*) filter (where own_rsoc) as own_rsoc_spend_days,
          count(*) filter (where own_rsoc and (r02 or r03 or r04 or r05 or r10 or r12)) as own_rsoc_any_qualify
        from qual group by 1 order by 1""")
show("3c. naming of adsets the rules acted on vs ACC-04 own_rsoc adsets",
     """select coalesce(lower(m.campaign_name) like 'own_rsoc%', false) as own_rsoc, split_part(m.campaign_name, ':', 1) as name_prefix,
          count(distinct r.adset_id) as adsets_acted_on
        from rule_executions r join campaign_adset_metadata m using (adset_id) group by 1, 2""")

out = "\n".join(lines)
(ROOT / "out" / "sanity_checks.txt").write_text(out, encoding="utf-8")
print(out)
