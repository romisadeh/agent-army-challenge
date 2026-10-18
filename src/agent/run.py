"""Run the Adset Decision Agent slice end to end on the snapshot (shadow mode: nothing is written to Meta).

  python src/agent/run.py --dry-run          cards + routes only, no LLM            -> out/routes_dry.txt
  python src/agent/run.py                    full agent (Haiku, Sonnet 2nd opinion)  -> out/decisions.jsonl
  python src/agent/run.py --replay           same, from stored responses, no API key -> out/decisions.jsonl
  python src/agent/run.py --no-llm           ablation: code rule instead of the LLM  -> out/decisions_nollm.jsonl
  python src/agent/run.py --snapshot-pass    14:00-style partial-day cases (§2c)     -> out/snapshot_pass.jsonl
  options: --limit N (max llm_review cards per day, for dev runs), --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import config as C, features as F, guard as G, portfolio_guard as PG, triage as T  # noqa: E402
from agent.llm import BudgetExhausted, LLMClient, combine  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
SAMPLE_ADSETS = {"31302925337341", "31273956645572", "730122709357812221", "31626016833981"}
ASSUMPTIONS = [
    "decision at 10:00 local (UTC+3) on matured data through D-1",
    "budget at the window reconstructed from the buyer/rule logs (metadata / 100 if no change all week)",
    "expected impact: spend scales with budget for the rest of the day; marginal ROI = m x blended ROI, m in 0.5/0.75/1.0",
    "peer ROI = forward 1-3 day ROI of same-account adsets of similar age and ROI band, windows ending by D-1",
]
log = logging.getLogger("agent.run")


def sonnet_reasons(f: dict, sc: dict, haiku_conf: float | None, haiku_action: str | None = None) -> list[str]:
    """Second opinion where a wrong call costs real money or overrides a person (ARCHITECTURE §5.3). A lock alone is
    not a conflict: Sonnet runs on locked cards only when the evidence also argues against the buyer (code review 1).
    A Haiku escalation always gets a second opinion (Step 4g): the cheap model's hedge must not cost human attention
    unchecked."""
    return [w for w, c in (("budget >= $50", (f["budget"] or 0) >= C.SONNET_BUDGET),
                           (f"Haiku confidence < {C.SONNET_CONF_BELOW}", haiku_conf is not None and haiku_conf < C.SONNET_CONF_BELOW),
                           ("buyer lock + evidence against the buyer", f["lock"] and sc["buyer_conflict"]),
                           ("Haiku escalated", haiku_action == "escalate")) if c]


def llm_decide(client: LLMClient, card: dict, f: dict, sc: dict) -> dict:
    """Haiku first; Sonnet per sonnet_reasons(). Returns proposal info."""
    h, hm = client.propose(C.HAIKU, card)
    info = {"haiku": None, "sonnet": None, "combine_rule": None, "error": hm["error"], "cost_usd": hm["cost_usd"]}
    if h is None:
        return {**info, "final": None}
    info["haiku"] = {"action": h.action, "amount": h.new_daily_budget, "confidence": h.confidence, "raw_key": hm["raw_key"]}
    why = sonnet_reasons(f, sc, h.confidence, h.action)
    if not why:
        return {**info, "final": h, "model": C.HAIKU}
    s, sm = client.propose(C.SONNET, card)
    info["cost_usd"] += sm["cost_usd"]
    if s is None:
        info["error"] = sm["error"]
        return {**info, "final": None, "sonnet_trigger": why}
    info["sonnet"] = {"action": s.action, "amount": s.new_daily_budget, "confidence": s.confidence, "raw_key": sm["raw_key"]}
    final, rule = combine(h, s, f["budget"])
    return {**info, "final": final, "combine_rule": rule, "sonnet_trigger": why, "model": f"{C.HAIKU}+{C.SONNET}"}


def decide_card(built: dict, mode: str, client: LLMClient | None, state: dict) -> dict:
    card, f = built["card"], built["f"]
    sc = T.scores(f)
    route, why = T.route(f, sc)
    flags = list(card["data_quality_flags"])
    llm, reasoning, key_ev, model, llm_unc = {}, "", [], "code", []
    action, amount, llm_conf, pie, esc_reason = "keep", None, None, None, "llm_escalated"

    if route == "auto_keep":
        reasoning = f"Code: {why}. Kept without an LLM call (materiality gate)."
    elif route == "deterministic":
        action, reasoning = "pause", f"Code: {why}. Clear-cut; overlaps the legacy kill rules."
    elif route == "escalate":
        action, esc_reason = "escalate", "critical_data_flag"
        reasoning = f"Code: {why}. A proposal built on this data would be worthless; a human must check the adset first."
    elif mode == "nollm":
        action, amount = G.nollm_rule(f)
        model, reasoning = "code_rule", "No-LLM ablation: blended-ROI sign rule."
    elif state.get("budget_out") or (state["limit"] is not None and state["llm_cards"] >= state["limit"]):
        flags.append("llm_budget_exhausted" if state.get("budget_out") else "llm_skipped_by_limit")
        reasoning = "LLM not called (budget or --limit); code-only keep (ARCHITECTURE §4)."
    else:
        state["llm_cards"] += 1
        try:
            llm = llm_decide(client, card, f, sc)
        except BudgetExhausted as e:
            state["budget_out"] = True
            log.warning("%s", e)
            flags.append("llm_budget_exhausted")
            llm = {"final": None, "error": None, "cost_usd": 0.0}
            reasoning = "LLM budget exhausted; code-only keep (ARCHITECTURE §4)."
        p = llm.get("final")
        if p is None and llm.get("error"):
            flags.append("llm_output_invalid")
            action, esc_reason = "escalate", "llm_output_invalid"
            reasoning = f"LLM output invalid after retry ({llm['error']}); sent to a human."
        elif p is not None:
            action, amount, llm_conf, pie = p.action, p.new_daily_budget, p.confidence, p.proposed_if_escalated
            reasoning, key_ev, llm_unc, model = p.reasoning, p.key_evidence, p.uncertainty, llm["model"]

    g = G.apply(f, sc, route, action, amount, llm_conf, pie, escalation_reason=esc_reason)
    if card["decision_date"] == C.WEEK_END.date().isoformat():
        flags.append("grading_day_may_be_incomplete")
    return {
        "adset_id": f["adset_id"], "decision_date": card["decision_date"], "action": g["action"], "amount": g["amount"],
        "confidence": g["confidence"], "reasoning": reasoning, "data_quality_flags": flags,
        "account": f["acc"], "route": route, "route_reason": why, "model": model, "prompt_version": C.PROMPT_VERSION,
        "current_budget": f["budget"], "age_spend_days": f["age"], "last_3d_roi": f["own_roi"],
        "last_3d_spend": round(f["s3"], 2),
        "expected_profit_impact": g["expected_profit_impact"],
        "evidence": {k: sc[k] for k in ("data_sufficiency", "signal_agreement", "evidence_score", "components")},
        "uncertainty_reasons": sc["uncertainty_reasons"] + [f"LLM: {u}" for u in llm_unc],
        "key_evidence": key_ev,
        "llm_raw": {k: llm.get(k) for k in ("haiku", "sonnet", "combine_rule", "sonnet_trigger")} if llm else None,
        "llm_action": action if model not in ("code", "code_rule") else None, "llm_confidence": llm_conf,
        "guard_adjustments": g["guard_adjustments"], "requires_approval": g["requires_approval"],
        "approval_reason": g["approval_reason"], "proposed_if_escalated": g["proposed_if_escalated"],
        "proposed_amount": g["proposed_amount"], "default_if_unanswered": g["default_if_unanswered"],
        "inputs_used": {"matured_dates": sorted({str(d.date()) for d in built["sources"]["perf_dates"]}),
                        "peer_segment": card["peers"]["segment"], "peer_n": card["peers"]["n"],
                        "peer_window_ends": str(built["sources"]["peer_max_date"].date()) if built["sources"]["peer_max_date"] is not None else None,
                        "events_before_window": len(built["sources"]["event_ts"]), "budget_source": f["budget_src"]},
        "assumptions": ASSUMPTIONS,
        "cost_usd": round(llm.get("cost_usd", 0.0), 6) if llm else 0.0,   # cost when first decided (replay-stable)
    }


def run(mode: str, replay: bool, limit: int | None, dates: list[pd.Timestamp], tag: str | None = None) -> None:
    data = F.load()
    client = LLMClient(replay=replay) if mode == "agent" else None
    state = {"limit": limit, "llm_cards": 0, "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out_rows, report, pg_lines = [], [], []
    (OUT / "cards_sample").mkdir(parents=True, exist_ok=True)
    for D in dates:
        state["llm_cards"] = 0
        peers, (act, info), wts = F.peer_table(data, D), F.active_adsets(data, D), F.window_ts(D)
        built_all = []
        for a in act:
            b = F.build_card(data, a, D, peers)
            F.assert_no_leakage(b, D, wts)
            built_all.append(b)
        report.append(f"{D.date()} population: {json.dumps(info)}")
        report.append(f"{D.date()} leakage assertion passed on {len(built_all)} cards (all perf <= D-1, peer windows <= D-1, events < window)")
        if mode == "dry":
            cnt, sp, lk = Counter(), Counter(), Counter()
            for b in built_all:
                sc = T.scores(b["f"])
                r, _ = T.route(b["f"], sc)
                cnt[r] += 1
                sp[r] += b["f"]["s3"]
                if r == "llm_review":
                    lk["locked"] += b["f"]["lock"]
                    lk["locked_with_buyer_conflict"] += b["f"]["lock"] and sc["buyer_conflict"]
                    lk["budget_ge_50"] += (b["f"]["budget"] or 0) >= C.SONNET_BUDGET
                    lk["sonnet_certain (lock+conflict or >= $50)"] += bool(sonnet_reasons(b["f"], sc, None))
            tot = sum(sp.values())
            report.append(f"{D.date()} routes: " + ", ".join(f"{k} {cnt[k]} adsets / {sp[k] / tot:.0%} of 3-day spend" for k in sorted(cnt)))
            report.append(f"{D.date()} llm_review cards: {dict(lk)} (plus Sonnet when Haiku confidence < {C.SONNET_CONF_BELOW})")
            continue
        day = [decide_card(b, mode, client, state) for b in built_all]
        for b, d in zip(built_all, day):
            if b["f"]["adset_id"] in SAMPLE_ADSETS or (d["route"] == "llm_review" and sum(
                    1 for x in day[: day.index(d)] if x["route"] == "llm_review") < 3):
                (OUT / "cards_sample" / f"{d['adset_id']}_{d['decision_date']}.json").write_text(
                    json.dumps(b["card"], indent=1, default=str), encoding="utf-8")
        pg_lines += PG.apply(day, {b["f"]["adset_id"]: b["f"] for b in built_all}, data.perf, D)
        out_rows += day
        c = Counter(d["action"] for d in day)
        report.append(f"{D.date()} actions: {dict(c)}; routes: {dict(Counter(d['route'] for d in day))}")

    if mode == "dry":
        (OUT / "routes_dry.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    else:
        # a --limit run is a dev run: it must never overwrite the full run's files
        suffix = f"_{tag}" if tag else "_dev" if limit is not None else ("" if mode == "agent" else "_nollm")
        with (OUT / f"decisions{suffix}.jsonl").open("w", encoding="utf-8") as fh:
            for r in out_rows:
                fh.write(json.dumps(r, default=str) + "\n")
        (OUT / f"portfolio_guard{suffix}.txt").write_text("\n".join(pg_lines) + "\n", encoding="utf-8")
        meta = {"run_ts": state["run_ts"], "mode": mode, "replay": replay, "limit": limit,
                "dates": [str(d.date()) for d in dates], "prompt_version": C.PROMPT_VERSION,
                "DS_MIN": C.DS_MIN, "SA_MIN": C.SA_MIN, "MATERIALITY_3D_SPEND": C.MATERIALITY_3D_SPEND,
                "llm_calls_billed": client.calls if client else 0,
                "cost_billed_usd": round(client.spent, 4) if client else 0.0,
                "cost_of_decisions_usd": round(sum(r["cost_usd"] for r in out_rows), 4)}
        (OUT / f"run_meta{suffix}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        report.append(f"run meta: {json.dumps(meta)}")
    print("\n".join(report))


# ---------------------------------------------------------------------------------------------------------
SNAPSHOT_CASES = [  # (adset, D, how to pick the rule-log reading)
    ("31273956645572", "2026-06-11", "nearest_14"),   # case 4.3, 14:30 local
    ("31626016833981", "2026-06-10", "nearest_14"),
    ("31626016833981", "2026-06-12", "nearest_14"),
    ("31302925337341", "2026-06-08", "first"),        # case 4.1, R10 at 05:00 local (outside the 3-day window)
]


def snapshot_pass(replay: bool) -> None:
    """Partial-day cards from the only intraday data there is: rule_executions readings (ACC-04, rule-touched)."""
    data = F.load()
    client = LLMClient(replay=replay)
    state = {"limit": None, "llm_cards": 0, "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows, lines = [], []
    for adset, ds, how in SNAPSHOT_CASES:
        D = pd.Timestamp(ds)
        rr = data.rules[(data.rules.adset_id == adset) & (data.rules.d == D) & data.rules.spend_seen.notna()].sort_values("ts")
        if how == "first":
            r = rr.iloc[0]
        else:
            r = rr.iloc[(rr.ts - F.window_ts(D, 14)).abs().argsort().iloc[0]]
        wts = r.ts
        b = F.build_card(data, adset, D, F.peer_table(data, D), wts=wts)
        F.assert_no_leakage(b, D, wts)
        card, f = b["card"], b["f"]
        hl = r.hour_local
        df = F.delay_factor(hl)
        rev_c = r.rev_seen / df
        own = (f["r3"] + rev_c) / (f["s3"] + r.spend_seen) - 1 if (f["s3"] + r.spend_seen) > 0 else None
        w = (f["s3"] + r.spend_seen) / (f["s3"] + r.spend_seen + C.K)
        blended = w * own + (1 - w) * f["peer_roi"] if own is not None and f["peer_roi"] is not None else own
        card["window"] = f"snapshot at {int(hl):02d}:{int(hl % 1 * 60):02d} local (UTC+3); today_partial included"
        card["today_partial"] = {"local_time": f"{int(hl):02d}:{int(hl % 1 * 60):02d}", "spend_seen": round(r.spend_seen, 2),
                                 "revenue_seen": round(r.rev_seen, 2), "roi_seen": round(r.rev_seen / r.spend_seen - 1, 3),
                                 "delay_factor": df, "roi_corrected": round(rev_c / r.spend_seen - 1, 3),
                                 "share_of_day_elapsed": round(hl / 24, 2)}
        card["blended_roi_incl_today"] = round(blended, 3) if blended is not None else None
        f = {**f, "blended": blended}
        b2 = {**b, "card": card, "f": f}
        d = decide_card(b2, "agent", client, state)
        if hl < 12 and d["action"] in ("scale_down", "pause") and (f["own_roi"] is None or f["own_roi"] >= 0):
            d["guard_adjustments"].append("maturity gate: no negative action on today's ROI before 12:00 local (ARCHITECTURE §4)")
            d["action"], d["amount"] = "keep", None
        d["snapshot"] = card["today_partial"]
        d["rule_actual"] = {"rule": r.rule_id, "action": r.kind, "response": r.response,
                            "budget_from": r.budget_fb, "budget_to": r.budget_set}
        rows.append(d)
        lines.append(f"{adset} {ds} @ {card['today_partial']['local_time']} local: rule {r.rule_id} {r.kind} ({r.response}) "
                     f"{r.budget_fb} -> {r.budget_set} | agent {d['action']} {d['amount'] or ''} conf {d['confidence']} "
                     f"| roi_seen {card['today_partial']['roi_seen']} corrected {card['today_partial']['roi_corrected']} "
                     f"blended {card['blended_roi_incl_today']} | adj: {d['guard_adjustments']}")
        (OUT / "cards_sample" / f"snapshot_{adset}_{ds}.json").write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
    with (OUT / "snapshot_pass.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    lines.append(f"LLM calls: {client.calls}, cost ${client.spent:.4f}")
    (OUT / "snapshot_pass.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--snapshot-pass", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--date")
    ap.add_argument("--ds-min", type=float, help="sensitivity: override DS_MIN (use with --replay and --tag)")
    ap.add_argument("--sa-min", type=float, help="sensitivity: override SA_MIN (use with --replay and --tag)")
    ap.add_argument("--tag", help="output suffix, e.g. sens_ds025 -> out/decisions_sens_ds025.jsonl")
    ap.add_argument("--prompt", choices=["v2", "v3", "v3b"], help="system prompt version (v3 = Step 5b revision; needs --tag)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if (a.ds_min is not None or a.sa_min is not None) and not a.tag:
        ap.error("threshold overrides need --tag so they never overwrite the main run")
    if a.prompt and a.prompt != "v2" and not a.tag:
        ap.error("--prompt v3 needs --tag so it never overwrites the main run")
    if a.prompt:
        C.PROMPT_VERSION = f"adset-agent-{a.prompt}"
    if a.ds_min is not None:
        C.DS_MIN = a.ds_min
    if a.sa_min is not None:
        C.SA_MIN = a.sa_min
    if a.snapshot_pass:
        snapshot_pass(a.replay)
    else:
        run("dry" if a.dry_run else "nollm" if a.no_llm else "agent", a.replay, a.limit,
            [pd.Timestamp(a.date)] if a.date else C.DECISION_DATES, a.tag)
