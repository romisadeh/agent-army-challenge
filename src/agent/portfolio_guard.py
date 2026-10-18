"""#4 (per window, no writes): portfolio caps after every card of a window is decided (ARCHITECTURE §4, §8 #5).

Spend floor proxy: §8 defines the floor on account *spend*, but at 10:00 only budgets exist. Expected account spend
= sum of each active adset's recent average spend; an action moves it by that spend x (new/old budget - 1), a pause
removes it. Floor: expected spend after net cuts must stay >= 85% of trailing spend/day. The cut cap (<= 15% of
trailing) runs first; the floor adds a check the cap can't make: an account already running below its trailing level
can't be cut further. The trailing level uses every day available before D (the data starts 06-06, so there is no
full 7-day trail) and says how many days that was.
Order: lowest expected_profit_impact.central first; the deterministic route is never touched. Cut/add/action caps trim to
keep; a spend-floor breach sets requires_approval (reason spend_floor) instead, because §8 says "unless a human approves".
"""
from __future__ import annotations

import pandas as pd

from agent import config as C

CHANGES = ("scale_up", "scale_down", "pause")


def _executable(d: dict) -> bool:
    return d["action"] in CHANGES and not d["requires_approval"]


def _dspend(d: dict, exp_spend: float) -> float:
    if d["action"] == "pause":
        return -exp_spend
    b = d["current_budget"]
    return exp_spend * (d["amount"] / b - 1) if b else 0.0


def apply(decisions: list[dict], feats: dict, perf: pd.DataFrame, D: pd.Timestamp) -> list[str]:
    """Mutates decisions in place: cap breaches are trimmed to keep; a spend-floor breach goes to approval. Returns log lines."""
    lines = []
    hist = perf[(perf.d < D) & (perf.d >= C.DATA_START)]
    n_days = hist.d.nunique()
    trailing = hist.groupby("acc").spend.sum() / n_days
    flag = f"trailing_window_short ({n_days} days)" if n_days < 7 else None

    def trim(d, why):
        d["guard_adjustments"].append(f"portfolio_trimmed: {why}")
        d["portfolio_trimmed_from"] = {"action": d["action"], "amount": d["amount"]}
        d["action"], d["amount"] = "keep", None
        d["expected_profit_impact"] = {"spend": 0.0, "low": 0.0, "central": 0.0, "high": 0.0}
        lines.append(f"  trimmed {d['adset_id']} ({d['portfolio_trimmed_from']['action']}): {why}")

    def to_approval(d, why):
        # ARCH §8: the floor holds "unless a human approves" -> the proposal goes to the queue, not into a silent keep
        d["requires_approval"] = True
        d["approval_reason"] = sorted(set(d["approval_reason"]) | {"spend_floor"})
        d["guard_adjustments"].append(f"portfolio: {why} -> requires approval")
        lines.append(f"  to approval {d['adset_id']} ({d['action']}): {why}")

    by_acc: dict[str, list] = {}
    for d in decisions:
        if _executable(d):
            by_acc.setdefault(d["account"], []).append(d)
    for acc, ds in sorted(by_acc.items()):
        t = float(trailing.get(acc, 0.0))
        es = {d["adset_id"]: feats[d["adset_id"]]["exp_spend"] for d in ds}
        def totals():
            live = [d for d in ds if _executable(d)]
            add = sum(max(_dspend(d, es[d["adset_id"]]), 0) for d in live)
            cut = -sum(min(_dspend(d, es[d["adset_id"]]), 0) for d in live)
            return add, cut
        add, cut = totals()
        # expected account spend before actions = recent average spend of every active adset in the account
        base = sum(v["exp_spend"] for v in feats.values() if v["acc"] == acc)
        floor = C.SPEND_FLOOR_PCT * t
        lines.append(f"{D.date()} {acc}: trailing spend/day ${t:.2f} ({flag or f'{n_days} days'}); expected before "
                     f"actions ${base:.2f}; floor ${floor:.2f}; proposed +${add:.2f} / -${cut:.2f}")
        if base < floor:
            lines.append(f"  expected spend already below the floor before any action (new launches are not counted)")
        for kind, limit, sign in (("cut", C.ACCOUNT_MAX_CUT_PCT * t, -1), ("add", C.ACCOUNT_MAX_ADD_PCT * t, 1),
                                  ("floor", floor, -1)):
            while True:
                add, cut = totals()
                # floor: the agent may not take expected spend below 85% of trailing by net cutting
                over = cut > limit if kind == "cut" else add > limit if kind == "add" else (base + add - cut < limit and cut > add)
                if not over:
                    break
                cands = [d for d in ds if _executable(d) and d["route"] != "deterministic"
                         and sign * _dspend(d, es[d["adset_id"]]) > 0]
                if not cands:
                    lines.append(f"  {kind} cap exceeded but only deterministic actions remain; left as is")
                    break
                pick = min(cands, key=lambda d: d["expected_profit_impact"]["central"])
                if kind == "floor":
                    to_approval(pick, f"{acc} spend floor (expected spend would fall below ${limit:.2f})")
                else:
                    trim(pick, f"{acc} {kind} cap (limit ${limit:.2f})")
        for d in ds:
            if flag:
                d["data_quality_flags"].append(flag)

    live = [d for d in decisions if _executable(d)]
    if len(live) > C.MAX_ACTIONS_PER_WINDOW:
        cands = sorted((d for d in live if d["route"] != "deterministic"), key=lambda d: d["expected_profit_impact"]["central"])
        for d in cands[: len(live) - C.MAX_ACTIONS_PER_WINDOW]:
            trim(d, f"{C.MAX_ACTIONS_PER_WINDOW} actions/window cap")
    lines.append(f"{D.date()} executable after portfolio guard: {sum(_executable(d) for d in decisions)}")
    return lines
