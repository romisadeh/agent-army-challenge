"""#2 Triage: two evidence sub-scores computed by code, then one route per card (ARCHITECTURE §2, §7.1; plan §2a-b).

data_sufficiency  - is there enough data to judge?          low -> keep (wait for data)
signal_agreement  - does the data agree with itself?         low -> escalate any change
evidence_score    - min of the two; caps the LLM's confidence and is what gets reported.
"""
from __future__ import annotations

import numpy as np

from agent import config as C

NOTE_WORDS = ("keep", "hold", "regardless", "do not", "don't")   # not "test": it matches "testing new bid"
NOTE_WINDOW_H = 72                                                # an instruction older than 3 days is not a live conflict


def _sign(x: float | None, band: float = 0.05) -> int:
    if x is None:
        return 0
    return 1 if x > band else -1 if x < -band else 0


def scores(f: dict) -> dict:
    # ---- data_sufficiency ---------------------------------------------------------------------------
    spend_c = f["s3"] / (f["s3"] + C.K)
    days_c = min(f["mat_days"] / 3, 1.0)
    peer_c = min(f["peer_n"] / 20, 1.0) * (1.0 if f["peer_fallback"] <= 1 else 0.5)
    stab_c = 1 / (1 + 2 * float(np.std(f["rois"]))) if len(f["rois"]) >= 2 else 0.5
    ds = 0.4 * spend_c + 0.3 * days_c + 0.15 * peer_c + 0.15 * stab_c
    reasons = []
    if f["s3"] < C.K / 2:
        reasons.append(f"only ${f['s3']:.2f} of matured spend in 3 days (K = ${C.K:.0f})")
    if f["mat_days"] < 2:
        reasons.append(f"only {f['mat_days']} matured spend day(s)")
    if f["peer_fallback"] >= 2 or f["peer_n"] < C.MIN_PEERS:
        reasons.append(f"peer group coarse or small (n = {f['peer_n']})")
    if len(f["rois"]) >= 2 and np.std(f["rois"]) > 0.5:
        reasons.append(f"daily ROI unstable (sd {np.std(f['rois']):.2f})")

    # ---- signal_agreement ---------------------------------------------------------------------------
    pen, conflicts = 0.0, []
    ev = _sign(f["blended"])
    last, prior = f["last"], f["prior_roi"]
    if last is not None and prior is not None and abs(last["roi"]) > 0.1 and abs(prior) > 0.1 \
            and _sign(last["roi"]) != _sign(prior):
        pen += 0.35
        conflicts.append(f"yesterday ROI {last['roi']:+.2f} vs prior days {prior:+.2f}")
    if f["own_roi"] is not None and f["trend"]:
        if (f["own_roi"] > 0.05 and f["trend"] == "declining") or (f["own_roi"] < -0.05 and f["trend"] == "improving"):
            pen += 0.2
            conflicts.append(f"3-day level {f['own_roi']:+.2f} but trend {f['trend']}")
    buyer_conflict = False
    if f["net48"] != 0 and ev != 0 and np.sign(f["net48"]) != ev:
        pen += 0.3
        buyer_conflict = True
        conflicts.append(f"buyer moved budget {'up' if f['net48'] > 0 else 'down'} in last 48h against the evidence")
    live_notes = [n for n in f["notes"] if n["hours_ago"] <= NOTE_WINDOW_H and any(w in n["text"].lower() for w in NOTE_WORDS)]
    if live_notes:
        pen += 0.3
        buyer_conflict = True
        conflicts.append("buyer note carries an instruction: " + "; ".join(n["text"] for n in live_notes[-2:]))
    if any(r["action"] in ("cut", "kill") and r["response"] == "SUCCESS" for r in f["rules_today"]) and ev > 0:
        pen += 0.2
        conflicts.append("a rule cut/killed it this morning, but the matured evidence is positive")
    if 0.25 <= f["w"] <= 0.75 and f["own_roi"] is not None and f["peer_roi"] is not None \
            and abs(f["own_roi"]) > 0.1 and abs(f["peer_roi"]) > 0.1 and _sign(f["own_roi"]) != _sign(f["peer_roi"]):
        pen += 0.15
        conflicts.append(f"own ROI {f['own_roi']:+.2f} vs peers {f['peer_roi']:+.2f} at similar weight")
    sa = max(0.0, 1.0 - pen)

    return {"data_sufficiency": round(ds, 3), "signal_agreement": round(sa, 3), "evidence_score": round(min(ds, sa), 3),
            "buyer_conflict": buyer_conflict,
            "components": {"spend_vs_K": round(spend_c, 3), "matured_days": round(days_c, 3), "peers": round(peer_c, 3),
                           "stability": round(stab_c, 3), "conflict_penalty": round(pen, 2)},
            "uncertainty_reasons": reasons + conflicts}


def route(f: dict, sc: dict) -> tuple[str, str]:
    """Return (route, why). Order matters: data problems first, then materiality, then clear-cut code decisions."""
    if f["critical"]:
        return "escalate", "critical data flag: " + ", ".join(f["critical"])
    big = f["budget"] is not None and f["budget"] >= C.APPROVAL_BUDGET
    # a buyer lock does not exempt an immaterial adset: a keep under a lock executes nothing and the buyer is on it
    if f["s3"] < C.MATERIALITY_3D_SPEND and not big and not f["off_by_rule"]:
        return "auto_keep", f"below materiality (${f['s3']:.2f} < ${C.MATERIALITY_3D_SPEND:.0f} over 3 matured days)" \
            + ("; buyer lock noted" if f["lock"] else "")
    if (f["zero_rev_streak"] and f["s3"] >= C.DET_MIN_SPEND and f["budget"] is not None
            and f["budget"] < C.PAUSE_MAX_BUDGET and not f["lock"] and not f["off_by_rule"]):
        return "deterministic", f"{C.DET_MIN_DAYS}+ matured spend days with $0 revenue (${f['s3']:.2f} burned)"
    return "llm_review", "material and not clear-cut"
