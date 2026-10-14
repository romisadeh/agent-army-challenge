"""Task A.3: data profiling. Row counts, nulls, duplicate keys, cross-table consistency.
Writes out/profile.txt. Every check here is one cited in INVESTIGATION.md."""
from db import ROOT, TABLES, connect

con = connect()
lines = []


def show(title, sql):
    lines.append(f"== {title}\n{con.execute(sql).df().to_string(index=False)}\n")


for t in TABLES:
    cols = [r[0] for r in con.execute(f"describe {t}").fetchall()]
    exprs = ", ".join(f'count(*) filter (where "{c}" is null or trim("{c}") = \'\') as "{c}"' for c in cols)
    nulls = con.execute(f"select count(*) as n_rows, {exprs} from {t}").df().T
    nulls = nulls[nulls[0] > 0]
    lines.append(f"== {t}: rows + columns with null/empty values\n{nulls.to_string(header=False)}\n")

show("exact duplicate rows per table", " union all ".join(
    f"select '{t}' as tbl, (select count(*) from {t}) - (select count(*) from (select distinct * from {t})) as exact_dups"
    for t in TABLES))
show("perf duplicate (adset_id, date) keys, by date",
     "select date, count(*) as dup_keys from (select adset_id, date from daily_adset_performance "
     "group by all having count(*) > 1) group by 1 order by 1")
show("perf dup keys that are NOT exact duplicates (conflicting values)",
     "select count(*) as n from (select distinct * from daily_adset_performance) group by adset_id, date having count(*) > 1")
show("metadata duplicate adset_id", "select count(*) as n_rows, count(distinct adset_id) as n_ids from campaign_adset_metadata")
show("perf rows per date: null revenue vs spend",
     "select date, count(*) as n_rows, count(*) filter (where revenue is null) as rev_null, "
     "count(*) filter (where revenue is null and spend::double > 0) as rev_null_with_spend, "
     "round(sum(spend::double)) as spend, round(sum(revenue::double)) as revenue, "
     "round(sum(revenue::double) / sum(spend::double), 3) as rev_per_spend "
     "from (select distinct * from daily_adset_performance) group by 1 order by 1")
show("roi value on rows with null revenue",
     "select min(roi::double) as min_roi, max(roi::double) as max_roi, count(*) as n from daily_adset_performance where revenue is null")
show("profit / roi recompute (spend>0, deduped)",
     "select count(*) as n, count(*) filter (where abs(profit::double - (revenue::double - spend::double)) > 0.01) as bad_profit, "
     "count(*) filter (where abs(roi::double - profit::double / spend::double) > 0.001) as bad_roi "
     "from (select distinct * from daily_adset_performance) where spend::double > 0")
show("implausible values",
     "select count(*) filter (where spend::double < 0) as neg_spend, count(*) filter (where clicks::int > impressions::int) as clicks_gt_impr, "
     "count(*) filter (where fb_conversions::int > clicks::int) as fbconv_gt_clicks, count(*) filter (where roi::double > 5) as roi_gt_5 "
     "from daily_adset_performance")
show("adset_id length by account (perf)",
     "select account_name, length(adset_id) as id_len, count(distinct adset_id) as ids from daily_adset_performance group by 1, 2 order by 1")
show("adset_id coverage across tables",
     "select 'rule_executions' as src, count(distinct adset_id) as ids, "
     "count(distinct adset_id) filter (where adset_id in (select adset_id from daily_adset_performance)) as in_perf, "
     "count(distinct adset_id) filter (where adset_id in (select adset_id from campaign_adset_metadata)) as in_meta from rule_executions "
     "union all select 'buyer_actions', count(distinct adset_id), "
     "count(distinct adset_id) filter (where adset_id in (select adset_id from daily_adset_performance)), "
     "count(distinct adset_id) filter (where adset_id in (select adset_id from campaign_adset_metadata)) from buyer_actions")
show("rule executions by account", "select r.account_id, p.account_name, count(*) as n from rule_executions r "
     "left join (select distinct adset_id, account_name from daily_adset_performance) p using (adset_id) group by 1, 2")
show("rule firings: auto_rules.firings vs log", "select a.rule_id, a.firings::int as n_declared, count(r.rule_id) as n_logged "
     "from auto_rules a left join rule_executions r using (rule_id) group by 1, 2 order by 1")
show("action_date vs UTC date of action_time (mismatch hours)",
     "select substr(action_time, 12, 2) as utc_hour, count(*) as n from rule_executions "
     "where action_date <> substr(action_time, 1, 10) group by 1 order by 1")
show("rule responses", "select response, count(*) as n from rule_executions group by 1")
show("rule total_days_at_action vs perf spend_day_no",
     "select r.rule_id, count(*) as n, count(*) filter (where r.total_days_at_action::int <> p.spend_day_no::int) as mismatch "
     "from rule_executions r join (select distinct adset_id, date, spend_day_no from daily_adset_performance) p "
     "on p.adset_id = r.adset_id and p.date = r.action_date group by 1 order by 1")
show("buyer actions: adset_id presence by object_type", "select object_type, event_type, count(*) as n, count(adset_id) as with_adset_id "
     "from buyer_actions group by 1, 2 order by 1, 2")

out = "\n".join(lines)
(ROOT / "out" / "profile.txt").write_text(out, encoding="utf-8")
print(out)
