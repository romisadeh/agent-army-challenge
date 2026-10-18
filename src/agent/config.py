"""Every constant the agent uses, with where it comes from. Thresholds are config, not code (ARCHITECTURE §4).

Values shared with Task A are copied, not imported: src/impact_model.py runs its whole analysis on import.
"""
import pandas as pd

# ---- time --------------------------------------------------------------------------------------------
TZ_OFFSET_H = 3                     # reporting day = UTC+3 (INVESTIGATION data issue 8)
WINDOW_LOCAL_H = 10                 # the 10:00 decision window (ARCHITECTURE §3); history-backed only
DECISION_DATES = [pd.Timestamp(d) for d in ("2026-06-10", "2026-06-11", "2026-06-12")]
DATA_START = pd.Timestamp("2026-06-06")
WEEK_END = pd.Timestamp("2026-06-12")
BUYER_LOG_START_UTC = pd.Timestamp("2026-06-06 14:00")   # INVESTIGATION issue 17

# ---- Task A carry-overs (src/impact_model.py:23-29, out/revenue_delay.txt 1c) ------------------------
K = 20.0                            # $ of own spend at which own and peer ROI weigh equally
DELAY_CURVE = [(6, 0.69), (12, 0.83), (24, 1.00)]   # revenue-per-$ visible, by local hour
M_SCENARIOS = {"low": 0.5, "central": 0.75, "high": 1.0}   # marginal ROI of added/removed spend
MIN_PEERS = 5
ENGINE_ECHO_S = 120                 # a buyer_actions row within 120 s of a rule execution = engine echo (issue 15)

# ---- decision limits (ARCHITECTURE §4) ---------------------------------------------------------------
MAX_STEP_PCT = 0.20                 # ±20% per action ...
MAX_STEP_USD = 50.0                 # ... and at most $50
THIN_UP_STEP_PCT = 0.10             # symmetric guard: thin-evidence scale-ups capped at half a step
PAUSE_MAX_BUDGET = 20.0             # autonomous pause only below $20/day ...
PAUSE_MIN_LOSS_DAYS = 2             # ... and with >= 2 matured loss days
APPROVAL_BUDGET = 100.0             # any action at >= $100/day needs a human
SONNET_BUDGET = 50.0                # second opinion at >= $50/day (ARCHITECTURE §5.3)
ACCOUNT_MAX_ADD_PCT = 0.10          # net budget added per account per day <= 10% of trailing daily spend
ACCOUNT_MAX_CUT_PCT = 0.15          # budget paused or cut per account per day <= 15%
SPEND_FLOOR_PCT = 0.85              # expected account spend >= 85% of trailing (ARCHITECTURE §8 #5)
MAX_ACTIONS_PER_WINDOW = 40
UTIL_CAP = 1.25                     # Meta can spend up to 125% of a daily budget

# ---- triage gates (ARCHITECTURE §1: materiality, maturity) -------------------------------------------
MATERIALITY_3D_SPEND = 8.0          # below $8 of matured spend over 3 days: auto_keep, no LLM. Raised from $3 so the
                                    # LLM load matches ARCHITECTURE §5.1 (~60 cards per window): 61-91/day, auto_keep
                                    # holds 9-16% of 3-day spend (DECISIONS Step 4e)
BUYER_LOCK_H = 24
DET_MIN_DAYS = 3                    # deterministic pause: >= 3 matured spend days with $0 revenue ...
DET_MIN_SPEND = 3.0                 # ... and at least $3 burned
# Sub-score thresholds (plan §2a). Set before looking at outcomes; see DECISIONS.md if they move.
DS_MIN = 0.35                       # data_sufficiency below this -> keep (wait for data)
SA_MIN = 0.50                       # signal_agreement below this -> escalate any change
UP_FULL_STEP_MIN = 0.55             # evidence_score needed for a full +20% step
SONNET_CONF_BELOW = 0.50            # Haiku confidence below this -> Sonnet second opinion. Fixed before the first full
                                    # day, not fitted to the data: below 0.5 Haiku is saying it doesn't know (Step 4f)

# ---- LLM ---------------------------------------------------------------------------------------------
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
EFFORT = {HAIKU: "medium", SONNET: "medium"}   # Haiku was "low"; at low it stated 0.35-0.45 on every smoke-test card
MAX_TOKENS = 4000                   # includes adaptive thinking
PRICES = {HAIKU: (0.10, 0.50), SONNET: (2.00, 10.00)}   # $ per 1M tokens in / out (claude-api skill, 2026-10)
CACHE_READ_MULT, CACHE_WRITE_MULT = 0.1, 1.25
RUN_BUDGET_USD = 3.00               # hard stop per run; the brief reimburses $10 in total. Raised from 2.00 in code
                                    # review 1: a stop mid-day-3 would turn the rest into code-only keeps and skew the eval
PROMPT_VERSION = "adset-agent-v2"   # v2: card without end-of-week status, Haiku medium effort, new combine rule
