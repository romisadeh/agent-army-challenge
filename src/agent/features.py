"""#1 Feature Builder: raw tables -> one adset card per (adset, decision day D), as of the 10:00 window (UTC+3).

Only this module touches raw tables (ARCHITECTURE §6.1). Every Task A data fix is applied here:
deduplicated perf (issue 1), null revenue = 0 (2), metadata budget / 100 (5), delivery_status not effective_status (6),
budgets from state readings, not the rule log's old/new (7), local day = UTC+3 (8), only ui_* is human (15).

Leakage rule (plan item 12): every perf-derived value on a card comes from dates <= D-1; rule/buyer events must be
stamped before the window. `assert_no_leakage` fails the run if not. The one exemption is the *state* at the window
(the budget), which the live system reads from Meta; when it has to be reconstructed from a later log row, the card
says so in its flags.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import connect  # noqa: E402

from agent import config as C  # noqa: E402


@dataclass
class Data:
    perf: pd.DataFrame
    dup_keys: set
    meta: pd.DataFrame
    rules: pd.DataFrame
    buyers: pd.DataFrame
    budget_points: dict


def window_ts(d: pd.Timestamp, hour_local: float = C.WINDOW_LOCAL_H) -> pd.Timestamp:
    """Naive-UTC timestamp of local (UTC+3) hour `hour_local` on reporting day d."""
    return d + pd.Timedelta(hours=hour_local - C.TZ_OFFSET_H)


def local_date(ts: pd.Series) -> pd.Series:
    return (ts + pd.Timedelta(hours=C.TZ_OFFSET_H)).dt.normalize()


def load() -> Data:
    con = connect()
    perf = con.execute("""
        select distinct adset_id, account_name as acc, date::date as d, spend::double as spend,
               coalesce(revenue::double, 0) as rev, clicks::double as clicks, fb_conversions::double as conv,
               spend_day_no::int as day_no, first_spend_date::date as first_spend
        from daily_adset_performance""").df()
    dups = con.execute("""
        select adset_id, date::date as d from daily_adset_performance group by all having count(*) > 1""").df()
    meta = con.execute("""
        select adset_id, account_name as acc, campaign_id, adset_name, daily_budget::double / 100 as budget_meta,
               delivery_status, effective_status, budget_optimization, bid_strategy,
               creation_date::timestamptz as created
        from campaign_adset_metadata""").df()
    rules = con.execute("""
        select adset_id, rule_id, action_name, response, action_time::timestamptz as ts, action_date::date as d,
               spend_at_action::double as spend_seen, today_roi_at_action::double as roi_seen_2dp,
               case when today_rpc_at_action is not null and today_cpa_at_action::double > 0
                    then spend_at_action::double * today_rpc_at_action::double / today_cpa_at_action::double
                    else spend_at_action::double * (1 + today_roi_at_action::double) end as rev_seen,
               current_budget_from_fb::double as budget_fb, set_budget::double as budget_set
        from rule_executions""").df()
    buyers = con.execute("""
        select distinct action_time::timestamptz as ts, adset_id, event_type, old_budget::double as old_b,
               new_budget::double as new_b, note
        from buyer_actions where object_type = 'adset' and adset_id is not null""").df()

    for df in (rules, buyers):
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
    for df, col in ((perf, "d"), (perf, "first_spend"), (rules, "d"), (dups, "d")):
        df[col] = pd.to_datetime(df[col])
    meta["created"] = pd.to_datetime(meta["created"], utc=True).dt.tz_localize(None)
    meta["created_local"] = local_date(meta["created"])
    rules["kind"] = np.select(
        [rules.action_name.str.lower().str.startswith("turn off"), rules.action_name.str.lower().str.contains("decrease")],
        ["kill", "cut"], "turn_on")
    rules["hour_local"] = (rules.ts.dt.hour + rules.ts.dt.minute / 60 + C.TZ_OFFSET_H) % 24

    # engine echoes in the buyer log (issue 15): non-ui rows within 120 s of a rule execution on the same adset
    rt = rules[["adset_id", "ts"]].rename(columns={"ts": "rts"})
    m = buyers.reset_index().merge(rt, on="adset_id")
    echo_idx = set(m.loc[(m.ts - m.rts).abs() <= pd.Timedelta(seconds=C.ENGINE_ECHO_S), "index"])
    buyers["human"] = buyers.event_type.str.startswith("ui_")
    buyers["engine_echo"] = buyers.index.isin(echo_idx) & ~buyers.human
    buyers["ld"] = local_date(buyers.ts)

    # budget state points (ts, before, after) from every source that observed or set the live budget
    pts: dict[str, list] = {}
    for r in buyers.itertuples():
        if r.event_type.endswith("budget") and pd.notna(r.new_b) and (r.new_b > 0 or r.old_b > 0):
            pts.setdefault(r.adset_id, []).append((r.ts, r.old_b, r.new_b, "buyer_log"))
    for r in rules.itertuples():
        if pd.notna(r.budget_fb):
            after = r.budget_set if (r.response == "SUCCESS" and pd.notna(r.budget_set)) else r.budget_fb
            pts.setdefault(r.adset_id, []).append((r.ts, r.budget_fb, after, "rule_engine"))
    for v in pts.values():
        v.sort(key=lambda x: x[0])

    return Data(perf=perf, dup_keys=set(zip(dups.adset_id, dups.d)), meta=meta.set_index("adset_id"),
                rules=rules, buyers=buyers, budget_points=pts)


# ---------------------------------------------------------------------------------------------------------
def roi_band(r: float) -> str:
    return "<=-50%" if r <= -0.5 else "-50..-10%" if r <= -0.1 else "-10..+5%" if r <= 0.05 else ">+5%"


def age_bucket(n: int) -> str:
    return "1" if n <= 1 else "2-3" if n <= 3 else "4+"


def delay_factor(hour_local: float) -> float:
    return next(c for h, c in C.DELAY_CURVE if hour_local < h)


def budget_at(data: Data, adset: str, ts: pd.Timestamp) -> tuple[float | None, str]:
    """Live budget at ts: last observed `after` before ts; else the `before` of the first later observation
    (the state didn't change in between); else metadata / 100 (no change all week)."""
    pts = data.budget_points.get(adset, [])
    before = [p for p in pts if p[0] < ts]
    if before:
        return float(before[-1][2]), f"{before[-1][3]}_before_window"
    if pts:
        return float(pts[0][1]), f"{pts[0][3]}_after_window"
    b = data.meta.budget_meta.get(adset) if adset in data.meta.index else None
    return (float(b), "metadata") if b is not None and pd.notna(b) and b > 0 else (None, "unknown")


def peer_table(data: Data, D: pd.Timestamp) -> pd.DataFrame:
    """Adset-days whose forward window (next 1-3 days) ends by D-1: no peer statistic can see day D."""
    p = data.perf[(data.perf.spend > 0) & (data.perf.d <= D - pd.Timedelta(days=2))].copy()
    p["roi"], p["age"] = p.rev / p.spend - 1, p.day_no.map(age_bucket)
    p["band"] = p.roi.map(roi_band)
    idx = data.perf.set_index(["adset_id", "d"])
    p["fs"], p["fr"], p["last_fwd"] = 0.0, 0.0, p.d
    for j in (1, 2, 3):
        dj = p.d + pd.Timedelta(days=j)
        ok = dj <= D - pd.Timedelta(days=1)
        nxt = idx.reindex(list(zip(p.adset_id, dj)))
        p["fs"] += np.where(ok, nxt.spend.fillna(0).values, 0)
        p["fr"] += np.where(ok, nxt.rev.fillna(0).values, 0)
        p["last_fwd"] = np.where(ok, dj, p.last_fwd)
    p["last_fwd"] = pd.to_datetime(p.last_fwd)
    return p


def peer_stats(peers: pd.DataFrame, adset: str, acc: str, age: str, band: str) -> dict:
    pool = peers[peers.adset_id != adset]
    for g, label, coarse in ((pool[(pool.acc == acc) & (pool.age == age) & (pool.band == band)], f"{acc} / age {age} / {band}", 0),
                             (pool[(pool.acc == acc) & (pool.age == age)], f"{acc} / age {age} (band fallback)", 1),
                             (pool[pool.acc == acc], f"{acc} (fallback)", 2),
                             (pool[(pool.age == age) & (pool.band == band)], f"all accounts / age {age} / {band} (fallback)", 3)):
        if len(g) >= C.MIN_PEERS:
            break
    fs = g.fs.sum()
    return {"segment": label, "n": int(len(g)), "fallback_level": coarse,
            "fwd_roi_1_3d": round(float(g.fr.sum() / fs - 1), 3) if fs > 0 else None,
            "_max_source_date": g.last_fwd.max() if len(g) else None}


# ---------------------------------------------------------------------------------------------------------
def active_adsets(data: Data, D: pd.Timestamp) -> tuple[list[str], dict]:
    """Active on D = spent on D-1 and not killed by a rule on or after its last spend day. A kill on an earlier day
    followed by spend means someone re-enabled it (the buyer log can't show status changes, issue 16), so it is live.
    Day-1 adsets (first spend on D) get no decision by design; they are counted here (plan item 11)."""
    p, r = data.perf, data.rules
    prev = D - pd.Timedelta(days=1)
    spent_prev = set(p[(p.d == prev) & (p.spend > 0)].adset_id)
    last_kill = r[(r.kind == "kill") & (r.response == "SUCCESS") & (r.d < D)].groupby("adset_id").d.max()
    # every adset here spent on D-1, so "kill on or after the last spend day" = a successful kill dated D-1
    killed_before = {a for a in spent_prev if a in last_kill.index and last_kill[a] >= prev}
    active = sorted(spent_prev - killed_before)
    day_d = p[(p.d == D) & (p.spend > 0)]
    day1 = day_d[day_d.first_spend == D]
    info = {"spent_on_D_minus_1": len(spent_prev), "excluded_killed_on_D_minus_1": len(killed_before),
            "active": len(active), "day1_adsets_on_D": int(len(day1)), "day1_spend_on_D": round(float(day1.spend.sum()), 2),
            "day1_share_of_D_spend": round(float(day1.spend.sum() / day_d.spend.sum()), 3) if len(day_d) else None}
    return active, info


def build_card(data: Data, adset: str, D: pd.Timestamp, peers: pd.DataFrame, wts: pd.Timestamp | None = None,
               lessons: list[str] | None = None) -> dict:
    """Return {'card': <what the LLM sees>, 'f': <internal features>, 'sources': <leakage evidence>}."""
    wts = wts if wts is not None else window_ts(D)
    hist = data.perf[(data.perf.adset_id == adset) & (data.perf.d <= D - pd.Timedelta(days=1))
                     & (data.perf.d >= D - pd.Timedelta(days=5))].sort_values("d")
    meta = data.meta.loc[adset] if adset in data.meta.index else None
    acc = hist.acc.iloc[-1] if len(hist) else (meta.acc if meta is not None else None)
    flags: list[str] = []

    # --- matured history -------------------------------------------------------------------------------
    days = []
    for r in hist.itertuples():
        if r.spend > 0 or r.rev > 0:
            days.append({"d": -(D - r.d).days, "spend": round(r.spend, 2), "revenue": round(r.rev, 2),
                         "roi": round(r.rev / r.spend - 1, 3) if r.spend > 0 else None})
    last3 = hist[hist.d >= D - pd.Timedelta(days=3)]
    s3, r3 = float(last3.spend.sum()), float(last3.rev.sum())
    own_roi = r3 / s3 - 1 if s3 > 0 else None
    spend_days = [x for x in days if x["spend"] >= 0.5]
    mat_days = len(spend_days)
    material = [x for x in days if x["spend"] >= 1 and x["roi"] is not None]
    rois = [x["roi"] for x in material]
    last = next((x for x in reversed(material) if x["d"] == -1), None)
    prior = [x for x in material if x["d"] < -1]
    prior_roi = (sum(x["revenue"] for x in prior) / sum(x["spend"] for x in prior) - 1) if prior else None
    trend = None
    if last is not None and prior_roi is not None:
        diff = last["roi"] - prior_roi
        trend = "improving" if diff > 0.2 else "declining" if diff < -0.2 else "flat"
    loss_days_3 = sum(1 for x in days if x["d"] >= -3 and x["roi"] is not None and x["roi"] < 0 and x["spend"] >= 0.5)
    profit_days_3 = sum(1 for x in days if x["d"] >= -3 and x["roi"] is not None and x["roi"] > 0 and x["spend"] >= 0.5)
    zero_rev_streak = len(last3[last3.spend > 0]) >= C.DET_MIN_DAYS and r3 == 0
    age = int(hist.day_no.iloc[-1]) if len(hist) else 0

    # --- budget at the window --------------------------------------------------------------------------
    budget, bsrc = budget_at(data, adset, wts)
    if bsrc == "metadata":
        flags.append("budget_reconstructed:metadata_end_of_week")
    elif bsrc.endswith("after_window"):
        flags.append("budget_reconstructed:from_later_log_row")
    elif bsrc == "unknown":
        flags.append("CRITICAL:budget_unknown")
    exp_spend = float(np.mean([x["spend"] for x in days if x["d"] >= -3])) if days else 0.0

    # --- peers + blend ---------------------------------------------------------------------------------
    pk = peer_stats(peers, adset, acc, age_bucket(age + 1), roi_band(own_roi if own_roi is not None else -1))
    w = s3 / (s3 + C.K)
    peer_roi = pk["fwd_roi_1_3d"]
    if own_roi is not None and peer_roi is not None:
        blended = w * own_roi + (1 - w) * peer_roi
    else:
        blended = own_roi if own_roi is not None else peer_roi
    if pk["fallback_level"] >= 2 or pk["n"] < C.MIN_PEERS:
        flags.append("small_or_coarse_peer_group")

    # --- buyers (ui_* only = human) ----------------------------------------------------------------------
    b = data.buyers[(data.buyers.adset_id == adset) & (data.buyers.ts < wts)]
    hb = b[b.human].sort_values("ts")
    last_h = None
    if len(hb):
        x = hb.iloc[-1]
        hours = (wts - x.ts).total_seconds() / 3600
        last_h = {"hours_ago": round(hours, 1), "event": x.event_type,
                  "from": x.old_b, "to": x.new_b,
                  "direction": "up" if x.new_b > x.old_b else "down" if x.new_b < x.old_b else "same",
                  "note": x.note if isinstance(x.note, str) else None}
    h48 = hb[hb.ts >= wts - pd.Timedelta(hours=48)]
    net48 = float((h48.new_b - h48.old_b).sum()) if len(h48) else 0.0
    notes = [{"hours_ago": round((wts - x.ts).total_seconds() / 3600, 1), "text": x.note}
             for x in hb.itertuples() if isinstance(x.note, str)]
    lock = last_h is not None and last_h["hours_ago"] < C.BUYER_LOCK_H
    if b.engine_echo.any():
        flags.append("engine_echo_rows_in_buyer_log_excluded")

    # --- rules -------------------------------------------------------------------------------------------
    rr = data.rules[data.rules.adset_id == adset]
    def rule_list(df):
        return [{"rule": x.rule_id, "action": x.kind, "local_time": f"{int(x.hour_local):02d}:{int(x.hour_local % 1 * 60):02d}",
                 "response": "SUCCESS" if x.response == "SUCCESS" else "FAILED/NOOP",
                 "roi_seen": x.roi_seen_2dp, "spend_seen": round(x.spend_seen, 2) if pd.notna(x.spend_seen) else None}
                for x in df.sort_values("ts").itertuples()]
    rules_prev = rule_list(rr[(rr.d == D - pd.Timedelta(days=1)) & (rr.kind != "turn_on")])
    rules_today = rule_list(rr[(rr.d == D) & (rr.ts < wts)])
    off_by_rule = bool(((rr.d == D) & (rr.ts < wts) & (rr.kind == "kill") & (rr.response == "SUCCESS")).any())

    # --- data quality flags (INVESTIGATION §5) ------------------------------------------------------------
    allp = data.perf[(data.perf.adset_id == adset) & (data.perf.d <= D - pd.Timedelta(days=1))]
    if any((adset, d) in data.dup_keys for d in hist.d):
        flags.append("duplicate_perf_rows_removed")
    if ((allp.spend == 0) & (allp.rev > 0)).any():
        flags.append("revenue_on_zero_spend_day")
    if (allp.conv > allp.clicks).any():
        flags.append("meta_conversions_gt_clicks")
    if any(x["roi"] is not None and x["roi"] > 5 for x in days):
        flags.append("extreme_roi_gt_5")
    if budget and any(x["spend"] > C.UTIL_CAP * budget for x in days):
        flags.append("spend_exceeds_1.25x_budget")
    # metadata delivery/effective status is an END-OF-WEEK snapshot: on D it partly describes what happened after the
    # window (later rule kills), so it must not reach the card (leak found in the smoke test, DECISIONS Step 4f)
    # zero-spend placeholder rows exist before creation for many adsets; only spend before creation is an identity problem
    if meta is not None and ((allp.d < meta.created_local) & (allp.spend > 0)).any():
        flags.append("CRITICAL:performance_before_adset_creation")
    if (allp[allp.spend > 0].day_no == 0).any():
        flags.append("CRITICAL:spend_day_counter_zero_on_spend_day")
    if meta is None:
        flags.append("CRITICAL:adset_missing_from_metadata")

    card = {
        "adset_id": adset, "account": acc, "decision_date": D.date().isoformat(),
        "window": f"{C.WINDOW_LOCAL_H:02d}:00 local (UTC+3); all performance below is matured (through D-1)",
        "age_spend_days": age,
        "status_at_window": "off_by_rule_today" if off_by_rule else "active",
        "budget": {"current": round(budget, 2) if budget else None, "source": bsrc,
                   "bid_strategy": meta.bid_strategy if meta is not None else None,
                   "budget_type": meta.budget_optimization if meta is not None else None},
        "matured_days": days,
        "last_3d": {"spend": round(s3, 2), "revenue": round(r3, 2), "roi": round(own_roi, 3) if own_roi is not None else None,
                    "loss_days": loss_days_3, "profit_days": profit_days_3},
        "trend_last_day_vs_prior": trend,
        "peers": {k: v for k, v in pk.items() if not k.startswith("_")},
        "blended_roi": round(blended, 3) if blended is not None else None, "blend_weight_own": round(w, 2),
        "buyer": {"last_human_action": last_h, "net_budget_change_48h": round(net48, 2), "lock_24h": lock,
                  "notes": notes[-3:]},
        "rules": {"acted_yesterday": rules_prev, "acted_today_before_window": rules_today},
        "data_quality_flags": flags,
        "lessons": lessons or [],
    }
    f = {"adset_id": adset, "acc": acc, "D": D, "budget": budget, "budget_src": bsrc, "s3": s3, "r3": r3,
         "own_roi": own_roi, "peer_roi": peer_roi, "peer_n": pk["n"], "peer_fallback": pk["fallback_level"],
         "blended": blended, "w": w, "mat_days": mat_days, "rois": rois, "last": last, "prior_roi": prior_roi,
         "trend": trend, "loss_days_3": loss_days_3, "profit_days_3": profit_days_3, "zero_rev_streak": zero_rev_streak,
         "age": age, "lock": lock, "net48": net48, "notes": notes, "rules_today": rules_today, "off_by_rule": off_by_rule,
         "exp_spend": exp_spend, "flags": flags, "critical": [x for x in flags if x.startswith("CRITICAL")]}
    sources = {"perf_dates": list(hist.d), "peer_max_date": pk["_max_source_date"],
               "event_ts": list(b.ts) + list(rr[rr.ts < wts].ts)}
    return {"card": card, "f": f, "sources": sources}


def assert_no_leakage(built: dict, D: pd.Timestamp, wts: pd.Timestamp) -> None:
    s = built["sources"]
    limit = D - pd.Timedelta(days=1)
    assert all(d <= limit for d in s["perf_dates"]), f"leak: perf after D-1 for {built['f']['adset_id']} on {D.date()}"
    assert s["peer_max_date"] is None or s["peer_max_date"] <= limit, f"leak: peer window reaches D for {D.date()}"
    assert all(t < wts for t in s["event_ts"]), f"leak: event at/after the window for {built['f']['adset_id']}"
