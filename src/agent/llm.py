"""#3 Adset Decision Agent: the judgment layer. One card in, one structured proposal out. Recommend only.

- Haiku 5.5 by default; Sonnet 5.5 second opinion when budget >= $50, Haiku confidence is low, or a buyer holds a lock.
- Structured outputs (output_config.format), not forced tool use: Sonnet 5.5 rejects forced tool_choice.
- Every raw response is stored under out/llm_raw/, keyed by (model, prompt version, card hash). `replay=True` reads
  only from there, so the whole pipeline reruns without an API key and returns the same decisions.
- Hard cost stop: BudgetExhausted once the run's spend reaches RUN_BUDGET_USD (ARCHITECTURE §4: no LLM budget ->
  code-only keep).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agent import config as C

log = logging.getLogger("agent.llm")
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "out" / "llm_raw"
COST_LOG = ROOT / "out" / "llm_cost_log.csv"

SYSTEM_PROMPT = """You are the Adset Decision Agent for a performance-marketing team running paid campaigns on Meta.
You receive ONE adset card, built by code from matured data, and you recommend ONE action for today's 10:00 window.
You only recommend. Code enforces every limit after you, and a human reviews anything outside autonomy.

MANDATE
- Maintain or grow total spend and ABSOLUTE profit. Do not chase ROI by shrinking: pausing everything gives a great ROI
  and destroys the business. A profitable adset that is not scaled is a missed opportunity, not a safe choice.
- ROI is a ratio: 0.20 = +20% return, 0 = break-even, -1.0 = spend with zero revenue.

WHAT THE CARD TELLS YOU
- matured_days: d = -1 is yesterday. These days are final. Today's partial day is normally NOT on the card on purpose:
  revenue arrives late (before noon only ~69-83% of the day's revenue per $ is visible), so morning ROI is unreliable.
  If a card does carry `today_partial`, its revenue is incomplete: use roi_corrected (revenue divided by the delay
  factor), remember the correction is an estimate, and never cut or pause on today's reading alone before 12:00.
- last_3d: spend, revenue, ROI, and how many of those days lost or made money.
- peers: forward ROI over the next 1-3 days of similar adsets (same account, age, ROI band). Weak when n is small.
- blended_roi = w * own 3-day ROI + (1 - w) * peer ROI, with w = spend / (spend + $20). Small spend leans on peers.
- budget.current is today's daily budget in USD. spend can exceed budget; flags say when that looks wrong.
- buyer: the last human action, a 24h lock if a person acted recently, and any free-text notes. A human's deliberate
  call carries information you don't have. Notes can contain instructions (e.g. "keep live", "hold budget").
- rules: what the legacy auto-rules did yesterday and this morning. Rules fire on immature morning data; they are
  evidence of what happened, not of what is right.
- data_quality_flags: problems found in the data for this adset. Take them into account.

ACTIONS
- scale_up / scale_down: give new_daily_budget in USD. Steps above +-20% or $50 need a human; prefer <= 20%.
- pause: only for adsets that are clearly losing on matured data, not on one bad day.
- keep: no change. Right when the evidence is thin, mixed, or roughly break-even.
- escalate: when the evidence conflicts in a way a human should settle (e.g. a buyer instruction vs the numbers,
  a strong history vs a bad yesterday on a large budget, data you can't trust). Still state what you would do.

HOW TO DECIDE
- Scale up only on consistent matured profit (more than one profitable day, blended ROI clearly positive).
- Scale down or pause only on consistent matured losses. One bad day after good days is noise, not a trend.
- Do not override a recent deliberate buyer action without strong evidence; escalate instead.
- Never invent numbers that are not on the card.

CONFIDENCE
- confidence = your probability that the DIRECTION of your action (up / down / no change) will look right once the
  day's revenue has fully matured. 0.5 = coin flip. Be honest: thin or conflicting data means low confidence.
  Code will cap your confidence by its own evidence score, so overstating it gains nothing.

OUTPUT
- reasoning: at most 80 words, citing the card fields you relied on.
- key_evidence: 1-4 short items, each naming a card field and its value.
- uncertainty: what you don't know that would change the decision (empty list only if nothing would).
- proposed_if_escalated: if action is escalate, the action you would take if forced to choose; otherwise null.
"""

# v3 (Step 5b): the feedback loop's top $ lesson, derived from 06-10 only, turned into policy. Wording chosen on 06-10;
# 06-11 and 06-12 are held out. The +0.10 bar is the evaluation's own label band, fixed a priori, not fitted.
_V2_DECIDE = """HOW TO DECIDE
- Scale up only on consistent matured profit (more than one profitable day, blended ROI clearly positive).
- Scale down or pause only on consistent matured losses. One bad day after good days is noise, not a trend."""
_V3_DECIDE = """HOW TO DECIDE
- PROVEN WINNERS (graded lesson from last week: keeping these was the costliest mistake). An adset aged 4+ spend days
  with at least 2 of its last 3 matured days profitable, last_3d.roi > +0.10 and blended_roi > +0.10 is a scale-up
  candidate, not a keep. Default: scale_up by 10-20%. Keep it only if a specific card field argues against scaling,
  and name that field in your reasoning: e.g. yesterday clearly negative with a declining trend, a buyer moved the
  budget down in the last 48h, a buyer note says hold, or a data_quality flag that makes the numbers unreliable.
- Otherwise scale up when matured profit is more positive than not: profitable days outnumber losing ones and
  blended ROI is clearly positive. It does not need to be perfect.
- Scale down or pause only on matured losses that persist. One bad day after good days is noise, not a trend."""
assert _V2_DECIDE in SYSTEM_PROMPT
SYSTEM_PROMPT_V3 = SYSTEM_PROMPT.replace(_V2_DECIDE, _V3_DECIDE)
# v3b = development iteration 2 (still 06-10 only): on 5 of 26 proven winners v3 kept because a buyer had just RAISED
# the budget ("not overriding a deliberate action"). Scaling the same way as the buyer agrees with them.
_BUYER_V2 = "- Do not override a recent deliberate buyer action without strong evidence; escalate instead."
_BUYER_V3B = ("- Do not override a recent deliberate buyer action without strong evidence; escalate instead. Scaling in the\n"
              "  SAME direction a buyer just moved is agreeing with them, not overriding them: propose it. Under a buyer lock\n"
              "  your proposal goes to a human anyway.")
assert _BUYER_V2 in SYSTEM_PROMPT_V3
SYSTEM_PROMPT_V3B = SYSTEM_PROMPT_V3.replace(_BUYER_V2, _BUYER_V3B)
# Version names key the raw-response store, so a changed text always gets a new name (never reuse "v3" for new text).
PROMPTS = {"adset-agent-v2": SYSTEM_PROMPT, "adset-agent-v3": SYSTEM_PROMPT_V3, "adset-agent-v3b": SYSTEM_PROMPT_V3B}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["scale_up", "scale_down", "pause", "keep", "escalate"]},
        "new_daily_budget": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "key_evidence": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
        "proposed_if_escalated": {"anyOf": [{"type": "string", "enum": ["scale_up", "scale_down", "pause", "keep"]},
                                            {"type": "null"}]},
    },
    "required": ["action", "new_daily_budget", "confidence", "reasoning", "key_evidence", "uncertainty",
                 "proposed_if_escalated"],
    "additionalProperties": False,
}


class Proposal(BaseModel):
    action: Literal["scale_up", "scale_down", "pause", "keep", "escalate"]
    new_daily_budget: float | None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    key_evidence: list[str]
    uncertainty: list[str]
    proposed_if_escalated: Literal["scale_up", "scale_down", "pause", "keep"] | None


class BudgetExhausted(RuntimeError):
    pass


class ReplayMissing(RuntimeError):
    pass


class LLMClient:
    def __init__(self, replay: bool = False, budget_usd: float = C.RUN_BUDGET_USD):
        self.replay, self.budget, self.spent, self.calls = replay, budget_usd, 0.0, 0
        self.version = C.PROMPT_VERSION          # selects the system prompt and keys the raw-response store
        self.system = PROMPTS[self.version]
        self._client = None
        if not replay:
            import anthropic  # imported lazily so --replay / --no-llm need no SDK credentials
            self._anthropic = anthropic
            self._client = anthropic.Anthropic(max_retries=3)
        RAW_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def card_text(card: dict) -> str:
        return json.dumps(card, sort_keys=True, default=str, separators=(",", ":"))

    def key(self, model: str, text: str) -> str:
        return hashlib.sha256(f"{model}|{self.version}|{text}".encode()).hexdigest()[:24]

    def _cost(self, model: str, u: dict) -> float:
        pin, pout = C.PRICES[model]
        return (u["input_tokens"] * pin + u["cache_read_input_tokens"] * pin * C.CACHE_READ_MULT
                + u["cache_creation_input_tokens"] * pin * C.CACHE_WRITE_MULT + u["output_tokens"] * pout) / 1e6

    def _call_api(self, model: str, text: str) -> dict:
        if self.spent >= self.budget:
            raise BudgetExhausted(f"run LLM budget ${self.budget:.2f} reached (spent ${self.spent:.4f})")
        try:
            r = self._client.messages.create(
                model=model, max_tokens=C.MAX_TOKENS,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": "Adset card (JSON):\n" + text}],
                output_config={"effort": C.EFFORT[model], "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            )
        except self._anthropic.APIStatusError as e:
            log.error("API error %s on %s: %s", e.status_code, model, e.message)
            raise
        except self._anthropic.APIConnectionError as e:
            log.error("connection error on %s: %s", model, e)
            raise
        u = {k: int(getattr(r.usage, k, 0) or 0) for k in
             ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        cost = self._cost(model, u)
        self.spent += cost
        self.calls += 1
        txt = next((b.text for b in r.content if b.type == "text"), "")
        rec = {"model": model, "prompt_version": self.version, "stop_reason": r.stop_reason, "text": txt,
               "usage": u, "cost_usd": round(cost, 6), "request_id": r._request_id,
               "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        new = not COST_LOG.exists()
        with COST_LOG.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts", "model", "input", "cache_read", "cache_write", "output", "cost_usd", "run_total_usd"])
            w.writerow([rec["ts"], model, u["input_tokens"], u["cache_read_input_tokens"],
                        u["cache_creation_input_tokens"], u["output_tokens"], rec["cost_usd"], round(self.spent, 6)])
        return rec

    def propose(self, model: str, card: dict) -> tuple[Proposal | None, dict]:
        """Return (validated proposal or None, meta). Stored raw responses are reused (cache + replay)."""
        text = self.card_text(card)
        k = self.key(model, text)
        path = RAW_DIR / f"{k}.json"
        # cost_usd = what this decision cost when it was first made (from the stored record), so a replay writes the
        # same row; what a run actually billed is client.spent, reported in out/run_meta.json
        meta = {"model": model, "raw_key": k, "cost_usd": 0.0, "error": None}
        for attempt in (1, 2):
            if path.exists() and attempt == 1:
                rec = json.loads(path.read_text(encoding="utf-8"))
            elif self.replay:
                raise ReplayMissing(f"no stored response {k} for {card['adset_id']} {card['decision_date']} ({model})")
            else:
                rec = self._call_api(model, text)
                path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            meta["cost_usd"] += rec["cost_usd"]
            try:
                if rec["stop_reason"] not in ("end_turn", "stop_sequence"):
                    raise ValueError(f"stop_reason={rec['stop_reason']}")
                return Proposal.model_validate_json(rec["text"]), meta
            except (ValidationError, ValueError) as e:
                log.warning("invalid output from %s for %s (attempt %d): %s", model, card["adset_id"], attempt, e)
                meta["error"] = f"llm_output_invalid: {e}"[:200]
                if self.replay:
                    break
                path.unlink(missing_ok=True)
        return None, meta


DIRECTION = {"scale_up": 1, "scale_down": -1, "pause": -1, "keep": 0}


def combine(h: Proposal, s: Proposal, budget: float | None) -> tuple[Proposal, str]:
    """Haiku/Sonnet disagreement rule (plan item 3, revised in Step 4f).
    - Sonnet escalates -> escalate. Haiku escalates -> Sonnet's answer goes forward if it is confident (>= 0.5),
      otherwise escalate (Step 4g: the second opinion decides). Opposite directions -> escalate.
    - One says keep, the other proposes a change -> the change wins unless the keep is the MORE confident of the two:
      an unsure keep must not veto (smoke test: 3/3 profitable days kept because Haiku shrugged), while a confident
      keep still stops a thin change. Guard caps and approval triggers apply after this.
    - Same direction, different size -> the smaller change."""
    if s.action == "escalate":
        return s, "Sonnet escalated"
    if h.action == "escalate":
        if s.confidence >= C.SONNET_CONF_BELOW:
            return s, "Haiku escalated, Sonnet confident -> Sonnet's answer"
        return h.model_copy(update={"proposed_if_escalated": h.proposed_if_escalated or s.action}), \
            "Haiku escalated, Sonnet unsure -> escalate"
    dh, ds = DIRECTION[h.action], DIRECTION[s.action]
    if dh * ds == -1:
        out = s.model_copy(update={"action": "escalate", "proposed_if_escalated": s.action,
                                   "confidence": min(h.confidence, s.confidence),
                                   "uncertainty": s.uncertainty + [f"Haiku proposed {h.action}, Sonnet {s.action}"]})
        return out, "opposite directions -> escalate"
    if dh == 0 and ds == 0:
        return s.model_copy(update={"confidence": min(h.confidence, s.confidence)}), "both keep"
    if dh == 0 or ds == 0:
        keep, change = (h, s) if dh == 0 else (s, h)
        conf = min(h.confidence, s.confidence)
        if keep.confidence > change.confidence:
            return keep.model_copy(update={"confidence": conf}), "keep vs change: keep more confident -> keep"
        return change.model_copy(update={"confidence": conf}), "keep vs change: keep not more confident -> change"
    if h.action != s.action:   # pause vs scale_down: scale_down is the conservative one
        pick = h if h.action == "scale_down" else s
    else:
        b = budget or 0
        pick = min((h, s), key=lambda p: abs((p.new_daily_budget or b) - b))
    return pick.model_copy(update={"confidence": min(h.confidence, s.confidence)}), "same direction -> smaller change"
