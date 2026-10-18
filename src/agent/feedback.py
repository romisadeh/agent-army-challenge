"""#5 Auditor / Learner (stub + the parts that run this week): grading, ledger, calibration, lessons.

The loop (ARCHITECTURE §7.2), without retraining anything:
  decide -> ledger (decision + card hash) -> T+3 grade on matured revenue -> calibration per segment
         -> thresholds move inside §4 bounds, and lessons go into future cards for the same segment.

grade_decision() and update_calibration() run on this week's ledger. propose_lessons() writes *proposed* lessons
that a human must activate; lessons_for() is what features.build_card() would call (off by default: week 1 has none).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from agent import config as C

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "out" / "ledger.jsonl"
CALIBRATION = ROOT / "out" / "calibration.json"
LESSONS = ROOT / "out" / "lessons.json"

DIR = {"scale_up": "up", "scale_down": "down", "pause": "down", "keep": "neutral"}
LABEL_BAND = 0.10       # outcome ROI beyond +-10% = the adset deserved more / less money
MIN_GRADE_SPEND = 1.0   # less than $1 of outcome spend = unclear (nothing to judge)


def outcome(perf: pd.DataFrame, adset: str, D: pd.Timestamp) -> dict:
    """Matured outcome on D and D+1 (D+1 preferred where it exists, plan item 15)."""
    rows = perf[(perf.adset_id == adset) & (perf.d >= D) & (perf.d <= D + pd.Timedelta(days=1))]
    o = {}
    for k, day in (("D", D), ("D1", D + pd.Timedelta(days=1))):
        r = rows[rows.d == day]
        o[f"spend_{k}"] = float(r.spend.sum()) if day <= C.WEEK_END else None
        o[f"rev_{k}"] = float(r.rev.sum()) if day <= C.WEEK_END else None
    s = o["spend_D"] + (o["spend_D1"] or 0)
    rv = o["rev_D"] + (o["rev_D1"] or 0)
    o["grading_days"] = "D+D1" if o["spend_D1"] is not None else "D only"
    o["roi_graded"] = rv / s - 1 if s > 0 else None
    o["spend_graded"] = s
    o["roi_D"] = o["rev_D"] / o["spend_D"] - 1 if o["spend_D"] > 0 else None
    return o


def label(roi: float | None, spend: float) -> str:
    if roi is None or spend < MIN_GRADE_SPEND:
        return "unclear"
    return "up" if roi >= LABEL_BAND else "down" if roi <= -LABEL_BAND else "neutral"


def grade_decision(action: str, lab: str) -> dict:
    """Direction grade. Day D's outcome happened under what buyers/rules actually did, so this grades whether the
    adset deserved more/less money, not the counterfactual of the agent's own action (plan item 15)."""
    d = DIR.get(action)
    if d is None or lab == "unclear":
        return {"correct": None, "harmful": None}
    return {"correct": d == lab, "harmful": {d, lab} == {"up", "down"}}


USD_MATERIALITY = 0.50  # regret at or below 50 cents = right in $ (assumption, stated in RESULTS.md)
M_CENTRAL = C.M_SCENARIOS["central"]
REMAINING_DAY = (24 - C.WINDOW_LOCAL_H) / 24


def dollar_grade(action: str | None, amount: float | None, budget: float | None, spend_d: float | None,
                 roi_d: float | None) -> dict:
    """$ grade (ARCHITECTURE §7.2; added after the first full run, Step 5a). On day D's matured outcome, the profit
    effect of the action vs keep for the rest of the day, and its regret against the best simple alternative
    {keep, +20%, -20%, the action itself}. Missed profit on keeps and realized loss on changes both show up as regret.
    Same counterfactual assumptions as the primary metric (spend scales with budget, marginal ROI m = 0.75)."""
    if action not in DIR or not spend_d or roi_d is None or (isinstance(roi_d, float) and roi_d != roi_d):
        return {"right": None, "delta": None, "regret": None}
    rest = spend_d * REMAINING_DAY
    if action == "pause":
        delta = -rest * roi_d
    elif action in ("scale_up", "scale_down") and amount and budget:
        delta = rest * (amount / budget - 1) * M_CENTRAL * roi_d
    else:
        delta = 0.0
    best = max(0.0, 0.2 * M_CENTRAL * rest * abs(roi_d), delta)
    regret = best - delta
    return {"right": regret <= USD_MATERIALITY, "delta": round(delta, 3), "regret": round(regret, 3)}


def write_ledger(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")


def segment(row: dict) -> str:
    age = row.get("age_spend_days") or 0
    return f"age {'1-3' if age <= 3 else '4+'} / {DIR.get(row.get('llm_action') or row['action'], 'escalate')}"


def update_calibration(rows: list[dict], min_n: int = 10) -> dict:
    """Per segment: stated (raw LLM) confidence vs how often the decision was right IN $ (regret <= $0.50; Step 5a:
    the first stub used the +-10% direction band, which graded every keep on a non-flat day as wrong and would have
    taught the agent to be more cautious when it was already too passive). Overconfident segments get a higher
    signal_agreement threshold next week, so they escalate more; bounded to +-0.15 (ARCHITECTURE §7.2 #5)."""
    g = defaultdict(list)
    for r in rows:
        if r.get("llm_confidence") is not None and r["grade_usd"]["right"] is not None:
            g[segment(r)].append((r["llm_confidence"], r["grade_usd"]["right"]))
    out = {}
    for seg, xs in sorted(g.items()):
        conf = sum(c for c, _ in xs) / len(xs)
        hit = sum(k for _, k in xs) / len(xs)
        gap = conf - hit
        shift = 0.0
        if len(xs) >= min_n and abs(gap) > 0.10:
            shift = max(-0.15, min(0.15, round(gap / 2, 2)))
        out[seg] = {"n": len(xs), "mean_confidence": round(conf, 3), "hit_rate": round(hit, 3),
                    "overconfidence": round(gap, 3), "sa_min_next_week": round(C.SA_MIN + shift, 2),
                    "status": "applied" if shift else ("too few cases" if len(xs) < min_n else "calibrated")}
    CALIBRATION.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def propose_lessons(rows: list[dict], min_cases: int = 3) -> list[dict]:
    """Stub of the lesson writer. Groups decisions that cost money (regret > $0.50) by (segment, pattern); >= 3 cases
    -> a proposed lesson, ranked by $ at stake, that a human activates. In production an LLM would word it; here a
    template does. Lessons expire when later grades contradict them (not implemented: needs more than one week)."""
    g = defaultdict(list)
    for r in rows:
        u = r["grade_usd"]
        if u["right"] is False:
            hist = "positive" if (r.get("last_3d_roi") or 0) > 0 else "negative"
            day = "positive" if (r.get("roi_D") or 0) > 0 else "negative"
            g[(segment(r), f"{r['action']} with {hist} 3-day history, day matured {day}")].append((r["adset_id"], u["regret"]))
    ranked = sorted(g.items(), key=lambda kv: -sum(x for _, x in kv[1]))
    lessons = [{"id": f"L-{i:03d}", "segment": seg,
                "text": f"{pat}: cost ${sum(x for _, x in v):.2f} in regret over {len(v)} cases (week of 2026-06-10)",
                "evidence": [a for a, _ in v][:10], "status": "proposed"}
               for i, ((seg, pat), v) in enumerate(ranked, 1) if len(v) >= min_cases]
    LESSONS.write_text(json.dumps(lessons, indent=1), encoding="utf-8")
    return lessons


def lessons_for(seg: str, cap: int = 5) -> list[str]:
    """What build_card() injects: active lessons for this segment only, capped (ARCHITECTURE §6.1)."""
    if not LESSONS.exists():
        return []
    ls = json.loads(LESSONS.read_text(encoding="utf-8"))
    return [f"{x['id']}: {x['text']}" for x in ls if x["status"] == "active" and x["segment"] == seg][:cap]
