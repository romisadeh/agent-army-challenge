"""#4 (per card, no writes): turn a proposal into a decision the Executor could run. Code, not prompt, holds the limits.

Order: normalise -> sufficiency gate -> agreement gate -> symmetric step caps -> §4 approval triggers -> confidence cap.
Every change to the proposal is recorded in guard_adjustments, so a reader can see when the LLM was overruled.
"""
from __future__ import annotations

from agent import config as C

CHANGES = ("scale_up", "scale_down", "pause")
REMAINING_DAY = (24 - C.WINDOW_LOCAL_H) / 24      # share of the day still to run after a 10:00 decision


def expected_impact(f: dict, action: str, amount: float | None) -> dict:
    """Code-computed, 1-day expected effect of the action vs keep (Task A §3.1 logic): spend scales with budget;
    added/removed spend earns m x blended ROI (m = 0.5/0.75/1.0); a pause removes average spend at the ROI range."""
    b, roi, spend = f["budget"], f["blended"], f["exp_spend"] * REMAINING_DAY
    zero = {"spend": 0.0, "low": 0.0, "central": 0.0, "high": 0.0}
    if roi is None or not b or spend <= 0 or action not in CHANGES:
        return zero
    if action == "pause":
        cands = sorted(x for x in (f["own_roi"], f["peer_roi"], roi) if x is not None)
        prof = sorted(-spend * r for r in (cands[0], roi, cands[-1]))
        return {"spend": round(-spend, 2), "low": round(prof[0], 2), "central": round(-spend * roi, 2), "high": round(prof[2], 2)}
    if amount is None:
        return zero
    ds = spend * (amount / b - 1)
    p = {k: ds * m * roi for k, m in C.M_SCENARIOS.items()}
    lo, hi = sorted((p["low"], p["high"]))
    return {"spend": round(ds, 2), "low": round(lo, 2), "central": round(p["central"], 2), "high": round(hi, 2)}


def apply(f: dict, sc: dict, route: str, action: str, amount: float | None, llm_conf: float | None,
          proposed_if_escalated: str | None = None, escalation_reason: str = "llm_escalated") -> dict:
    """escalation_reason names who escalated when no gate did: 'llm_escalated' (the model chose to, e.g. a buyer
    conflict or a note instruction), 'llm_output_invalid', or 'critical_data_flag' (code route)."""
    adj, triggers = [], []
    b = f["budget"]
    proposal = {"action": action, "amount": amount}

    # ---- normalise ---------------------------------------------------------------------------------
    if action in ("scale_up", "scale_down") and (amount is None or not b):
        adj.append(f"{action} without a usable amount/budget -> keep")
        action, amount = "keep", None
    if action == "scale_up" and amount is not None and amount <= b:
        adj.append(f"scale_up to {amount} is not above budget {b} -> keep")
        action, amount = "keep", None
    if action == "scale_down" and amount is not None and amount >= b:
        adj.append(f"scale_down to {amount} is not below budget {b} -> keep")
        action, amount = "keep", None
    if action in ("keep", "pause", "escalate"):
        amount = None if action != "escalate" else amount

    # ---- off by a rule this morning: anything other than keep(off)/escalate is a reactivation -----------
    if f["off_by_rule"] and action in ("scale_up", "scale_down"):
        triggers.append("reactivation")

    # ---- gates (deterministic route is exempt: it is defined as clear-cut) -------------------------------
    if route != "deterministic":
        if action in CHANGES and sc["data_sufficiency"] < C.DS_MIN:
            adj.append(f"data_sufficiency {sc['data_sufficiency']:.2f} < {C.DS_MIN} -> keep (wait for data)")
            action, amount = "keep", None
        if action in CHANGES and sc["signal_agreement"] < C.SA_MIN:
            adj.append(f"signal_agreement {sc['signal_agreement']:.2f} < {C.SA_MIN} -> escalate (proposal kept)")
            proposed_if_escalated, action = action, "escalate"
            triggers.append("insufficient_evidence")

    # ---- symmetric step caps --------------------------------------------------------------------------
    if action == "scale_up" and sc["evidence_score"] < C.UP_FULL_STEP_MIN:
        cap = round(b * (1 + C.THIN_UP_STEP_PCT), 2)
        if amount > cap:
            adj.append(f"thin evidence ({sc['evidence_score']:.2f} < {C.UP_FULL_STEP_MIN}): scale_up capped at +10% ({amount} -> {cap})")
            amount = cap
    if action in ("scale_up", "scale_down"):
        # a step within half a point of the 20% limit is the LLM rounding a 20% step: snap it to exactly 20%
        # (bug: 17.78 -> 21.34 = +20.02% triggered approval; Step 5a)
        if C.MAX_STEP_PCT * b < abs(amount - b) <= (C.MAX_STEP_PCT + 0.005) * b:
            snapped = round(b * (1 + C.MAX_STEP_PCT if action == "scale_up" else 1 - C.MAX_STEP_PCT), 2)
            adj.append(f"step {abs(amount - b) / b:.2%} snapped to 20% ({amount} -> {snapped})")
            amount = snapped
        step = abs(amount - b)
        if step > C.MAX_STEP_PCT * b + 0.01 or step > C.MAX_STEP_USD:   # 1-cent tolerance for rounding to cents
            triggers.append("change_gt_20pct")

    # ---- remaining §4 approval triggers ---------------------------------------------------------------
    if action == "pause":
        if f["profit_days_3"] > 0:
            triggers.append("pause_with_profitable_day_last3")
        if b is None or b >= C.PAUSE_MAX_BUDGET or f["loss_days_3"] < C.PAUSE_MIN_LOSS_DAYS:
            triggers.append("pause_outside_autonomy")
    if b is not None and b >= C.APPROVAL_BUDGET:
        triggers.append("budget_ge_100")
    if f["lock"]:
        triggers.append("buyer_lock")
    if f["critical"]:
        triggers.append("critical_data_flag")
    # insufficient_evidence only comes from the sufficiency/agreement gates above; other escalations say who escalated
    if action == "escalate" and "insufficient_evidence" not in triggers and escalation_reason not in triggers:
        triggers.append(escalation_reason)

    # requires_approval only when there is something to execute: a keep under a lock changes nothing.
    requires_approval = action == "escalate" or (action in CHANGES and bool(triggers))
    conf = sc["evidence_score"] if llm_conf is None else min(llm_conf, sc["evidence_score"])
    if llm_conf is not None and llm_conf > sc["evidence_score"]:
        adj.append(f"confidence capped by evidence_score ({llm_conf:.2f} -> {sc['evidence_score']:.2f})")

    # an escalation carries the proposal (action + amount) to the human; impact is computed for that proposal
    esc = action == "escalate"
    impact_action = (proposed_if_escalated or "keep") if esc else action
    return {"action": action, "amount": None if esc or amount is None else round(amount, 2),
            "confidence": round(conf, 3), "requires_approval": requires_approval,
            "approval_reason": sorted(set(triggers)), "guard_adjustments": adj,
            "proposed_if_escalated": proposed_if_escalated if esc else None,
            "proposed_amount": round(amount, 2) if esc and amount is not None else None,
            "default_if_unanswered": "keep",
            "expected_profit_impact": expected_impact(f, impact_action, amount),
            "proposal_before_guard": proposal}


def nollm_rule(f: dict) -> tuple[str, float | None]:
    """Ablation (plan item 13): what a plain code rule does with an llm_review card."""
    b, r = f["budget"], f["blended"]
    if r is None or not b:
        return "keep", None
    if r > 0.10 and (f["own_roi"] or 0) > 0 and f["profit_days_3"] >= 2:
        return "scale_up", round(b * (1 + C.MAX_STEP_PCT), 2)
    if r < -0.15 and f["loss_days_3"] >= 2:
        if b < C.PAUSE_MAX_BUDGET and (f["own_roi"] or 0) < -0.5:
            return "pause", None
        return "scale_down", round(b * (1 - C.MAX_STEP_PCT), 2)
    return "keep", None
