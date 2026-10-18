# RESULTS: the Adset Decision Agent (Task C)

The Adset Decision Agent (component #3 of [ARCHITECTURE.md](ARCHITECTURE.md)) ran end to end on the snapshot, in shadow mode, for every active adset in all six accounts on 06-10, 06-11 and 06-12: 728 decisions. Every number below is reproducible with the commands in [README.md](README.md), and each one comes from a file in `out/`: `evaluation.txt`, `comparison.txt`, `snapshot_pass.txt`, `run_meta.json`, `llm_cost_log.csv`.

## Verdict

**Against the mandate, the agent is too passive.**
- It kept 698 of 728 adset-days. Adsets it kept or escalated carried 91% of each day's spend.
- Its executable actions would have added about **+$4.59 profit** over three days, on a portfolio that makes ~$125/day.
- The mandate is absolute profit with spend maintained. Leaving proven winners unscaled is a miss against it, not a safe choice.

**The 2% harm rate comes mostly from how rarely the agent acts.** On the decisions where it did act, it was about as error-prone as the plain rule (21% harmful on scale-ups vs the rule's 18%). What makes loosening safe to attempt is that the loop measures harm as it goes: v3 doubled the changes on the held-out days and harm went from 2% to 6%, still far below the rule's 20% on the same days.
- It never repeated the legacy rules' mistakes: in the 4 cases where a rule cut an adset that matured profitable, the agent didn't cut.

**The passivity comes from the prompt's policy, not the guard thresholds or the data.**
- Of the 698 keeps, **0** were changes converted by a guard gate. 203 were "the LLM said keep" on evidence the code rated as solid (median data sufficiency 0.68, signal agreement 0.80).
- Lowering the sufficiency threshold changed no decision.

**The feedback loop found the fix, and one revision moved the agent in the right direction on held-out days.**
- The top dollar lesson was "keeping proven winners". Turned into policy (prompt v3, wording chosen on 06-10 only), it scaled more on 06-11 and 06-12:
  - profit vs keep rose from **+$3.86 to +$6.23**;
  - regret fell from $35.17 to $32.80;
  - harm rose from 2% to 6%.

**Buyers beat the agent on direction: 75% correct vs 11%,** graded the same way. Two structural reasons:
- Buyers act during the day with that day's data. Task C tested the 10:00 window, which by design uses none.
- The agent can't see on/off status.

  The architecture's answers are the 14:00 window and a status read from Meta before each card is built. Task C shows the price of the caution it tested.

Cost: **$0.85** for the 728 decisions, **$2.05** for everything run in this task (development, smoke tests and the prompt revision included).

---

## 1. What was built

```
raw CSVs ──► features.py (#1) ──► triage.py (#2) ──► llm.py (#3) ──► guard.py (#4, per card) ──► portfolio_guard.py (#4, per day)
             one card per            routes:             Haiku 5.5          caps, approval            account caps, spend floor
             adset-day, leakage      auto_keep / determ. Sonnet 5.5 2nd     triggers, confidence      (shadow: nothing is written)
             assertion               llm_review/escalate opinion            cap
                                                                                   └──► compare.py + feedback.py (#5): grading, ledger, lessons
```

- **Decision moment.** The 10:00 window of day D, using matured data through D−1 only. "Local" is UTC+3, the reporting day (INVESTIGATION data issue 8). The snapshot has one row per adset per day, so any intraday reading would have been invented. The only exception is §5.3.
- **Active adset on D.** It spent on D−1 and wasn't killed by a rule dated D−1. Adsets launched on D (69 / 212 / 145, 12% / 20% / 12% of each day's spend) get no decision by design.
- **Routing per day:**

| Day | Active | auto_keep (no LLM) | llm_review | escalate (no LLM) | auto_keep share of 3-day spend |
|---|---|---|---|---|---|
| 06-10 | 223 | 131 | 91 | 1 | 11% |
| 06-11 | 188 | 109 | 78 | 1 | 9% |
| 06-12 | 317 | 255 | 61 | 1 | 16% |

- **Materiality.** The line is $8 of matured spend over 3 days. It was set so the LLM load matches ARCHITECTURE's sizing of ~60 cards per window (DECISIONS Step 4e).
- **The code-decided route didn't fire.** Its only qualifying case ($4 with zero revenue) is below the materiality line. It overlaps the client's legacy kill rules by design.

### 1.1 Context per decision: no tables reach the model
- **What the LLM sees:** one card (~40 lines of JSON) per adset-day, built by code. It holds:
  - up to 5 matured days and the 3-day totals;
  - the blended ROI (`w = spend/(spend+$20)`, as in Task A);
  - peer forward ROI from the same account, age and ROI band, over windows that end by D−1;
  - the budget at 10:00, reconstructed from the logs;
  - the last human action with any note, and the 24h lock;
  - what the rules did yesterday and this morning;
  - data-quality flags.
- **Size per call:** ~600 uncached input tokens plus a 1,869-token cached policy prefix.
- **Leakage.** `features.assert_no_leakage` fails the run if any performance value comes from a date after D−1, if any peer window reaches D, or if any event is stamped after 10:00. It passes on all 728 cards.
- **A leak the assertion didn't cover, found in the smoke test:** the metadata's end-of-week on/off status. On 06-10 it partly describes later rule kills, and the model was citing it. It's now kept off every card (DECISIONS Step 4f).

Abridged real card (the case 4.3 adset on 06-12, [out/cards_sample/31273956645572_2026-06-12.json](out/cards_sample/31273956645572_2026-06-12.json)):
```json
{"adset_id": "31273956645572", "account": "ACC-04", "decision_date": "2026-06-12", "age_spend_days": 9,
 "budget": {"current": 17.22, "source": "buyer_log_before_window", "budget_type": "ABO"},
 "matured_days": [{"d": -3, "spend": 8.27, "roi": 0.203}, {"d": -2, "spend": 12.88, "roi": 0.197}, {"d": -1, "spend": 16.52, "roi": 0.121}],
 "last_3d": {"spend": 37.67, "roi": 0.165, "loss_days": 0, "profit_days": 3},
 "peers": {"segment": "ACC-04 / age 4+ / >+5%", "n": 24, "fwd_roi_1_3d": 0.108}, "blended_roi": 0.145,
 "buyer": {"last_human_action": {"hours_ago": 17.2, "from": 10.87, "to": 17.22, "direction": "up"}, "lock_24h": true},
 "rules": {"acted_yesterday": [{"rule": "R02", "action": "cut", "local_time": "14:30", "roi_seen": -0.14}]},
 "data_quality_flags": ["engine_echo_rows_in_buyer_log_excluded"]}
```

### 1.2 Output: the minimum schema plus fields for auditing

| Field | Why it's there |
|---|---|
| `route`, `route_reason`, `model`, `prompt_version` | Which path produced the decision; lets decisions be grouped and audited by path and prompt version |
| `current_budget`, `expected_profit_impact {low, central, high}` | What's at stake, computed by code; the portfolio guard trims by it and the Auditor grades against it |
| `evidence {data_sufficiency, signal_agreement, evidence_score, components}` | The uncertainty mechanism (§2), as numbers |
| `uncertainty_reasons[]` | The gaps in words ("buyer moved budget up in last 48h against the evidence"), so a human sees *why*, not just a low number |
| `llm_raw {haiku, sonnet, combine_rule, sonnet_trigger}`, `llm_confidence` | What each model said before code intervened; needed for calibration and to see the cascade working |
| `guard_adjustments[]` | Every override by code, in words; made the keep decomposition (§3.2) possible |
| `requires_approval`, `approval_reason[]`, `proposed_if_escalated`, `proposed_amount`, `default_if_unanswered` | The ARCHITECTURE §4 decision boundaries: who must approve, why, and what happens if nobody answers |
| `inputs_used` (matured dates, peer window end, budget source), `assumptions` | The leakage evidence and the stated assumptions, per decision |
| `cost_usd` | The cost of each decision (from the stored response, so replays are identical) |

Run metadata (timestamp, thresholds, money billed) is in `out/run_meta.json`, not on each row. That's why `--replay` with no API key reproduces `decisions.jsonl` **byte for byte**.

---

## 2. The uncertainty mechanism: how the agent knows it doesn't know

Code, not the model, decides how much evidence exists:
- **`data_sufficiency`** (is there enough data?): own spend vs $20, matured days, peer group size, ROI stability. Below 0.35, any change becomes `keep`.
- **`signal_agreement`** (does the data agree with itself?): yesterday vs the prior days, trend vs level, buyer moves and live notes vs the evidence, a rule acting this morning vs the evidence. Below 0.5, any change becomes `escalate`, and the proposal goes to the human.
- **`evidence_score`** = the lower of the two. It caps the LLM's confidence. The cap applied on 51 of 230 LLM decisions: 45 keeps, 4 pauses, 1 scale-down and 1 escalation.
- **Critical data problems** go straight to a human with no LLM call, because a proposal built on bad data is worthless.
- **Model disagreement.** If Haiku and Sonnet point in opposite directions, the card escalates. An *unsure* keep doesn't veto a change; a *confident* keep does.

### 2.1 Cases with no clean answer

**A. The brand-test adset (730122709357812221). The data can't be trusted, and a human instruction overrides the numbers.**
- It spent $27 on 06-09, the day *before* it was created (06-10), and its spend-day counter reads 0. It's the only adset in the data with spend before its creation date.
- A buyer note posted on 06-09, before the adset existed, says "client brand test – keep live through the weekend regardless of ROI (account manager request)".
- The agent escalated all three days with no LLM call (`critical_data_flag`). The buyer kept raising its budget: 1.27 → 3.81 → 12.7 → 25.4.
- Outcome: ROI +0.18, then +0.12, then **−0.36** on 06-12.
- Nobody could have known the right answer from the data. A brand test with an account-manager instruction is a business decision, and escalating is the correct behaviour, not a failure to decide.

**B. 730180390900194670 on 06-11. The signals disagree.**
- Yesterday's ROI was +0.20 against −0.14 for the prior days, and a buyer had raised the budget against the evidence. The LLM proposed a cut.
- The agreement gate turned it into an escalation carrying the proposal.

**C. 730110081340832816 on 06-11. The LLM recognised a gap in the data.**
- The model escalated on its own, writing: "true current daily budget is unknown (reconstructed from metadata)" and "CBO may allocate spend at campaign level, so the adset-level budget may not bind".
- Both points are real limits of this data. 429 of 728 cards carry a `budget_reconstructed` flag.

**D. Case 4.1 at 05:00 (snapshot pass, §5.3). Immature revenue.**
- The rule saw ROI 0.00 and cut. The card divides revenue by the delay factor (0.69 before 06:00), which gives +0.45, and blends it with matured history to +0.22. The agent kept.

### 2.2 What the mechanism did *not* do
- After the escalation fix (DECISIONS Step 4g), the gates barely fired: one agreement-gate escalation, no sufficiency conversions and no thin-evidence caps in three days.
- Most of the agent's uncertainty shows up as `keep` with a low capped confidence, not as escalation. Only 6 escalations remain (3 of them the brand test).
- Whether that's right is exactly what §3 tests. The answer: the keeps weren't uncertainty, they were policy.

---

## 3. How it did

### 3.1 Headline numbers (all three days)

| LLM-review cards (230) | Changes | Correct / harmful / neither (direction) | Right in $ | Regret | Profit vs keep |
|---|---|---|---|---|---|
| **Agent** (prompt v2) | 24 | 21% / **2%** / 77% | 86% | $51.76 | +$5.38 |
| No-LLM rule (blended-ROI sign, same guard) | 95 | 33% / **18%** / 49% | 89% | **$35.99** | **+$12.57** |

- **Direction** grades whether the adset deserved more or less money once revenue matured: ROI ≥ +10% means up, ≤ −10% means down, otherwise neutral. "Harmful" means the action went the opposite way. Both definitions were set before the run.
- **In dollars, the plain rule beat the agent this week.** It scaled more, and the winners it scaled outweighed its wrong-way decisions. The agent was safer and earned less.
- **Why keep the LLM at all?** What it bought this week is a 9× lower harm rate (2% vs 18%) and decisions that say why (§2.1), not profit. §4 is the attempt to get both.
- **The 2% harm rate is carried by keeps.** On the 14 graded scale-ups the agent was harmful 21% of the time by direction (8 correct, 3 harmful, 3 neither), close to the plain rule's 18% overall. Their dollar effect was still positive: +$4.21 profit vs keep, $2.81 regret. When the agent does act, it is not much safer than the rule; it is safer mainly because it rarely acts.
- **Two profit figures appear in this document.** +$4.59 (the verdict, §5.1) is the counterfactual on *executable* actions only. +$5.38 (this table) is profit vs keep over all LLM cards, including proposals that needed approval. "Profit vs keep" in §4 is the same measure as +$5.38.
- **Human load:** 9 decisions required approval over three days (2 / 5 / 2). By reason (a decision can have several): buyer_lock 4, critical_data_flag 3 (the brand-test adset), llm_escalated 2, and budget_ge_100, pause_with_profitable_day_last3, reactivation and insufficient_evidence 1 each. Under v3 it's 17. ARCHITECTURE §5.4 assumed 15–25 a day. The queue is easily workable, but only because the agent proposes so little; a less passive agent will fill it, mostly with buyer-locked proposals (11 of v3's 17).

### 3.2 Why it's passive: the keep decomposition

| Why it's a keep | n | Share of D's spend | Right in $ (by count / spend-weighted / D spend ≥ $5) |
|---|---|---|---|
| auto_keep (materiality gate, no LLM) | 495 | 14% | 96% / 91% / 73% (n = 11) |
| **The LLM said keep** | **203** | **68%** | 87% / **55%** / 74% (n = 90) |
| A guard gate converted a change | **0** | — | — |
| Portfolio trim | 0 | — | — |

- **The 203 LLM keeps were made on solid evidence:** median data sufficiency 0.68, signal agreement 0.80. 126 of them had Sonnet as well.
- **Sensitivity check:** replaying with the sufficiency threshold at 0.25 instead of 0.35 changed **no decision** (it costs $0 because the guard runs after the LLM).
- **So the passivity comes from the prompt.** The rule "scale up only on consistent matured profit" was read strictly.
- **Caveat on "right in $" for keeps:** by count it looks high (87%) because most adsets are small and their regret rarely reaches $0.50. Weighted by spend it's **55%**, which is the honest number.

### 3.3 Agent vs legacy rules (same days)
The rules acted on 11 active adset-days, 6 of them before the agent's 10:00 window. Graded the Task A way (the ROI the rule saw vs the matured ROI of that day):
- **Rules:** right on 6, wrong on 4, neither on 1. In all 4 wrong cases the rule saw −0.10 to −0.48 and the day ended **+0.12 to +0.52**.
- **The agent never followed a wrong rule cut.** It kept on 3 of those days and scaled up on the fourth (31191755212537 on 06-12, which a rule killed at 02:30 on ROI −0.46 and which ended +0.30).
- **It also kept on 5 days where the rule was right,** three of them small adsets ($0.37–$4.67 of spend) that lost all day. That's the same passivity again.

### 3.4 Agent vs human buyers, graded the same way

On the 187 adset-days where a buyer changed a budget (62% of spend), with the same labels for both sides:

| | Correct | Harmful | Neither |
|---|---|---|---|
| **Buyers** | **75%** | 13% | 12% |
| Agent | 11% | 1% | 88% |

**Who was right?** On direction, the buyers, by a wide margin. Mostly they scaled winners that the agent kept. Two structural reasons, and the architecture already answers both:
- **Buyers decide with day D's own data; the agent at 10:00 used none of it.** ARCHITECTURE's main window is 14:00, when revenue per $ is close to final (INVESTIGATION §2). Task C deliberately tested the window that doesn't use today's data, and this gap is the price of that caution.
- **The agent can't see on/off status.** On 06-10 it proposed scaling 31302925337341, the ≥ $100 adset, from $215.90 to $250. The adset spent **$0** that day: it had almost certainly been paused, which the buyer log can't show (INVESTIGATION issue 16). The proposal went to approval anyway (`budget_ge_100`), so nothing would have executed. But a live card needs a status read from Meta before the LLM sees it (ARCHITECTURE §6.4).

**Where I'd side with the agent:** buyers were harmful 13% of the time, against the agent's 1%. The buyers' wrong-way moves are real money, and the agent's caution protected against them. That makes the agent a safe base to loosen, as §4 does.

---

## 4. The improvement loop, closed once on real data

**The loop (ARCHITECTURE §7.2), with no retraining:**
1. Each decision goes into the ledger (`out/ledger.jsonl`, with its card).
2. It's graded once revenue matures.
3. Calibration is computed per segment (`out/calibration.json`), and lessons are proposed (`out/lessons.json`, which a human activates).
4. Calibration moves the escalation threshold within bounds, and active lessons go into matching cards (`feedback.lessons_for`) or, as here, into the policy.

**What the loop learned this week:**
- **The first grading signal taught the wrong lesson.** It used the direction band, under which every keep on a non-flat day counts as wrong. All 8 lessons said "keep was wrong", and calibration would have made the agent *more* cautious.
- **Switched to dollar regret.** Lessons and calibration now use ARCHITECTURE §7.2's dollar signal: regret = best of {keep, +20%, −20%, the action} minus the action's profit effect, with right in $ meaning regret ≤ $0.50. **This grade was added after the first run;** the pre-set direction grade is still reported next to it.
- **Under the dollar signal, the top lesson is the real weakness:** "keep with positive 3-day history on 4+-day adsets, day matured positive: **$20.92** regret over 17 cases". Calibration now reads keeps as *under*confident and would lower the escalation threshold.

**Closing the loop: prompt v3, with a held-out split**
1. **Lesson re-derived on 06-10 only.** The same pattern holds there (6 cases, $5.04 regret).
2. **v3 policy:** a 4+-day adset with ≥ 2 of 3 matured days profitable and 3-day and blended ROI > +0.10 is a scale-up candidate. The +0.10 bar is the evaluation's own label band, fixed in advance, not fitted. That also means v3 scales exactly where the direction grade calls scaling correct, so **the direction numbers favour v3 by construction. The dollar metrics (regret, profit vs keep) are the fair test.** v3 keeps such an adset only if it names a card field that argues against scaling.
3. **Development iteration 2 (v3b, still 06-10):** "scaling the same way a buyer just moved is agreeing, not overriding".
   - **Freeze rule, written down before the result:** freeze v3b only if it adds scale-ups without raising harm.
   - It raised harm from 3% to 6% (it also started following buyer *cuts*), so **v3b was rejected and v3 frozen.**
4. **v3 on the held-out days:**

| Held-out 06-11 + 06-12 (139 LLM cards) | Changes | Correct / harmful | Regret | Profit vs keep |
|---|---|---|---|---|
| v2 (the run) | 12 | 18% / 2% | $35.17 | +$3.86 |
| **v3** | 26 | 23% / 6% | $32.80 | **+$6.23** |
| No-LLM rule | 60 | 29% / 20% | $23.50 | +$10.66 |

**Reading:**
- v3 moves along the frontier in the intended direction: twice the changes, +$2.37 profit, a modestly higher harm rate, still far below the plain rule's.
- **These are small numbers** (two days, 139 cards), so it's evidence the loop works, not proof that v3 is better.
- **The misread v3b tried to fix is still in v3.** On 06-12 it kept the case 4.3 adset (3/3 profitable days, ROI +0.165) because "a buyer raised budget 17h ago, not stacking another increase". The day closed at +0.40.
- **The next iteration should be narrower:** treat a buyer's *raise* as agreement, without generalising to buyer cuts.

---

## 5. The evaluation question: how to know if the agent did a good job

### 5.1 The metrics, and why these
1. **Primary: counterfactual absolute profit and spend, in dollars.** The mandate is absolute profit with spend maintained. ROI is deliberately not a headline: pausing everything maximises it.
   - Method (the Task A method): each action is applied to the rest of the matured day; spend scales with budget; added or removed spend earns m × the day's ROI, with m = 0.5 / 0.75 / 1.0.
   - Agent, executable actions: **+$4.59** central (+$3.27 to +$5.91) on +$13.17 spend.
   - That figure covers 15 of the 21 executable actions in `out/portfolio_guard.txt` (11 / 5 / 5 by day). The other 6 hit adsets that spent $0 on day D, so their effect is zero and they aren't counted. All 6 are cuts (3 pauses, 3 scale-downs): 6 of the agent's 9 executable cuts landed on adsets that had already stopped spending, another case for a Meta status read before the card is built.
2. **Safety: harmful rate.** How often the action went the opposite way to the matured outcome. It's the number that makes it safe to give the agent more autonomy.
3. **Regret in $ per decision.** It captures what profit alone hides: the money left on the table by keeps.
4. **Calibration.** Does a stated 0.6 mean right 60% of the time? It decides where the escalation threshold sits.
5. **Supporting:** escalation rate (human load), share of spend on keep/escalate (the shrink check), and agreement with buyers.

**Why not accuracy against what buyers did?** Buyers are evidence, not ground truth (ARCHITECTURE §6.4): they were harmful 13% of the time. Copying them would copy their mistakes along with their skill.

### 5.2 The "right answer isn't known yet" problem
- **Only matured revenue grades a decision.** In production, grading waits until T+3, when INVESTIGATION §2 shows only ~0.1% of revenue is still to come. Here, 06-12 is graded on a possibly incomplete last day (flagged on every 06-12 row).
- **Grading confound.** Day D's outcome happened under what buyers and rules *actually* did, not under the agent's action. So the direction grade measures whether the adset deserved more or less money, not the agent's counterfactual. It uses D and D+1 where D+1 exists.
- **The $ metrics are explicit counterfactuals with stated assumptions,** reported as a range over m.
- **In production the clean answer is a holdout:** a random slice of adsets the agent doesn't touch, compared on matured profit and spend. That's possible in a live rollout, not on a snapshot.
- **Calibration on change decisions only** (where a direction grade means something): n = 16, Brier 0.265. Raw and capped scores are identical on these 16: the capped changes (mostly pauses) had no gradable outcome. Too few cases to conclude anything. The all-decisions Brier (0.261 raw, 0.237 capped) mostly scores the label band, not the model.

### 5.3 The maturity gate, demonstrated only in part

| Case | Rule saw | Corrected / blended | Rule did | Agent |
|---|---|---|---|---|
| 31302925337341, 06-08, 05:00 (case 4.1) | ROI 0.00 | +0.45 / +0.22 | R10 cut 254 → 215.90 | keep |
| 31273956645572, 06-11, 14:30 (case 4.3) | −0.10 | −0.10 / +0.05 | R02 cut 13.60 → 10.88 (buyer reversed it in 2h15m) | keep |
| 31626016833981, 06-10, 17:30 | −0.11 | −0.11 / −0.05 | R02 no-op | keep |
| 31626016833981, 06-12, 17:00 | −0.06 | −0.06 / −0.07 | R02 no-op | keep |

- The only intraday data is the rule log's readings, so these cards exist only for adsets the rules touched (ACC-04), at the moments they fired.
- **At portfolio scale the maturity gate is the main untested piece.**

---

## 6. Where the agent is weak
1. **Passive against the mandate.** Leaving proven winners unscaled cost about $21 of regret in three days. v3 helps; the misread about buyer raises is still there.
2. **It decided only at the 10:00 window.** That forfeits the intraday information buyers use, and it's the main reason for the 75% vs 11% gap.
3. **No on/off status.** It can propose scaling a paused adset (31302925337341). The production fix is a Meta status read before the card.
4. **Budgets are reconstructed** on 429 of 728 cards (from the logs or the end-of-week metadata). With 20% of spending rows above 1.25× budget, the adset budget may not be the binding constraint (the LLM said so itself, case C).
5. **The code-decided route didn't fire,** so the deterministic path is untested on this week.
6. **Small samples.** Three days and 230 LLM cards; the prompt revision was tested on two held-out days; change-only calibration has n = 16.
7. **One network failure stops the run.** A connection error that outlasts the SDK's 3 retries ends the process. Stored responses make the rerun cheap, but the architecture says to escalate the card and continue (§8), and this slice doesn't.
8. **The spend floor's proxy undercounts expected spend.** New launches aren't in "expected spend", so an account can look below its floor before any action. That's the right place for a human (the floor now sends the action to approval rather than trimming it), but it may fire too often.
9. **Lessons are proposed by a template** and expire only on paper (it needs more than one week of data).

## 7. Cost

| Item | Calls | $ |
|---|---|---|
| Production run: 728 decisions over 3 days (230 Haiku + 136 Sonnet) | 366 | 0.85 |
| Per decision window (one day, six accounts) | ~120 | 0.21–0.33 |
| Everything run in Task C (smoke tests, development, snapshot pass, v3 / v3b) | 877 | **2.05** |

- **Against ARCHITECTURE §5:** the estimate was $0.47/day for three windows. The measured $0.21–0.33 per window implies ~$0.85/day for three, because Sonnet ran on 59% of LLM cards (136 of 230) instead of the ~20 calls/day assumed. Still ~35× under the $30/day cap.
- **Haiku at medium effort stated confidence below 0.5 on 45% of cards,** which is what drives the Sonnet share. Whether that low confidence was honest can't be settled at n = 16 change decisions. What the run does show is that Sonnet resolved 37 of 38 Haiku escalations with a confident answer.
