"""Compare the agent with what the rules and the buyers actually did, and evaluate it (plan §6).

  python src/agent/compare.py            reads out/decisions.jsonl (+ out/decisions_nollm.jsonl for the ablation)
                                         -> out/comparison.txt, out/evaluation.txt, out/ledger.jsonl, out/calibration.json

Grading confound (stated, plan item 15): day D's outcome happened under what buyers and rules actually did, not under
the agent's action. So grades are about DIRECTION (did the adset deserve more or less money?), using D and D+1 where
D+1 exists. The $ metric applies the agent's action to the observed day as a counterfactual, with the Task A
assumptions (spend scales with budget for the rest of the day; marginal ROI m = 0.5/0.75/1.0).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import config as C, features as F, feedback as FB  # noqa: E402
from agent.guard import REMAINING_DAY  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
CHANGES = ("scale_up", "scale_down", "pause")
BINS = [(0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


def load_decisions(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def enrich(decs: list[dict], data: F.Data) -> pd.DataFrame:
    rows = []
    for d in decs:
        D = pd.Timestamp(d["decision_date"])
        o = FB.outcome(data.perf, d["adset_id"], D)
        lab = FB.label(o["roi_graded"], o["spend_graded"])
        lab_d = FB.label(o["roi_D"], o["spend_D"])
        rr = data.rules[(data.rules.adset_id == d["adset_id"]) & (data.rules.d == D)
                        & (data.rules.response == "SUCCESS") & data.rules.kind.isin(["kill", "cut"])].sort_values("ts")
        rule = rr.iloc[0] if len(rr) else None
        bb = data.buyers[(data.buyers.adset_id == d["adset_id"]) & (data.buyers.ld == D) & data.buyers.human]
        net = float((bb.new_b - bb.old_b).sum()) if len(bb) else 0.0
        eff_action = d["proposed_if_escalated"] if d["action"] == "escalate" else d["action"]
        la = d.get("llm_action")
        b = d["current_budget"]
        llm_amt = d["amount"] if la == d["action"] else (
            round(b * (1.2 if la == "scale_up" else 0.8), 2) if la in ("scale_up", "scale_down") and b else None)
        rows.append({**d, **o, "outcome_label": lab, "outcome_label_D": lab_d,
                     "grade": FB.grade_decision(d["action"], lab),
                     "grade_proposal": FB.grade_decision(eff_action, lab) if eff_action else {"correct": None, "harmful": None},
                     "grade_default_keep": FB.grade_decision("keep", lab),
                     "grade_llm_raw": FB.grade_decision(d["llm_action"], lab) if d.get("llm_action") in FB.DIR else {"correct": None, "harmful": None},
                     # $ grades (added after the first full run, Step 5a); LLM-raw amount assumed a 20% step if the guard changed it
                     "grade_usd": FB.dollar_grade(d["action"], d["amount"], b, o["spend_D"], o["roi_D"]),
                     "grade_usd_llm_raw": FB.dollar_grade(la, llm_amt, b, o["spend_D"], o["roi_D"]),
                     "rule_on_D": None if rule is None else rule.kind,
                     "rule_id": None if rule is None else rule.rule_id,
                     "rule_before_window": None if rule is None else bool(rule.ts < F.window_ts(D)),
                     "rule_local_time": None if rule is None else f"{int(rule.hour_local):02d}:{int(rule.hour_local % 1 * 60):02d}",
                     "rule_roi_seen": None if rule is None or not rule.spend_seen else round(rule.rev_seen / rule.spend_seen - 1, 3),
                     "rule_spend_seen": None if rule is None else rule.spend_seen,
                     "buyer_on_D": "up" if net > 0 else "down" if net < 0 else ("touch" if len(bb) else None),
                     "buyer_net_change": round(net, 2),
                     "buyer_note_D": "; ".join(n for n in bb.note if isinstance(n, str)) or None})
    return pd.DataFrame(rows)


def dollar_impact(df: pd.DataFrame, which: str) -> dict:
    """Counterfactual vs what actually happened on D, for 'executable' actions or for all proposals if approved."""
    out = {k: {"spend": 0.0, "profit": 0.0} for k in C.M_SCENARIOS}
    n = 0
    for r in df.itertuples():
        act, amt = r.action, r.amount
        if which == "if_approved" and act == "escalate":
            act, amt = r.proposed_if_escalated, r.proposed_amount
        if act not in CHANGES or (which == "executable" and r.requires_approval) or not r.spend_D or pd.isna(r.roi_D):
            continue
        spend_rest = r.spend_D * REMAINING_DAY
        if act == "pause":
            ds, dp = -spend_rest, {k: -spend_rest * r.roi_D for k in C.M_SCENARIOS}
        else:
            if pd.isna(amt) or not amt or pd.isna(r.current_budget) or not r.current_budget:
                continue
            ds = spend_rest * (amt / r.current_budget - 1)
            dp = {k: ds * m * r.roi_D for k, m in C.M_SCENARIOS.items()}
        n += 1
        for k in C.M_SCENARIOS:
            out[k]["spend"] += ds
            out[k]["profit"] += dp[k]
    return {"n_actions": n, **{k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in out.items()}}


def accuracy(df: pd.DataFrame, col: str = "grade") -> str:
    """correct = direction matched; harmful = opposite direction (up vs down); neither = no change on a clear
    up/down day, or a change on a flat day (not harmful, not right)."""
    g = df[col].map(lambda x: x["correct"])
    h = df[col].map(lambda x: x["harmful"])
    graded = int(g.notna().sum())
    if not graded:
        return "no gradable decisions"
    ok, bad = int((g == True).sum()), int((h == True).sum())  # noqa: E712
    neither = graded - ok - bad
    return (f"graded {graded} of {len(df)}: correct {ok} ({ok / graded:.0%}), harmful {bad} ({bad / graded:.0%}), "
            f"neither {neither} ({neither / graded:.0%})")


def calibration(df: pd.DataFrame, conf_col: str, grade_col: str) -> str:
    x = df[df[conf_col].notna()].copy()
    x["ok"] = x[grade_col].map(lambda g: g["correct"])
    x = x[x.ok.notna()]
    if x.empty:
        return "  (no graded LLM decisions)"
    brier = float(((x[conf_col] - x.ok.astype(float)) ** 2).mean())
    lines = [f"  Brier {brier:.3f} on n = {len(x)} (0 = perfect; always saying 0.5 scores 0.25)"]
    for lo, hi in BINS:
        b = x[(x[conf_col] >= lo) & (x[conf_col] < hi)]
        if len(b):
            lines.append(f"    confidence {lo:.1f}-{min(hi, 1):.1f}: n {len(b):3d}, mean stated {b[conf_col].mean():.2f}, "
                         f"actually right {b.ok.mean():.0%}")
    return "\n".join(lines)


def usd_summary(df: pd.DataFrame, col: str = "grade_usd") -> str:
    g = df[col]
    graded = g[g.map(lambda x: x["right"] is not None)]
    if graded.empty:
        return "no gradable decisions"
    right = int(graded.map(lambda x: x["right"]).sum())
    regret = float(graded.map(lambda x: x["regret"]).sum())
    delta = float(graded.map(lambda x: x["delta"]).sum())
    return (f"graded {len(graded)} of {len(df)}: right in $ {right} ({right / len(graded):.0%}), "
            f"total regret ${regret:.2f}, profit effect vs keep ${delta:+.2f}")


def keep_reason(r) -> str:
    """Why a final keep is a keep (Step 5a decomposition). Uses guard_adjustments, which records every override."""
    adj = " ".join(r.guard_adjustments)
    if r.route == "auto_keep":
        return "1 auto_keep (materiality gate, no LLM)"
    if "llm_budget_exhausted" in r.data_quality_flags or "llm_skipped_by_limit" in r.data_quality_flags:
        return "6 LLM not called"
    if "portfolio_trimmed" in adj:
        return "5 portfolio trim"
    if "data_sufficiency" in adj:
        return "3 guard: data_sufficiency gate converted a change"
    if "without a usable amount" in adj or "is not above budget" in adj or "is not below budget" in adj:
        return "4 guard: unusable amount from the LLM"
    if r.llm_action == "keep":
        return "2 LLM said keep"
    return "7 other"


def main(path: Path) -> None:
    data = F.load()
    df = enrich(load_decisions(path), data)
    abl = enrich(load_decisions(OUT / "decisions_nollm.jsonl"), data) if (OUT / "decisions_nollm.jsonl").exists() else None
    ev, cmp = [], []

    # ---- coverage -------------------------------------------------------------------------------------
    ev.append("== Coverage (plan items 11, 16)")
    for D in C.DECISION_DATES:
        _, info = F.active_adsets(data, D)
        x = df[df.decision_date == D.date().isoformat()]
        tot = x.last_3d_spend.sum()
        ak = x[x.route == "auto_keep"]
        ev.append(f"  {D.date()}: {len(x)} active adsets; auto_keep {len(ak)} ({len(ak) / len(x):.0%} of adsets, "
                  f"{ak.last_3d_spend.sum() / tot:.1%} of 3-day spend); routes {dict(Counter(x.route))}; "
                  f"day-1 adsets with no decision by design: {info['day1_adsets_on_D']} "
                  f"({info['day1_share_of_D_spend']:.0%} of D's spend)")
    ev.append(f"  actions: {dict(Counter(df.action))}; requires_approval: {int(df.requires_approval.sum())}; "
              f"portfolio-trimmed: {int(df.get('portfolio_trimmed_from', pd.Series(dtype=object)).notna().sum()) if 'portfolio_trimmed_from' in df else 0}")
    esc_spend = df[df.action.isin(["escalate", "keep"])].spend_D.sum() / df.spend_D.sum()
    ev.append(f"  shrink check: adsets left on keep/escalate carried {esc_spend:.0%} of D's spend among active adsets")

    # ---- primary metric -------------------------------------------------------------------------------
    ev.append("\n== Primary metric: counterfactual absolute profit and spend vs what actually happened (day D, rest of day)")
    for name, frame in (("agent", df), ("no-LLM ablation", abl)):
        if frame is None:
            continue
        for which in ("executable", "if_approved"):
            ev.append(f"  {name:16s} {which:12s}: {json.dumps(dollar_impact(frame, which))}")
    ev.append("  (profit/spend deltas in $, by marginal-ROI scenario m = 0.5 / 0.75 / 1.0; pause uses the day's average ROI)")

    # ---- direction accuracy ---------------------------------------------------------------------------
    ev.append("\n== Direction accuracy on matured outcome (D+D1 where D+1 exists). Assumption: label band +-10% ROI (>= +10% up, <= -10% down, else neutral). The harmful rate is the safety number; accuracy alone rewards activity")
    ev.append(f"  agent, all decisions: {accuracy(df)}")
    for r in ("llm_review", "deterministic", "auto_keep"):
        ev.append(f"  agent, route {r}: {accuracy(df[df.route == r])}")
    for a in ("scale_up", "scale_down", "pause", "keep"):
        ev.append(f"  agent, action {a}: {accuracy(df[df.action == a])}")
    if abl is not None:
        ev.append(f"  no-LLM ablation, llm_review cards: {accuracy(abl[abl.route == 'llm_review'])}")
        ev.append(f"  agent,           llm_review cards: {accuracy(df[df.route == 'llm_review'])}")
    esc = df[df.action == "escalate"]
    ev.append(f"\n== Escalations graded twice (plan item 10): n = {len(esc)}")
    ev.append(f"  as the proposed action: {accuracy(esc, 'grade_proposal')}")
    ev.append(f"  as the default keep:    {accuracy(esc, 'grade_default_keep')}")

    # ---- why so many keeps ------------------------------------------------------------------------------
    kp = df[df.action == "keep"].copy()
    kp["why"] = kp.apply(keep_reason, axis=1)
    ev.append(f"\n== Keep decomposition (Step 5a): {len(kp)} keeps, carrying {kp.spend_D.sum() / df.spend_D.sum():.0%} of D's spend")
    for why, g in sorted(kp.groupby("why"), key=lambda x: x[0]):
        ev.append(f"  {why[2:]:52s} n {len(g):3d}  D spend ${g.spend_D.sum():7.2f} ({g.spend_D.sum() / df.spend_D.sum():.0%})  "
                  f"$ grade: {usd_summary(g)}")
    lk = kp[kp.why == "2 LLM said keep"]
    if len(lk):
        both = lk.llm_raw.map(lambda x: bool(x and x.get("sonnet")))
        ev.append(f"  of 'LLM said keep': Haiku alone {int((~both).sum())}, with Sonnet {int(both.sum())}; median "
                  f"data_sufficiency {lk.evidence.map(lambda e: e['data_sufficiency']).median():.2f}, signal_agreement "
                  f"{lk.evidence.map(lambda e: e['signal_agreement']).median():.2f}")
    for why in ("1 auto_keep (materiality gate, no LLM)", "2 LLM said keep"):
        g = kp[kp.why == why]
        big = g[g.spend_D >= 5]
        gr = g[g.grade_usd.map(lambda x: x["right"] is not None)]
        w = (gr.spend_D * gr.grade_usd.map(lambda x: float(x["right"]))).sum() / gr.spend_D.sum() if len(gr) else float("nan")
        ev.append(f"  {why[2:]}: right in $, spend-weighted {w:.0%}; restricted to D spend >= $5: {usd_summary(big)}")
    gate_esc = df[(df.action == "escalate") & df.guard_adjustments.map(lambda a: any("signal_agreement" in x for x in a))]
    ev.append(f"  (not keeps, for completeness: agreement-gate escalations {len(gate_esc)}; thin-evidence +10% caps "
              f"{int(df.guard_adjustments.map(lambda a: any('thin evidence' in x for x in a)).sum())})")

    # ---- $ grade (added after the run) ------------------------------------------------------------------
    ev.append("\n== $ grade (ADDED AFTER THE FIRST FULL RUN, Step 5a: ARCH §7.2 grades in $; the stub had used the direction band)")
    ev.append(f"   regret = best of {{keep, +20%, -20%, the action}} minus the action's profit effect, day D, m = {FB.M_CENTRAL}; "
              f"right in $ = regret <= ${FB.USD_MATERIALITY:.2f}")
    ev.append(f"  agent, all decisions:      {usd_summary(df)}")
    ev.append(f"  agent, llm_review cards:   {usd_summary(df[df.route == 'llm_review'])}")
    ev.append(f"  agent, LLM's own action:   {usd_summary(df[df.route == 'llm_review'], 'grade_usd_llm_raw')}")
    if abl is not None:
        ev.append(f"  no-LLM, llm_review cards:  {usd_summary(abl[abl.route == 'llm_review'])}")
    for a in ("scale_up", "scale_down", "pause", "keep"):
        ev.append(f"  agent, action {a:10s}:  {usd_summary(df[df.action == a])}")

    ev.append("\n== Calibration (plan item 4)")
    ev.append(" raw LLM confidence vs the LLM's own action (direction band):\n" + calibration(df, "llm_confidence", "grade_llm_raw"))
    ev.append(" capped confidence vs the final action (LLM cards, direction band):\n"
              + calibration(df[df.llm_confidence.notna()], "confidence", "grade"))
    ev.append(" CHANGE DECISIONS ONLY (where a direction grade means something):")
    ev.append("  raw, LLM proposed a change:\n" + calibration(df[df.llm_action.isin(CHANGES)], "llm_confidence", "grade_llm_raw"))
    ev.append("  capped, final action is a change:\n" + calibration(df[df.action.isin(CHANGES) & df.llm_confidence.notna()], "confidence", "grade"))

    # ---- prompt revision: v2 (the run) vs v3 (Step 5b), development day vs held-out days -------------------
    for vp in sorted(OUT.glob("decisions_prompt_*.jsonl")):
        v = enrich(load_decisions(vp), data)
        ev.append(f"\n== Prompt revision {vp.stem}: v2 (the run) vs v3. Wording chosen on 06-10 only (development); "
                  f"06-11 and 06-12 held out")
        for split, dates in (("development 06-10", ["2026-06-10"]), ("HELD-OUT 06-11 + 06-12", ["2026-06-11", "2026-06-12"])):
            vname = vp.stem.removeprefix("decisions_prompt_")
            for name, frame in (("v2 (run)", df), (vname, v), ("no-LLM rule", abl)):
                if frame is None:
                    continue
                x = frame[frame.decision_date.isin(dates) & (frame.route == "llm_review")]
                if x.empty:
                    continue
                acts = Counter(x.action)
                ev.append(f"  {split:24s} {name}: actions {dict(acts)}; changes {sum(acts[k] for k in CHANGES)}")
                ev.append(f"  {'':24s}     $: {usd_summary(x)}")
                ev.append(f"  {'':24s}     direction: {accuracy(x)}")
                ev.append(f"  {'':24s}     executable $ vs actual: {json.dumps(dollar_impact(x, 'executable')['central'])}")

    # ---- sensitivity runs (same stored LLM responses, different guard thresholds) ----------------------
    for sp in sorted(OUT.glob("decisions_sens_*.jsonl")):
        s = enrich(load_decisions(sp), data)
        meta = json.loads((OUT / f"run_meta{sp.stem.removeprefix('decisions')}.json").read_text(encoding="utf-8"))
        ev.append(f"\n== Sensitivity check {sp.stem} (DS_MIN {meta['DS_MIN']}, SA_MIN {meta['SA_MIN']}; replayed, $0; not a headline)")
        ev.append(f"  actions: {dict(Counter(s.action))}")
        ev.append(f"  llm_review cards, direction: {accuracy(s[s.route == 'llm_review'])}")
        ev.append(f"  llm_review cards, $ grade:   {usd_summary(s[s.route == 'llm_review'])}")
        ev.append(f"  executable $ vs actual:      {json.dumps(dollar_impact(s, 'executable'))}")

    # ---- comparison with rules and buyers ------------------------------------------------------------
    df["agent_dir"] = df.apply(lambda r: FB.DIR.get(r.proposed_if_escalated if r.action == "escalate" else r.action, "neutral"), axis=1)
    # Rules are graded the Task A way (code review 3): the ROI the rule saw vs the matured ROI of that day's spend.
    # D+1 can't be used: after a morning kill the adset has ~no spend left, so the forward label is "unclear".
    cmp.append("== Agent vs legacy rules on the same day (effective rule action = first successful kill/cut on D)")
    cmp.append("   graded on day D's matured ROI (any spend): a kill/cut is right if the day matured at <= -10%, wrong if >= +10%")
    rr = df[df.rule_on_D.notna()].copy()
    rr["day_label"] = rr.apply(lambda r: FB.label(r.roi_D, max(r.spend_D, FB.MIN_GRADE_SPEND)) if r.spend_D else "unclear", axis=1)
    cmp.append(f"  rule acted on {len(rr)} active adset-days; before the 10:00 window on {int(rr.rule_before_window.sum())}")
    rtally = Counter()
    for r in rr.itertuples():
        rule_g = FB.grade_decision("scale_down", r.day_label)
        agent_g = FB.grade_decision(r.proposed_if_escalated if r.action == "escalate" else r.action, r.day_label)
        word = lambda g: "unclear" if g["correct"] is None else "right" if g["correct"] else "wrong" if g["harmful"] else "neither"
        rtally[(f"rule {word(rule_g)}", f"agent {word(agent_g)}")] += 1
        cmp.append(f"  {r.decision_date} {r.adset_id} rule {r.rule_id} {r.rule_on_D} at {r.rule_local_time}, saw ROI "
                   f"{r.rule_roi_seen:+.2f} on ${r.rule_spend_seen:.2f} -> day matured at "
                   f"{'n/a' if pd.isna(r.roi_D) else f'{r.roi_D:+.2f}'} (${r.spend_D:.2f}) | agent at 10:00: {r.action}"
                   f"{'->' + str(r.proposed_if_escalated) if r.action == 'escalate' else ''} conf {r.confidence} | "
                   f"rule {word(rule_g)}, agent {word(agent_g)}")
    cmp.append("  tally: " + ", ".join(f"{a} / {b}: {v}" for (a, b), v in sorted(rtally.items())))
    cmp.append("\n== Agent vs human buyers on the same day (ui_* budget changes on D; only adsets a buyer touched)")
    bb = df[df.buyer_on_D.isin(["up", "down"])].copy()
    bb["grade_buyer"] = bb.apply(lambda r: FB.grade_decision("scale_up" if r.buyer_on_D == "up" else "scale_down", r.outcome_label), axis=1)
    cmp.append("  graded the same way for both sides (Step 5a), same labels:")
    cmp.append(f"    buyers: {accuracy(bb, 'grade_buyer')}")
    cmp.append(f"    agent:  {accuracy(bb)}")
    cmp.append("  structural difference: buyers acted during D with D's own data; the agent decided at 10:00 on history only")
    m = pd.crosstab(bb.agent_dir, bb.buyer_on_D)
    cmp.append(m.to_string())
    dis = bb[((bb.agent_dir == "up") & (bb.buyer_on_D == "down")) | ((bb.agent_dir == "down") & (bb.buyer_on_D == "up"))
             | ((bb.agent_dir == "neutral") & bb.buyer_on_D.isin(["up", "down"]))]
    tally = Counter()
    cmp.append(f"\n  disagreements: {len(dis)} (agent keep while buyer moved counts as a disagreement)")
    for r in dis.sort_values("spend_graded", ascending=False).itertuples():
        b_ok = None if r.outcome_label == "unclear" else (r.buyer_on_D == r.outcome_label)
        a_ok = None if r.outcome_label == "unclear" else (r.agent_dir == r.outcome_label)
        tally[("buyer right" if b_ok else "buyer wrong" if b_ok is False else "unclear",
               "agent right" if a_ok else "agent wrong" if a_ok is False else "unclear")] += 1
        amt = next((x for x in (r.amount, r.proposed_amount) if not pd.isna(x)), "")
        cmp.append(f"  {r.decision_date} {r.adset_id} {r.account} budget {r.current_budget} | agent {r.action} {amt}"
                   f" ({r.agent_dir}, conf {r.confidence}) | buyer {r.buyer_on_D} {r.buyer_net_change:+} "
                   f"{'[' + r.buyer_note_D + ']' if isinstance(r.buyer_note_D, str) else ''} | outcome {r.outcome_label} "
                   f"ROI {r.roi_graded if r.roi_graded is None else round(r.roi_graded, 2)} on ${r.spend_graded:.0f}")
    cmp.append("  tally: " + ", ".join(f"{k[0]} / {k[1]}: {v}" for k, v in sorted(tally.items())))

    cmp.append("\n== Timing: rules that fired on D before the agent's 10:00 window")
    for r in rr[rr.rule_before_window == True].itertuples():  # noqa: E712
        cmp.append(f"  {r.adset_id} {r.decision_date}: rule {r.rule_id} {r.rule_on_D} at {r.rule_local_time}, saw ROI "
                   f"{r.rule_roi_seen:+.2f} on ${r.rule_spend_seen:.2f} (immature); day matured at "
                   f"{'n/a' if pd.isna(r.roi_D) else f'{r.roi_D:+.2f}'}; agent at 10:00 on matured history: {r.action}")

    # ---- feedback loop on this week's ledger ----------------------------------------------------------
    # JSON round trip turns pandas NaN back into None: NaN passed `is not None` checks and put code-decided rows into
    # an "escalate" calibration segment (bug found on the first full run)
    rows = json.loads(df.drop(columns=["agent_dir"]).to_json(orient="records"))
    if path.name == "decisions.jsonl":      # the ledger belongs to the real agent run, not to test/ablation inputs
        FB.write_ledger(rows)
        cal, lessons = FB.update_calibration(rows), FB.propose_lessons(rows)
    else:
        cal, lessons = {}, []
    ev.append("\n== Feedback loop run on this week's ledger (out/ledger.jsonl -> out/calibration.json, out/lessons.json)")
    for seg, v in cal.items():
        ev.append(f"  {seg}: {v}")
    ev.append(f"  proposed lessons (need human activation): {len(lessons)}")
    for l in lessons[:8]:
        ev.append(f"    {l['id']} [{l['segment']}] {l['text']}")

    (OUT / ("evaluation.txt" if path.name == "decisions.jsonl" else f"evaluation_{path.stem}.txt")).write_text("\n".join(ev) + "\n", encoding="utf-8")
    (OUT / ("comparison.txt" if path.name == "decisions.jsonl" else f"comparison_{path.stem}.txt")).write_text("\n".join(cmp) + "\n", encoding="utf-8")
    print("\n".join(ev))
    print("\n".join(cmp[:60]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", default=str(OUT / "decisions.jsonl"))
    main(Path(ap.parse_args().decisions))
