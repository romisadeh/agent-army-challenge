"""Task A: $ impact of rule actions (profit and spend, separately). Method: notes/impact_method_proposal.md
(approved with changes; see DECISIONS.md step 1d). Writes out/impact_actions.csv, out/impact_summary.txt.

Key choices (each is an assumption, listed in INVESTIGATION.md):
- Effective action = first SUCCESS kill per adset; every SUCCESS budget cut. Repeats / "No budget to change" = no-ops.
- Budgets: pre = current_budget_from_fb, post = set_budget (old_budget/new_budget in the log are one change behind).
- Horizon = rest of action day + up to 3 days, cut at week end and at the next intervention on the adset
  (next effective rule action or any buyer action that isn't the engine's own API echo).
- Counterfactual ROI = w*own + (1-w)*peer, w = own_spend / (own_spend + K).
    own  = action-day revenue seen, corrected by the step-1 delay curve, + up to 3 prior matured days.
    peer = ACC-04 adset-days not acted on, same age bucket + same-day ROI band + budget used >= 35% or not
           (fallbacks if < MIN_PEERS) -> spend-weighted ROI over next 1-3 days.
- Kill counterfactual spend: rest of day to the peer full-day level, then per later day:
  budget_pre x peer survival x peer budget utilization (given spending), capped at OVERSPEND x budget_pre.
- Cut counterfactual spend: actual spend x budget_pre / budget_post. Marginal ROI = m x counterfactual ROI,
  m = 0.5 / 0.75 / 1.0 -> low / central / high (assumption #3: diminishing returns).
"""
import numpy as np
import pandas as pd

from db import ROOT, connect

MIN_PEERS = 5                              # min peer adset-days with a next day in the window
OVERSPEND = 1.25                           # Meta can spend up to 125% of daily budget on a day
K = 20.0                                   # $ of own spend at which own and peer ROI weigh equally
M_SCENARIOS = {"low": 0.5, "central": 0.75, "high": 1.0}
HORIZON_DAYS = 3
WEEK_END = pd.Timestamp("2026-06-12")
DELAY_CURVE = [(6, 0.69), (12, 0.83), (24, 1.00)]   # step 1 (out/revenue_delay.txt 1c): revenue-per-$ visible by local hour
ACC = "ACC-04"

con = connect()
con.execute("set TimeZone = 'UTC'")

perf = con.execute("""
    select distinct p.adset_id, p.account_name as acc, p.date::date as d, p.spend::double as spend,
           coalesce(p.revenue::double, 0) as rev, p.spend_day_no::int as day_no, m.daily_budget::double / 100 as budget_meta
    from daily_adset_performance p left join campaign_adset_metadata m using (adset_id)""").df()
perf["d"] = pd.to_datetime(perf["d"])
P = perf.set_index(["adset_id", "d"])

rules = con.execute("""
    select adset_id, rule_id, action_name, response, action_time::timestamptz as ts, action_date::date as d,
           spend_at_action::double as spend_seen,
           case when today_rpc_at_action is not null and today_cpa_at_action::double > 0
                then spend_at_action::double * today_rpc_at_action::double / today_cpa_at_action::double
                else spend_at_action::double * (1 + today_roi_at_action::double) end as rev_seen,
           coalesce(current_budget_from_fb::double, new_budget::double) as budget_pre, set_budget::double as budget_post
    from rule_executions""").df()
buyers = con.execute("""
    select b.adset_id, b.action_time::timestamptz as ts from buyer_actions b
    where b.adset_id is not null and not exists (select 1 from rule_executions r where r.adset_id = b.adset_id
          and abs(date_diff('second', r.action_time::timestamptz, b.action_time::timestamptz)) <= 120)""").df()
for df in (rules, buyers):
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
rules["d"] = pd.to_datetime(rules["d"])
rules["kind"] = np.where(rules.action_name.str.lower().str.startswith("turn off"), "kill",
                np.where(rules.action_name.str.lower().str.startswith("decrease"), "cut", "turn_on"))
rules["hour_local"] = (rules.ts.dt.hour + 3) % 24

ok = rules[rules.response == "SUCCESS"]
eff = pd.concat([ok[ok.kind == "kill"].sort_values("ts").groupby("adset_id").head(1),
                 ok[ok.kind == "cut"]]).sort_values("ts").reset_index(drop=True)
failed = rules[(rules.response != "SUCCESS") & (rules.kind != "turn_on")]


def day_end_utc(d):            # end of local (UTC+3) reporting day d, in naive UTC
    return d + pd.Timedelta(days=1) - pd.Timedelta(hours=3)


def roi_band(r):
    return "<=-50%" if r <= -0.5 else "-50..-10%" if r <= -0.1 else "-10..+5%" if r <= 0.05 else ">+5%"


def age_bucket(n):
    return "1" if n <= 1 else "2-3" if n <= 3 else "4+"


# ---- peers: ACC-04 adset-days with no rule action that day; their next 1-3 days -------------------------
acted = set(zip(rules.adset_id, rules.d))
pa = perf[(perf.acc == ACC) & (perf.spend > 0)].copy()
pa = pa[[(a, d) not in acted for a, d in zip(pa.adset_id, pa.d)]]
pa["roi"] = pa.rev / pa.spend - 1
pa["band"], pa["age"] = pa.roi.map(roi_band), pa.day_no.map(age_bucket)
pa["util"] = pa.spend / pa.budget_meta
pa["used35"] = pa.util >= 0.35
for j in (1, 2, 3):
    nxt = perf.set_index(["adset_id", "d"]).reindex(list(zip(pa.adset_id, pa.d + pd.Timedelta(days=j))))
    pa[f"s{j}"], pa[f"r{j}"] = nxt.spend.fillna(0).values, nxt.rev.fillna(0).values
    pa[f"in{j}"] = (pa.d + pd.Timedelta(days=j)) <= WEEK_END


def peer_stats(age, band, used35):
    """Peers = ACC-04 adset-days matching (age, ROI band, budget-used>=35%); fallbacks if < MIN_PEERS with a next day."""
    for grp, label in ((pa[(pa.age == age) & (pa.band == band) & (pa.used35 == used35)], f"age {age}, band {band}, used35={used35}"),
                       (pa[(pa.age == age) & (pa.used35 == used35)], f"age {age}, used35={used35} (band fallback)"),
                       (pa[pa.age == age], f"age {age} (fallback)"), (pa, "all ACC-04 (fallback)")):
        g1 = grp[grp.in1]
        if len(g1) >= MIN_PEERS:
            break
    out = {"peer_group": label, "peer_n": len(g1), "peer_util": float(grp.util.median())}
    fs, fr = 0.0, 0.0
    for j in (1, 2, 3):
        gj = grp[grp[f"in{j}"]]
        out[f"surv{j}"] = float((gj[f"s{j}"] > 0).mean()) if len(gj) else 0.0
        spent = gj[gj[f"s{j}"] > 0]
        out[f"util{j}"] = float((spent[f"s{j}"] / spent.budget_meta).median()) if len(spent) else 0.0
        fs, fr = fs + gj[f"s{j}"].sum(), fr + gj[f"r{j}"].sum()
    out["peer_roi"] = fr / fs - 1 if fs > 0 else np.nan
    out["peer_fwd_spend"] = fs
    return out


def completeness(hour):
    return next(c for h, c in DELAY_CURVE if hour < h)


def next_intervention(adset, ts):
    t = [x for x in list(eff.ts[eff.adset_id == adset]) + list(buyers.ts[buyers.adset_id == adset]) if x > ts]
    return min(t) if t else None


def evaluate(a):
    """Impact of one action. Returns dict with actual/counterfactual spend and profit per scenario."""
    day = P.loc[(a.adset_id, a.d)]
    prior = [P.loc[(a.adset_id, a.d - pd.Timedelta(days=j))] for j in (1, 2, 3)
             if (a.adset_id, a.d - pd.Timedelta(days=j)) in P.index]
    prior = [r for r in prior if r.spend > 0]
    c = completeness(a.hour_local)
    rev_corr = a.rev_seen / c
    own_spend = a.spend_seen + sum(r.spend for r in prior)
    own_roi = (rev_corr + sum(r.rev for r in prior)) / own_spend - 1 if own_spend > 0 else np.nan
    seen_roi = a.rev_seen / a.spend_seen - 1 if a.spend_seen > 0 else np.nan
    budget = a.budget_pre if a.budget_pre and a.budget_pre > 0 else day.budget_meta
    used35 = bool(a.spend_seen / budget >= 0.35) if budget else False
    peers = peer_stats(age_bucket(day.day_no), roi_band(seen_roi if not np.isnan(seen_roi) else -1), used35)
    w = own_spend / (own_spend + K)
    roi_cf = w * own_roi + (1 - w) * peers["peer_roi"] if not np.isnan(peers["peer_roi"]) else own_roi

    # horizon pieces: (day offset j, fraction of that day's post-action spend inside the horizon)
    nxt = next_intervention(a.adset_id, a.ts)
    end = min(x for x in [nxt, day_end_utc(a.d + pd.Timedelta(days=HORIZON_DAYS)), day_end_utc(WEEK_END)] if x is not None)
    pieces = []
    rest_len = (day_end_utc(a.d) - a.ts).total_seconds()
    pieces.append((0, max(0.0, min(1.0, (end - a.ts).total_seconds() / rest_len)) if rest_len > 0 else 0.0))
    for j in range(1, HORIZON_DAYS + 1):
        start = day_end_utc(a.d + pd.Timedelta(days=j - 1))
        if end <= start:
            break
        pieces.append((j, min(1.0, (end - start).total_seconds() / 86400)))

    act_spend = act_rev = cf_spend = 0.0
    cap = OVERSPEND * budget
    cf_day0_full = max(day.spend, min(cap, budget * peers["peer_util"]))
    for j, frac in pieces:
        key = (a.adset_id, a.d + pd.Timedelta(days=j))
        row = P.loc[key] if key in P.index else None
        if j == 0:
            s = max(0.0, day.spend - a.spend_seen) * frac
            r = s * (day.rev / day.spend if day.spend > 0 else 0)
        else:
            s = (row.spend if row is not None else 0.0) * frac
            r = (row.rev if row is not None else 0.0) * frac
        act_spend, act_rev = act_spend + s, act_rev + r
        if a.kind == "kill":
            cf = max(s, (cf_day0_full - a.spend_seen) * frac) if j == 0 else \
                 min(cap, budget * peers[f"util{j}"]) * peers[f"surv{j}"] * frac
        else:  # cut: spend scales with budget
            cf = s * (a.budget_pre / a.budget_post) if a.budget_post and a.budget_pre else s
        cf_spend += cf

    res = {"adset_id": a.adset_id, "rule_id": a.rule_id, "kind": a.kind, "action_date": a.d.date(), "hour_local": a.hour_local,
           "response": a.response, "budget_pre": a.budget_pre, "budget_post": a.budget_post,
           "spend_seen": round(a.spend_seen, 2), "roi_seen": round(seen_roi, 3), "delay_factor": c,
           "roi_own_corrected": round(own_roi, 3), "own_spend": round(own_spend, 2), "w_own": round(w, 2),
           "peer_roi": round(peers["peer_roi"], 3), "peer_group": peers["peer_group"], "peer_n": peers["peer_n"],
           "roi_cf": round(roi_cf, 3), "roi_day_matured": round(day.rev / day.spend - 1, 3) if day.spend > 0 else np.nan,
           "horizon_end_utc": end, "truncated_by_intervention": nxt is not None and end == nxt,
           "actual_spend": round(act_spend, 2), "actual_profit": round(act_rev - act_spend, 2),
           "cf_spend": round(cf_spend, 2), "d_spend": round(act_spend - cf_spend, 2)}
    marginal = cf_spend - act_spend
    for name, m in M_SCENARIOS.items():
        # kill removes average spend (m does not apply); cut removes marginal spend at m x average ROI
        d_profit = -marginal * (roi_cf if a.kind == "kill" else m * roi_cf)
        res[f"d_profit_{name}"] = round(d_profit, 2)
    return res


impact = pd.DataFrame([evaluate(a) for a in eff.itertuples()])
impact.to_csv(ROOT / "out" / "impact_actions.csv", index=False)

lines = []
add = lambda t, df: lines.append(f"== {t}\n{df.to_string()}\n")
add("Effective actions by rule: spend impact and profit impact (low / central / high = marginal ROI at 50/75/100% for cuts)",
    impact.groupby(["kind", "rule_id"]).agg(n=("adset_id", "size"), spend_seen=("spend_seen", "sum"),
        d_spend=("d_spend", "sum"), d_profit_low=("d_profit_low", "sum"), d_profit_central=("d_profit_central", "sum"),
        d_profit_high=("d_profit_high", "sum"), truncated=("truncated_by_intervention", "sum")).round(2))
tot = {}
for name in M_SCENARIOS:
    col = impact[f"d_profit_{name}"]
    tot[name] = {"saved (d_profit>0)": col[col > 0].sum(), "burned (d_profit<0)": col[col < 0].sum(), "net": col.sum()}
add("Totals: profit saved vs burned", pd.DataFrame(tot).round(2))
add("Totals: spend", pd.DataFrame({"actual_spend": [impact.actual_spend.sum()], "cf_spend": [impact.cf_spend.sum()],
                                    "spend_lost": [impact.d_spend.sum()]}).round(2))
add("Top 10 burns (central)", impact.nsmallest(10, "d_profit_central")[
    ["adset_id", "rule_id", "action_date", "hour_local", "roi_seen", "roi_own_corrected", "peer_roi", "roi_cf",
     "roi_day_matured", "actual_spend", "cf_spend", "d_spend", "d_profit_central"]])
add("Top 10 saves (central)", impact.nlargest(10, "d_profit_central")[
    ["adset_id", "rule_id", "action_date", "hour_local", "roi_seen", "roi_own_corrected", "peer_roi", "roi_cf",
     "roi_day_matured", "actual_spend", "cf_spend", "d_spend", "d_profit_central"]])

grp = np.where(impact.kind == "cut", "1 budget cuts", np.where(impact.rule_id == "R04", "3 R04 kills", "2 kills excl. R04"))
split = impact.groupby(grp).agg(n=("adset_id", "size"), spend_judged=("spend_seen", "sum"), spend_lost=("d_spend", "sum"),
    d_profit_low=("d_profit_low", "sum"), d_profit_central=("d_profit_central", "sum"), d_profit_high=("d_profit_high", "sum"))
split.loc["4 total excl. R04"] = split.loc[["1 budget cuts", "2 kills excl. R04"]].sum()
split.loc["5 total incl. R04"] = split.loc[["1 budget cuts", "2 kills excl. R04", "3 R04 kills"]].sum()
add("SPLIT: cuts vs kills (spend_lost = actual - counterfactual spend; profit low/central/high = marginal ROI 50/75/100% for cuts)",
    split.round(2))

# ---- validation: failed actions = the no-action path was observed ---------------------------------------
val = []
for a in failed.sort_values("ts").groupby(["adset_id", "d"]).head(1).itertuples():
    r = evaluate(a)
    obs_roi = r["actual_profit"] / r["actual_spend"] if r["actual_spend"] > 0 else np.nan
    pred_spend = r["cf_spend"] if a.kind == "kill" else r["actual_spend"]
    val.append({"adset_id": a.adset_id, "rule_id": a.rule_id, "kind": a.kind, "date": a.d.date(), "response": a.response[:25],
                "predicted_roi_cf": r["roi_cf"], "observed_roi": round(obs_roi, 3),
                "predicted_spend_no_action": round(pred_spend, 2) if a.kind == "kill" else None,
                "observed_spend": r["actual_spend"], "observed_profit": r["actual_profit"]})
vdf = pd.DataFrame(val)
add(f"VALIDATION (sanity check, n = {len(val)} adset-days): failed / no-op actions, where the no-action path was observed", vdf)
sign_ok = int((np.sign(vdf.predicted_roi_cf.round(2)) == np.sign(vdf.observed_roi.round(2))).sum())
vk = vdf[vdf.kind == "kill"]
over = vk.predicted_spend_no_action / vk.observed_spend
add("VALIDATION summary", pd.DataFrame({"n": [len(vdf)], "roi_sign_matches": [sign_ok], "n_failed_kills": [len(vk)],
    "kill_spend_overprediction_min_x": [round(over.min(), 1)], "kill_spend_overprediction_max_x": [round(over.max(), 1)]}))

# ---- R04 sensitivity: peer ROI and spend over-prediction ---------------------------------------------------
r4 = impact[impact.rule_id == "R04"]
sens = []
for x in (-1.0, -0.5, -0.25, 0.0):
    roi_cf = r4.w_own * r4.roi_own_corrected + (1 - r4.w_own) * x
    sens.append({"case": f"peer_roi = {x:+.2f}", "r04_d_profit": round(float((-(r4.cf_spend - r4.actual_spend) * roi_cf).sum()), 2)})
for f in (over.min(), over.max()):
    sens.append({"case": f"modelled spend / {f:.1f} (validation over-prediction)", "r04_d_profit": round(r4.d_profit_central.sum() / f, 2)})
add(f"R04 SENSITIVITY (model central = {r4.d_profit_central.sum():.2f}; peer n = {int(r4.peer_n.max())} adset-days)", pd.DataFrame(sens))

# ---- omission: R04 conditions outside ACC-04 ------------------------------------------------------------
r04 = rules[(rules.rule_id == "R04") & (rules.response == "SUCCESS")].groupby(["adset_id", "d"]).head(1)
r04 = r04.merge(perf, on=["adset_id", "d"])
tail_share = float((1 - r04.spend_seen / r04.spend).median())
o = perf[(perf.acc != ACC) & (perf.spend > 0) & (perf.day_no == 1)].copy()
o = o[(o.spend / o.budget_meta > 0.35) & (o.rev / o.spend - 1 < -0.5)]
rows = []
for q in o.itertuples():
    later = perf[(perf.adset_id == q.adset_id) & (perf.d > q.d)]
    after_spend = tail_share * q.spend + later.spend.sum()
    after_profit = tail_share * (q.rev - q.spend) + (later.rev - later.spend).sum()
    rows.append({"acc": q.acc, "adset_id": q.adset_id, "spend_after_trigger": after_spend, "profit_after_trigger": after_profit})
om = pd.DataFrame(rows)
add(f"OMISSION: R04 conditions met outside ACC-04 (EOD approx). After-trigger = {tail_share:.0%} of day-1 + all later days. "
    "Kill would save -profit_after (net of later winners).",
    om.groupby("acc").agg(qualifiers=("adset_id", "size"), spend_after=("spend_after_trigger", "sum"),
        profit_after=("profit_after_trigger", "sum"),
        saves=("profit_after_trigger", lambda s: -s[s < 0].sum()), burns=("profit_after_trigger", lambda s: -s[s > 0].sum()))
    .assign(net_saved=lambda x: -x.profit_after).pipe(lambda t: pd.concat([t, t.sum().to_frame("TOTAL").T])).round(2))

out = "\n".join(lines)
(ROOT / "out" / "impact_summary.txt").write_text(out, encoding="utf-8")
print(out)
