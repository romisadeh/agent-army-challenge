# INVESTIGATION: what did the auto-rules actually do?

Task A. Data: one week (2026-06-06 → 06-12), six accounts. Every number below is reproducible from the scripts listed at the end.

## Verdict

**Where the impact can be measured, the rules lost a small amount of money, concentrated in early-morning budget cuts. R04, the most frequent rule, can't be measured precisely and is treated as ≈ 0. The suspicion holds in pattern, not in total.**

- **Measurable net: −$26 to −$41 profit.** That's budget cuts (−$16 to −$31) plus kills other than R04 (−$10). As a share of the week's profit, that's **3–5% of all six accounts' profit ($878)**, or **5–8% of ACC-04's ($490)**, the only account the rules touched. Spend lost: **$154 on cuts** (reliable) and **$72 on other kills** (overstated); see §3.3.
- **The pattern is real.** Budget cuts fire early in the morning on real spenders, before the day's revenue has arrived: 9 of 12 effective cuts fired before 09:00 local, and **6 of 12 cut an adset at ROI ≤ 0 on a day that ended positive**.
- **The scale is small.** The rules acted on **one account (ACC-04) out of six**, through **78 effective actions** out of 214 log rows, mostly kills of ~$1–$5/day test adsets. R04's 39 kills judged $32 of spend in total (§3.4).
- **"Letting losers run" is mostly not a rules problem.** Losing adsets that no rule touched lost $690 across the week, but that's a ceiling. Applying R04's conditions to the five accounts without rules would have saved only **~$31**, because most day-1 losers there stopped spending without any rule.
- **The bigger operational failures aren't threshold logic:** rules that say "all accounts" acted on one; a 14-hour access-token outage on 06-08 left 27 actions unapplied (30 OAuth failures across the week), including 8 attempts by the rule that exists to undo automation mistakes; and the rule log records budgets one change behind.

---

## 1. What the rules did

### 1.1 From 214 log rows to 78 effective actions

```
214  rule executions logged (all ACC-04)
 −30  failed: OAuth "access token invalidated" (06-08 ~03:00–17:00 UTC, plus 3 later)
 −20  no-op: "No budget to change" (15 of 20: engine could not read the live budget)
 ───
164  SUCCESS
     ├─ 152 Turn OFF rows → on 66 adsets  (86 are repeats on an adset already off)
     └─  12 budget cuts
 ───
 78  effective actions = 66 kills + 12 cuts      (the 8 Turn ON attempts all failed)
```

### 1.2 By rule

| Rule | Logic (from name) | Logged | Applied | Effective | Adsets | Notes |
|---|---|---|---|---|---|---|
| R01 | Turn OFF, total days ≥ 5 | 4 | 4 | 3 | 3 | Hidden conditions: it fired 4 times, yet many adsets are > 5 days old |
| R02 | −20%, −30 < ROI ≤ −10 | 23 | 6 | 6 | 4 | 13 no-ops on one adset (06-09) |
| R03 | Turn OFF, 0 positive days, > 2 days | 36 | 17 | 10 | 13 | 19 OAuth failures; its "age" disagrees with perf in 28/36 |
| R04 | Turn OFF, day 1, > 35% budget used, ROI < −50% | 109 | 109 | 39 | 39 | 2.8 firings per kill; median spend at firing **$0.57** |
| R05 | Turn OFF, total profit ≤ −$2.5, ≥ 15% budget used | 7 | 6 | 4 | 6 | |
| R06 | Turn OFF, early loss thresholds | 1 | 1 | 1 | 1 | |
| R07 | −40%, "−50 ≤ ROI", budget > $65 | 1 | 1 | 1 | 1 | Name likely flipped (§5) |
| R08 | Turn OFF, total days = 4 | 15 | 13 | 9 | 10 | Hidden ROI condition |
| R09 | **Turn ON, "Automation Mistake"** | 8 | 0 | 0 | 1 | All 8 failed (OAuth). The system's own undo rule |
| R10 | −15%, −10 < ROI ≤ 5, budget ≥ $100 | 2 | 2 | 2 | 1 | Both on the week's biggest adset (§4.1) |
| R11 | Turn OFF, 0 positive days, > 3 days | 2 | 2 | 0 | 1 | Both firings came after R03 had already killed the same adset |
| R12 | −40%, ROI ≤ −50, budget ≤ $65 | 6 | 3 | 3 | 3 | |

Firing counts match `auto_rules.firings` exactly for all 12 rules.

### 1.3 Scope: "all accounts" in name, one account in practice
- All 214 executions are on **ACC-04**. All rule names say "OWN RSOC adsets, all accounts".
- **None** of the 75 adsets acted on is named `own_rsoc`; all are `slg:` campaigns. ACC-04's 23 `own_rsoc` adsets got zero actions.
- **This is not because the other accounts had nothing to act on.** R04's conditions reproduce all 39 of its real firings (calibration in `out/sanity_checks.txt` 3a), and the same conditions match **15 / 97 / 50 / 96 / 35** day-1 adset-days in ACC-01/02/03/05/06, with zero firings.

### 1.4 Timing
- **Cuts fire early:** effective cuts by local hour are 05, 05, 05, 06, 06, 07, 07, 08, 08, 14, 18, 22. 76% of the spend the cut rules judged was judged before 09:00.
- **Kills spread through the day** (16 / 9 / 27 / 14 across the four 6-hour blocks), mostly R04 in the afternoon.
- **Turn-offs stick:** 0 of 66 killed adsets spent on any later day. A same-day tail exists: 57/66 show spend after the kill (median 9% of the day, $9.44 total). That's either spend reporting lag or a delivery wind-down; the data can't tell which.

---

## 2. Revenue delay (input to Task B and Task C)

Method: for each execution, revenue per $ the engine saw for *today* ÷ the final revenue per $ for that adset-day. Using revenue per $ rather than revenue share separates "revenue arrives late" from "the day isn't over".

| Local hour (UTC+3) | Revenue-per-$ visible vs final | n executions (spend ≥ $1) |
|---|---|---|
| 00–06 | **0.69** (median 0.43) | 8 |
| 06–12 | **0.83** (median 0.93) | 15 |
| 12–18 | 1.00 | 25 |
| 18–24 | ~1.0 (1.08) | 32 |

- **Revenue arrives in batches.** On the one clean series (31626016833981, 06-09, 13 no-op attempts), revenue stays flat for ~2 h while spend rises.
- **Days-later tail is small:** 6 rows show revenue on a zero-spend day (1–4 days after the last spend), $11.42 total, ~0.1% of the week's revenue.
- **The final day may be incomplete:** on 06-12, 2 executions saw *more* spend than the final perf row shows. There's no snapshot timestamp, so the perf table is treated as matured, with that caveat.
- **Small sample.** These are 80 executions on a few dozen adset-days. Read the curve as a shape (front-loaded, gone by midday), not as precise factors.

**Implication:** a ROI reading before ~noon understates the day's revenue per $ by 17–31% (1 − 0.83 and 1 − 0.69). That is exactly the window in which R02/R07/R10/R12 fire.

---

## 3. Impact in $

### 3.1 Method

**Unit.**
- Each of the 78 effective actions.
- Repeats and no-ops count as $0.
- Failed actions are kept separately as the validation set.

**Horizon.** The rest of the action day plus up to 3 days. It is cut at week end and at the **next intervention on the adset**: the next rule action, or any buyer action that isn't the engine's own API echo. So effects after a human stepped in aren't credited to the rule.

**Actual path.** Observed from matured perf. For the action day: day spend − spend at action, at the day's matured revenue per $.

**Counterfactual (no action).**
- **Budget cut:** actual spend × (budget before ÷ budget after). The extra spend earns **marginal ROI = m × counterfactual ROI**, with m = 0.5 / 0.75 / 1.0 (diminishing returns), which gives the range.
- **Kill:** the rest of the day up to the peer full-day level. Then per later day: budget × peer survival × peer budget utilization, capped at 1.25× budget. A kill removes average spend, so m does not apply.

**Counterfactual ROI** = `w · own + (1 − w) · peer`, with `w = own spend / (own spend + $20)`.
- *own* = action-day revenue seen **÷ delay factor** (0.69 / 0.83 / 1.00 by hour, from §2), plus up to 3 prior matured days.
- *peer* = **ACC-04 adset-days not acted on** in the same age bucket (1 / 2–3 / 4+), same-day ROI band, and budget-used ≥ 35% or not. Their spend-weighted ROI over the next 1–3 days. If fewer than 5 qualify, fall back to coarser groups.

**Why the control group is ACC-04 only.** The other five accounts are also `slg:`, but they fail a similarity test:
- revenue per $ ranges 0.79–1.19 across accounts;
- day-1 revenue per $ ranges 0.70–1.09;
- what happens to day-1 losers differs completely (ACC-02: 0 of 69 R04-qualifiers spent the next day; ACC-06: 17 of 29).

**Budgets** come from `current_budget_from_fb` → `set_budget`. The log's `old_budget`/`new_budget` are one change behind (§5).

### 3.2 Assumptions (each one moves the number)
1. **The perf table is matured revenue.** 06-12 may be incomplete. The days-later tail (~0.1%) is ignored.
2. **The delay curve (§2) applies to every adset.** It is estimated on ~80 executions.
3. **Marginal ROI of cut spend is 50–100% of average.** This is the stated range.
4. **Spend scales with budget** (utilization stays constant) after a cut.
5. **Within-ACC-04 peers left alone are a fair stand-in** for what a killed adset would have done.
6. **No interaction between adsets** (no auction overlap or cannibalization).
7. **Repeats and "No budget to change" have no effect.** The same-day post-kill tail belongs to the actual path.
8. **The horizon ends at the next human or rule intervention.** This understates impact where a buyer only fine-tuned.
9. **The metadata `daily_budget` ÷ 100 is the real budget.** Used only for peer utilization.

### 3.3 Results: kills and cuts reported separately

| | n | Spend lost | Profit impact | How reliable |
|---|---|---|---|---|
| **Budget cuts** | 12 | **$154** | **−$16 / −$24 / −$31** (marginal ROI 50 / 75 / 100%) | **More reliable.** The post-cut path is observed; only the spend-scaling and marginal ROI are assumed |
| Kills (excl. R04) | 27 | $72 | −$10 | Weak: the counterfactual spend is modelled, and validation shows it is over-predicted 1.7–35.5× |
| **Measurable total (excl. R04)** | 39 | **$226** | **−$26 to −$41** | |
| R04 kills | 39 | $86 *(modelled; likely overstated, same caveat as other kills)* | **≈ 0, not precisely measurable** (model: +$83) | See §3.4 |
| **All 78 actions, as modelled** | 78 | **$312** | (not meaningful: it includes R04's +$83) | |

**Spend lost: $312 as modelled = $154 (cuts) + $72 (other kills) + $86 (R04).** Only the cuts' $154 is reasonably reliable. The kill figures ($158 together) are very likely overstated, because the model over-predicts what a killed adset would have spent by 1.7–35.5× (§3.5).

Largest single item: **R10 on 31302925337341** (the $264/day adset): −$16.81 profit and −$77.59 spend over two early-morning cuts (§4.1).

### 3.4 Why R04 is "near zero, not measurable" rather than +$83
- The model's +$83 is *(modelled post-kill spend) × (peer ROI)*.
- The **peer ROI comes from 7 adset-days** (forward ROI −1.0). Moving it from −1.0 to 0.0 moves R04 from +$85 to +$3.
- The **modelled spend is the quantity validation shows is over-predicted 1.7–35.5×**. If the real spend was that much lower, the saving is $2–$48.
- What *is* solid: R04's median spend at the moment it fired was **$0.57** (all 109 firings), and its 39 effective kills judged **$31.94** in total. The ROI it saw matched the matured day almost exactly (mean gap +0.006). Its kills stuck. And the adsets it killed were losers on the day. **It wasn't misled. It was ruling on noise at a scale where the money involved is small either way.**

### 3.5 Validation: a sanity check, n = 13 adset-days
Failed and no-op actions are the only cases where the no-action path was actually observed: 5 OAuth-failed kills and 8 no-op cuts. Running the model on them as if the outcome were unknown:
- **ROI direction:** the predicted counterfactual ROI had the same sign as the observed ROI in **8 of 13**, little better than a coin flip.
- **Spend:** for the 5 failed kills, predicted no-action spend was **1.7–35.5× the observed spend**. Those adsets mostly stopped spending anyway (the same pattern as day-1 losers in the accounts without rules).
- **Reading:** the model is weak at the single adset-day level, and kill $ figures are inflated in both directions. This is why cuts are the headline and R04 is reported as unmeasurable.

### 3.6 The other half: losers that ran
**9 sustained losers** that no rule touched (≥ 3 spend days, loss on most of them, ≥ $5 week loss) lost **$63 on $179 spend**. 7 of the 9 had buyer actions during the week, so people were looking at them.

| adset | account | spend days | days at a loss | spend | profit | ROI |
|---|---|---|---|---|---|---|
| 730121092582511364 | ACC-05 | 4 | 3 | $20.27 | −$9.39 | −46% |
| 730131435815981997 | ACC-06 | 4 | 3 | $12.61 | −$9.31 | −74% |
| 730133762543700867 | ACC-05 | 4 | 4 | $17.15 | −$8.32 | −49% |
| 730175047382406513 | ACC-03 | 3 | 2 | $35.62 | −$7.53 | −21% |
| 730133022053654014 | ACC-05 | 7 | 6 | $51.57 | −$6.79 | −13% |
| 730111395845609920 | ACC-06 | 3 | 3 | $9.93 | −$6.16 | −62% |
| 730132435690207113 | ACC-06 | 4 | 4 | $5.21 | −$5.21 | −100% |
| 730124861204325890 | ACC-06 | 3 | 2 | $17.87 | −$5.13 | −29% |
| 31326123629255 | ACC-04 | 4 | 3 | $9.13 | −$5.09 | −56% |

- **The broader number is a ceiling.** 699 losing adsets that no rule touched lost **$690 on $2,032**: 460 of them at a week ROI ≤ −50%. $658 of it was in the five accounts without rules.
- **What rules would actually have saved.** For R04 applied outside ACC-04 (the one rule whose conditions reproduce its real firings), the saving = loss after the adset first met the conditions: the post-trigger 6% of day 1 plus all later days, net of later winners. That comes to **saves $36, burns $5, net ~$31**.

---

## 4. Cases: decisions a competent human would not have made

### 4.1 R10 cuts the week's biggest adset at 05:00, on 27% of the day
**What happened.** Adset `31302925337341` spent $216, $320, $264 and $127 over 06-06 to 06-09, at ROI **+0.50, −0.00, +0.22, +0.33**.
- On 06-07 at 15:03 UTC a buyer set its budget to **$254** (`ui_update_budget`).
- At **05:00 local on 06-08**, R10 cut it 15% to $215.90. At that moment the rule had seen $70 of the day's $264 spend (**27%**) at ROI −0.04 (≈ 0.00 at full precision).
- The day matured at **+0.22**.
- The day before, at 08:30 local, R10 had already cut it from $314.55 to $267.36.

**What the rule was missing or misreading:**
- **Revenue delay.** Before 06:00 only ~69% of revenue per $ is visible (§2). Corrected for delay and combined with the prior days, the reading is +0.23.
- **History.** The two prior days in the data were +0.50 and −0.00 on $216 and $320 of spend, but the rule reads one intraday number.
- **Human intent.** A buyer had set this budget hours earlier. The rule can't see that a human just made a deliberate call.

**Cost:** −$16.81 profit (central), −$77.59 spend over the two cuts. That's the largest single item in the week.

### 4.2 R08 kills an improving adset, and the undo rule can't reverse it
**What happened.** Adset `31314467522499` ($1.27/day) had ROI −0.61 on day 2 and −0.20 on day 3.
- R08 ("Turn OFF | Total Days = 4") killed it at **04:00 local on day 4**, at ROI −0.37 on $0.27 of spend.
- At that point it had seen 84% of the day's spend but only **27% of the day's revenue**.
- The day matured at **+1.00**.
- **R09 "Automation Mistake" recognised the error.** It fired 8 times between 06:30 and 15:30 UTC to turn the adset back on. **All 8 failed** with "access token invalidated". The adset stayed off for the rest of the week.

**What the rule was missing or misreading:**
- **Revenue delay.** At 04:00 the revenue had not arrived yet.
- **Trend.** −0.61 → −0.20 is an improving adset.
- **An age trigger.** A kill condition keyed to "day 4" doesn't distinguish an improving adset from a dying one.
- **Nobody checked whether the fix worked.** Nothing escalated after the first failed undo, so a human never saw it.

**Cost:** tiny ($0.31 modelled; the budget is $1.27). The case matters because of the failure chain, not the dollars.

### 4.3 R02 cuts a consistent winner; the buyer reverses it within 2h15m
**What happened.** Adset `31273956645572` had ROI **+1.03, +0.10, −0.23, +0.20, +0.20** on 06-06 to 06-10. The buyer was scaling it, raising the budget 8.00 → 13.60 on 06-09 (`ui_adjust_budget`).
- At **14:30 local on 06-11**, R02 cut it 20% (13.60 → 10.87) on ROI −0.14 (−0.10 at full precision), with about half the day's spend seen.
- At **16:46 local a buyer raised it to 17.22.** The action appears as both `ui_adjust_budget` and its API echo `update_ad_set_budget`, so a human made it.
- The day matured at **+0.12**. The buyer kept scaling (23.57, 29.92) and 06-12 closed at **+0.40**.

**What the rule was missing:** it read one intraday reading, ignored 4 of the previous 5 days being positive, and couldn't see that a human was deliberately scaling the adset. This is not revenue delay (at 14:30 the delay factor is ~1.0); it's intraday noise on a ~$8 reading.

**Verdict:** **the buyer was right.** $ impact is negligible (−$0.03) *because* the human reversed it fast, which is itself the point: someone had to spend attention undoing the automation.

---

## 5. Data issues

| # | Issue | Evidence (how found) | How handled |
|---|---|---|---|
| 1 | Exact duplicate perf rows | 72 rows, all on 2026-06-09; same (adset, date), identical values | `select distinct` before every aggregation |
| 2 | Null revenue ≠ missing revenue | All 2,676 null-revenue rows (deduped) have spend = 0, and `roi` is set to 0 | Treated as no-delivery days; not evidence of lag |
| 3 | Revenue on zero-spend days | 6 rows, $11.42, 1–4 days after the last spend | Kept; the only evidence of lag that spans days (~0.1% of revenue) |
| 4 | Two adset_id formats | 14-digit = ACC-04 only, 18-digit = others; 100% of IDs in the rule log and buyer log join to perf + metadata verbatim | No mapping needed; checked coverage |
| 5 | **Metadata `daily_budget` is 100× the real budget** | Ratio to the last buyer budget = 100 for 178/185 adsets across all accounts; rule log `current_budget_from_fb` matches ÷100 | ÷ 100. Without this, every "budget used" condition silently evaluates false |
| 6 | Metadata `effective_status` is misleading | 58 of 66 rule-killed adsets show `ACTIVE` + `delivery_status = ADSET_OFF`; 1 shows `ACTIVE/ACTIVE` | Not used for on/off; kills verified from perf (no later spend). **`delivery_status`, not `effective_status`, identifies active adsets. This is an input for Task C** ("each active adset") |
| 7 | **Rule log `old_budget`/`new_budget` are one change behind** | For 10/12 cuts the engine's own API row shows `current_budget_from_fb → set_budget`, not old→new | Use `current_budget_from_fb` → `set_budget`. The other 2: one predates buyer-log coverage, one has a missing API row; both are arithmetically exact |
| 8 | `action_date` ≠ UTC date of `action_time` | 21 rows at 21:00–23:59 UTC carry the next date → the reporting day is UTC+3 | Join rule log ↔ perf on `action_date`; all time maths in UTC (a machine-timezone bug was caught and fixed) |
| 9 | Repeated firings | 152 successful kill rows on 66 adsets; 66 (rule, adset, day) groups fired > once | First success per adset = effective; the rest are no-ops |
| 10 | Access-token outage | 30 OAuth errors, mostly 06-08 03:00–17:00 UTC | Counted as failed (no effect); used as the validation set |
| 11 | "No budget to change" | 20 rows; 15 have `current_budget_from_fb` = null | No-op; mostly failed budget reads, not floor hits |
| 12 | `last_3_days_revenue_at_action` mis-named | ~2.3× the matching 3-day perf revenue and grows during the day; `last_3_days_spend` matches (1.00) | Not used. No rule decision depends on it: ROI thresholds match *today's* ROI (R04 107/109, R10 2/2, R12 6/6, R02 21/23; misses are rounding) |
| 13 | Two definitions of age | R03's `total_days_at_action` > perf `spend_day_no` in 28/36 firings | Noted; R03 fired on adsets 1–2 spend-days old by perf's count |
| 14 | Rule names don't describe behaviour | Scope "OWN RSOC, all accounts" vs ACC-04 `slg:` only; R01/R08 have hidden conditions; **R07 "−50 ≤ ROI"** contradicted 5× in the log, while "ROI ≤ −50" reproduces its single firing | Conditions tested empirically; R07 = "ROI ≤ −50" **likely, unverified (n = 1)** |
| 15 | buyer_actions is not purely human | 10 of 48 `update_ad_set_budget` rows are the rule engine (within 120 s of an execution); at least one human UI action also wrote an API echo (case 4.3), so the other 38 can't be attributed | Only `ui_*` events count as human evidence; engine echoes excluded from "interventions" |
| 16 | buyer_actions can't show on/off | No status event type exists | Manual re-enables are invisible; only budget reversals are detectable |
| 17 | buyer_actions duplicates and gaps | 4 exact duplicate rows; same-second triplicates; log starts 06-06 14:00 UTC; at least one engine API row missing | Deduped where used; coverage stated |
| 18 | Campaign-level buyer actions can't be joined | 286 rows with no adset_id and no campaign_id column | Excluded |
| 19 | Rounded ROI in the rule log | `today_roi_at_action` has 2 decimals; R04 fired once at a precise ROI of −0.46 | Revenue seen = spend × rpc ÷ cpa where available |
| 20 | Final perf day may be incomplete | 2 executions on 06-12 saw more spend than the final row | Treated as matured; stated as assumption 1 |
| 21 | Spend reading at action time lags | Median 9% of the day's spend lands after a kill | Tail attributed to the actual path; "share of day seen" carries ±10–20 pts |

**Checked and clean:** profit = revenue − spend and roi = profit ÷ spend recompute exactly (1,922 rows with spend). Rule firing counts match `auto_rules.csv`. No negative spend; no clicks > impressions.
**Seen but not investigated (not used in any result):** 42 rows with Meta conversions > clicks; 10 rows with ROI > 5; metadata naming fields beyond the `own_rsoc`/`slg:` prefix.

---

## Carried forward to Tasks B and C
- **Active adsets** = `delivery_status`, not `effective_status` (data issue 6).
- **Revenue delay curve** (§2): readings before ~noon understate revenue per $. Front-loaded; ~0.1% arrives days later.
- **Budgets:** metadata ÷ 100; in the rule log use `current_budget_from_fb` → `set_budget` (issues 5, 7).
- **Human signal:** only `ui_*` events in buyer_actions are safely human (issue 15).

---

## Reproduce

```bash
.venv/Scripts/python src/profile_data.py          # row counts, nulls, duplicates, consistency   -> out/profile.txt
.venv/Scripts/python src/rule_roi_delta.py        # ROI seen vs matured, per execution            -> out/rule_roi_delta.csv
.venv/Scripts/python src/sanity_checks.py         # reconciliation, post-kill spend, R04 calibration -> out/sanity_checks.txt
.venv/Scripts/python src/revenue_delay.py         # delay curve, zero-spend revenue, field checks -> out/revenue_delay.txt
.venv/Scripts/python src/losers_and_reversals.py  # untouched losers, buyer reversals, engine echoes -> out/losers_and_reversals.txt
.venv/Scripts/python src/impact_model.py          # $ impact, validation, omission                 -> out/impact_summary.txt, out/impact_actions.csv
.venv/Scripts/python src/task_a_facts.py          # remaining cited facts (funnel, timing, cases)  -> out/task_a_facts.txt
.venv/Scripts/python src/check_numbers.py         # every number here -> its out/ file and line    -> notes/number_sources.md
```
**Number sources:** [notes/number_sources.md](notes/number_sources.md) maps every data-derived number in this document to the `out/` file and line it comes from (147 claims, all checked). Method parameters (m = 0.5/0.75/1.0, K = $20, 1.25× cap, ≥ 5 peers, 3-day horizon) are constants in `src/impact_model.py`.
SQL: [sql/02_rule_roi_delta.sql](sql/02_rule_roi_delta.sql). Method notes: [notes/impact_method_proposal.md](notes/impact_method_proposal.md) (the as-built changes are in DECISIONS.md, steps 1d–1e).
