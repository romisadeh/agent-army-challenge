"""Task A: (2) losers that ran — negative week profit, never touched by a rule; (4) buyer reversals of rule actions.
Writes out/losers_and_reversals.txt and out/losers_untouched.csv."""
from db import ROOT, connect

con = connect()
con.execute("set TimeZone = 'UTC'")
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


con.execute("""create view p as select distinct adset_id, account_name, date::date as d, spend::double as spend,
               coalesce(revenue::double, 0) as revenue, coalesce(profit::double, 0) as profit from daily_adset_performance""")
con.execute("""
create view wk as
select adset_id, any_value(account_name) as account_name,
       sum(spend) as spend, sum(revenue) as revenue, sum(profit) as profit,
       count(*) filter (where spend > 0) as spend_days,
       count(*) filter (where spend > 0 and profit < 0) as neg_days,
       sum(profit) filter (where d < '2026-06-12') as profit_excl_last_day,   -- sensitivity: last day may be incomplete
       adset_id in (select adset_id from rule_executions) as rule_touched,
       adset_id in (select adset_id from buyer_actions where adset_id is not null) as buyer_touched
from p group by adset_id having sum(spend) > 0
""")
con.execute("create view losers as select * from wk where profit < 0")

show("2a. week totals by account: all spending adsets vs losers (week profit < 0) vs losers no rule touched",
     """select account_name,
          count(*) as spend_adsets, round(sum(spend)) as spend, round(sum(profit)) as profit,
          count(*) filter (where profit < 0) as losers, round(sum(spend) filter (where profit < 0)) as loser_spend,
          round(sum(profit) filter (where profit < 0)) as loser_loss,
          count(*) filter (where profit < 0 and not rule_touched) as untouched_losers,
          round(sum(spend) filter (where profit < 0 and not rule_touched)) as untouched_spend,
          round(sum(profit) filter (where profit < 0 and not rule_touched)) as untouched_loss,
          count(*) filter (where profit < 0 and not rule_touched and not buyer_touched) as untouched_by_anyone
        from wk group by rollup (account_name) order by account_name nulls last""")
show("2b. SUSTAINED untouched losers: >= 3 spend days, loss on most of them, week loss >= $5",
     """select account_name, count(*) as n, round(sum(spend)) as spend, round(sum(profit)) as loss,
          round(sum(profit_excl_last_day)) as loss_excl_last_day, count(*) filter (where buyer_touched) as buyer_touched
        from losers where not rule_touched and spend_days >= 3 and neg_days * 2 > spend_days and profit <= -5
        group by rollup (account_name) order by account_name nulls last""")
show("2b-list. the sustained untouched losers",
     """select adset_id, account_name, spend_days, neg_days, round(spend, 2) as spend, round(profit, 2) as profit,
          round(profit / spend, 3) as roi, buyer_touched
        from losers where not rule_touched and spend_days >= 3 and neg_days * 2 > spend_days and profit <= -5
        order by profit""")
show("2c. top 15 untouched losers by loss",
     """select adset_id, account_name, spend_days, neg_days, round(spend, 2) as spend, round(profit, 2) as profit,
          round(profit / spend, 3) as roi, round(profit_excl_last_day, 2) as profit_excl_last_day, buyer_touched
        from losers where not rule_touched order by profit limit 15""")
show("2d. how deep: untouched losers by week ROI band",
     """select case when profit / spend <= -0.5 then 'a roi <= -50%' when profit / spend <= -0.3 then 'b -50..-30%'
                 when profit / spend <= -0.1 then 'c -30..-10%' else 'd -10..0%' end as roi_band,
          count(*) as n, round(sum(spend)) as spend, round(sum(profit)) as loss
        from losers where not rule_touched group by 1 order by 1""")
con.execute("""copy (select * from losers where not rule_touched order by profit)
               to '""" + (ROOT / "out" / "losers_untouched.csv").as_posix() + "' (header)")

# ---- 4. buyer reversals ------------------------------------------------------------------------
# buyer_actions has only budget events (no status/on-off events), so a manual re-enable is NOT observable.
# Observable reversal: a buyer budget increase on the same adset after a rule decrease/turn-off.
show("4a. buyer event types (is a status change even loggable?)",
     "select object_type, event_type, count(*) as n from buyer_actions group by 1, 2 order by 1, 2")
show("4b. buyer actions on adsets AFTER a rule acted on them",
     """with ra as (select adset_id, min(action_time::timestamptz) as first_rule_t,
                          string_agg(distinct rule_id || ':' || action_name || ':' || response[1:7], ' | ') as rule_actions
                   from rule_executions group by 1)
        select b.adset_id, ra.rule_actions, ra.first_rule_t, b.action_time, b.event_type, b.old_budget, b.new_budget,
               round(date_diff('minute', ra.first_rule_t, b.action_time::timestamptz) / 60.0, 1) as hours_after_rule, b.note
        from buyer_actions b join ra on ra.adset_id = b.adset_id
        where b.action_time::timestamptz > ra.first_rule_t order by b.adset_id, b.action_time""")
show("4c. buyer actions on rule-touched adsets BEFORE the rule (context)",
     """select count(*) as n, count(distinct b.adset_id) as adsets from buyer_actions b
        join (select adset_id, min(action_time::timestamptz) as t from rule_executions group by 1) ra
        on ra.adset_id = b.adset_id where b.action_time::timestamptz <= ra.t""")

show("4d. buyer_actions rows that are actually the rule engine (same adset, within 120 s of a rule execution)",
     """select b.event_type, count(*) as n,
          count(*) filter (where exists (select 1 from rule_executions r where r.adset_id = b.adset_id
            and abs(date_diff('second', r.action_time::timestamptz, b.action_time::timestamptz)) <= 120)) as matches_rule_execution
        from buyer_actions b group by 1 order by 1""")

out = "\n".join(lines)
(ROOT / "out" / "losers_and_reversals.txt").write_text(out, encoding="utf-8")
print(out)
