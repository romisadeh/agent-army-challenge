"""Task A: facts cited in INVESTIGATION.md that are not produced by the other scripts.
Each section is labelled F<n>; out/task_a_facts.txt is the source file for those numbers."""
import pandas as pd

from db import ROOT, connect

con = connect()
con.execute("set TimeZone = 'UTC'")
con.execute("create view p as select distinct * from daily_adset_performance")
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


show("F1 funnel", """select count(*) as all_exec,
    count(*) filter (where response like '%OAuth%') as oauth_failed,
    count(*) filter (where response like '%No budget%') as no_budget,
    count(*) filter (where response = 'SUCCESS') as success,
    count(*) filter (where response = 'SUCCESS' and action_name ilike 'turn off%') as success_kill_rows,
    count(distinct adset_id) filter (where response = 'SUCCESS' and action_name ilike 'turn off%') as killed_adsets,
    count(*) filter (where response = 'SUCCESS' and action_name ilike 'decrease%') as success_cuts,
    count(*) filter (where action_name ilike 'turn on%') as turn_on_rows,
    count(*) filter (where action_name ilike 'turn on%' and response = 'SUCCESS') as turn_on_success
    from rule_executions""")
show("F2 OAuth failures: 06-08 03:00-17:00 UTC vs other times",
     """select (action_time::timestamptz between '2026-06-08 03:00:00+00' and '2026-06-08 17:00:00+00') as in_0608_window, count(*) as n
        from rule_executions where response like '%OAuth%' group by 1 order by 1""")
show("F3 'No budget to change' vs null live-budget read",
     "select current_budget_from_fb is null as fb_budget_null, count(*) as n from rule_executions where response like '%No budget%' group by 1 order by 1")
show("F4 per rule: logged / applied (SUCCESS) / distinct adsets / OAuth failed / no-op",
     """select rule_id, count(*) as logged, count(*) filter (where response = 'SUCCESS') as applied, count(distinct adset_id) as adsets,
          count(*) filter (where response like '%OAuth%') as oauth_failed, count(*) filter (where response like '%No budget%') as no_op
        from rule_executions group by 1 order by 1""")
show("F5 R04: spend at action (all 109 rows) and repeats",
     """select count(*) as rows, count(distinct adset_id) as adsets, round(count(*) / count(distinct adset_id), 1) as rows_per_kill,
          round(median(spend_at_action::double), 2) as median_spend_at_action from rule_executions where rule_id = 'R04'""")
show("F6 R11: firings vs the earlier R03 kill on the same adset",
     """select r.adset_id, r.action_time as r11_time, (select min(action_time) from rule_executions k where k.adset_id = r.adset_id
          and k.rule_id = 'R03' and k.response = 'SUCCESS') as r03_first_kill from rule_executions r where r.rule_id = 'R11'""")
show("F7 own_rsoc adsets in ACC-04 metadata", """select count(*) as acc04_own_rsoc_adsets,
     count(*) filter (where adset_id in (select adset_id from rule_executions)) as acted_on
     from campaign_adset_metadata where account_name = 'ACC-04' and lower(campaign_name) like 'own_rsoc%'""")

im = pd.read_csv(ROOT / "out" / "impact_actions.csv", dtype={"adset_id": str})
cuts, kills = im[im.kind == "cut"], im[im.kind == "kill"]
f8 = pd.DataFrame({
    "cut_hours_local": [sorted(cuts.hour_local.tolist())],
    "cuts_before_09": [int((cuts.hour_local < 9).sum())], "n_cuts": [len(cuts)],
    "share_cut_spend_judged_before_09": [round(cuts[cuts.hour_local < 9].spend_seen.sum() / cuts.spend_seen.sum(), 2)],
    "cuts_matured_better_than_seen": [int((cuts.roi_day_matured > cuts.roi_seen).sum())],
    "cuts_seen_le0_matured_gt0": [int(((cuts.roi_seen <= 0) & (cuts.roi_day_matured > 0)).sum())]})
kb = pd.cut(kills.hour_local, [-1, 5, 11, 17, 23], labels=["00-05", "06-11", "12-17", "18-23"]).value_counts().sort_index()
lines.append(f"== F8 timing of effective actions (from out/impact_actions.csv)\n{f8.T.to_string(header=False)}\n"
             f"kills by local hour block: {kb.to_dict()}\n")
show("F9 rule ROI seen vs matured, R04 (mean gap)", """select round(avg(p.roi::double - r.today_roi_at_action::double), 3) as r04_mean_roi_gap
     from rule_executions r join p on p.adset_id = r.adset_id and p.date = r.action_date where r.rule_id = 'R04'""")
show("F10 (rule, adset, day) groups fired more than once", """select count(*) as groups_fired_more_than_once, sum(n) as rows_in_them from
     (select rule_id, adset_id, action_date, count(*) as n from rule_executions group by all having count(*) > 1)""")
show("F11 account similarity (adset-days with spend)",
     """select p.account_name, round(sum(coalesce(p.revenue::double, 0)) / sum(p.spend::double), 3) as rev_per_spend,
          round(sum(coalesce(p.revenue::double, 0)) filter (where p.spend_day_no::int = 1) / sum(p.spend::double) filter (where p.spend_day_no::int = 1), 3) as rev_per_spend_day1,
          count(*) filter (where p.campaign_name_slg) as slg_spend_rows
        from (select p.*, m.campaign_name like 'slg:%' as campaign_name_slg from p join campaign_adset_metadata m using (adset_id)) p
        where p.spend::double > 0 group by 1 order by 1""")
show("F12 R04-qualifiers (not acted on) that spent the next day, by account",
     """with d as (select p.account_name as acc, p.adset_id, p.date::date as d, p.spend::double as spend, coalesce(p.revenue::double, 0) as rev,
                 p.spend_day_no::int as day_no, m.daily_budget::double / 100 as budget
               from p join campaign_adset_metadata m using (adset_id) where p.spend::double > 0),
        q as (select * from d where day_no = 1 and spend / budget > 0.35 and rev / spend - 1 < -0.5
              and adset_id not in (select adset_id from rule_executions) and d < '2026-06-12')
        select q.acc, count(*) as qualifiers_with_next_day_in_window, count(n.adset_id) as spent_next_day
        from q left join d n on n.adset_id = q.adset_id and n.d = q.d + 1 group by 1 order by 1""")
show("F13 metadata daily_budget / last buyer budget (all accounts)",
     """with last as (select adset_id, new_budget::double as nb from buyer_actions where adset_id is not null and new_budget is not null
                     qualify row_number() over (partition by adset_id order by action_time desc) = 1)
        select count(*) as adsets, count(*) filter (where abs(m.daily_budget::double / l.nb - 100) < 1) as ratio_100
        from last l join campaign_adset_metadata m using (adset_id)""")
show("F14 metadata status of rule-killed adsets", """select m.effective_status, m.delivery_status, count(distinct r.adset_id) as killed_adsets
     from rule_executions r join campaign_adset_metadata m using (adset_id)
     where r.response = 'SUCCESS' and r.action_name ilike 'turn off%' group by 1, 2 order by 3 desc""")
show("F15 budget cuts: rule-log fields vs the engine's own API row (buyer_actions, within 10 s)",
     """select r.rule_id, r.adset_id, r.action_time, r.old_budget, r.new_budget, r.current_budget_from_fb as fb_live, r.set_budget,
          round(r.set_budget::double / r.current_budget_from_fb::double, 3) as set_over_live, b.old_budget as api_old, b.new_budget as api_new,
          b.adset_id is not null as api_row_found
        from rule_executions r left join buyer_actions b on b.adset_id = r.adset_id and b.event_type = 'update_ad_set_budget'
          and abs(date_diff('second', r.action_time::timestamptz, b.action_time::timestamptz)) <= 10
        where r.action_name ilike 'decrease%' and r.response = 'SUCCESS' order by r.action_time""")
show("F16 buyer_actions coverage", "select min(action_time) as first_row, max(action_time) as last_row from buyer_actions")
show("F17 which ROI field matches each rule's threshold (today vs last_3_days)",
     """select rule_id, count(*) as n,
          count(*) filter (where case rule_id when 'R02' then today_roi_at_action::double > -0.30 and today_roi_at_action::double <= -0.10
             when 'R04' then today_roi_at_action::double < -0.5 when 'R10' then today_roi_at_action::double > -0.10 and today_roi_at_action::double <= 0.05
             when 'R12' then today_roi_at_action::double <= -0.5 end) as today_roi_fits,
          count(*) filter (where case rule_id when 'R02' then last_3_days_roi_at_action::double > -0.30 and last_3_days_roi_at_action::double <= -0.10
             when 'R04' then last_3_days_roi_at_action::double < -0.5 when 'R10' then last_3_days_roi_at_action::double > -0.10 and last_3_days_roi_at_action::double <= 0.05
             when 'R12' then last_3_days_roi_at_action::double <= -0.5 end) as l3d_roi_fits
        from rule_executions where rule_id in ('R02', 'R04', 'R10', 'R12') group by 1 order by 1""")
show("F18 R04 firings that miss '< -50%' (2-decimal ROI vs rpc/cpa precision)",
     """select adset_id, action_time, today_roi_at_action as roi_2dp,
          round(today_rpc_at_action::double / today_cpa_at_action::double - 1, 4) as roi_from_rpc_cpa
        from rule_executions where rule_id = 'R04' and not today_roi_at_action::double < -0.5""")
show("F19 R07 reading test: rule-log evaluations with live budget > 65",
     """select rule_id, action_time, adset_id, current_budget_from_fb as fb_live, today_roi_at_action as roi,
          today_roi_at_action::double >= -0.5 as literal_reading_qualifies, today_roi_at_action::double <= -0.5 as flipped_reading_qualifies
        from rule_executions where current_budget_from_fb::double > 65 order by action_time""")
show("F20 buyer_actions same-second repeats", """select count(*) as n_groups, sum(n) as n_rows, sum(n) - count(*) as extra_rows from
     (select adset_id, event_type, date_trunc('second', action_time::timestamptz) as t, new_budget, count(*) as n
      from buyer_actions where adset_id is not null group by all having count(*) > 1)""")
show("F22 budget (current_budget_from_fb) at effective kill, by rule", """with k as (select rule_id, adset_id, coalesce(current_budget_from_fb, new_budget)::double as budget
       from rule_executions where response = 'SUCCESS' and action_name ilike 'turn off%'
       qualify row_number() over (partition by adset_id order by action_time) = 1)
     select rule_id, count(*) as kills, min(budget) as min_budget, median(budget) as median_budget, max(budget) as max_budget
     from k group by rollup (rule_id) order by rule_id nulls last""")
show("F23 no-op / failed executions by rule, adset, day", """select rule_id, adset_id, action_date, left(response, 22) as response, count(*) as n
     from rule_executions where response <> 'SUCCESS' group by all order by n desc""")
r04 = im[im.rule_id == "R04"]
lines.append(f"== F24 R04 effective kills: matured day ROI on the kill day (from out/impact_actions.csv)\n"
             f"kills={len(r04)} matured_roi_gt_0={int((r04.roi_day_matured > 0).sum())} matured_roi_le_0={int((r04.roi_day_matured <= 0).sum())}\n")
show("F25 R03 firings: perf spend_day_no at firing", """select p.spend_day_no::int as perf_spend_day_no, count(*) as r03_firings
     from rule_executions r join p on p.adset_id = r.adset_id and p.date = r.action_date where r.rule_id = 'R03' group by 1 order by 1""")
for a in ("31302925337341", "31314467522499", "31273956645572"):
    show(f"F21 case adset {a}: daily perf", f"""select date, round(spend::double, 2) as spend, round(revenue::double, 2) as revenue,
         round(roi::double, 2) as roi, spend_day_no from p where adset_id = '{a}' order by date""")
    show(f"F21 case adset {a}: every rule + buyer event", f"""select action_time::timestamptz as t, 'buyer:' || event_type as src, old_budget, new_budget,
           null as roi_seen, null as spend_seen from buyer_actions where adset_id = '{a}'
         union all select action_time::timestamptz, 'rule:' || rule_id || ' ' || action_name || ' ' || left(response, 9),
           current_budget_from_fb, set_budget, today_roi_at_action, spend_at_action from rule_executions where adset_id = '{a}' order by 1""")

out = "\n".join(lines)
(ROOT / "out" / "task_a_facts.txt").write_text(out, encoding="utf-8")
print(out)
