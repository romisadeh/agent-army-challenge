# Impact model: proposal (not built)

Goal: for every rule action, $ **profit impact** and $ **spend impact** (reported separately), as a range, plus the cost of rules that *didn't* fire.

## Core problem
After a kill or a cut, the "what would have happened" path is never observed: the action destroys its own counterfactual. So the counterfactual has to come from somewhere other than the acted-on adset's own future.

## Unit of analysis
- **Effective action** = first `SUCCESS` per (rule, adset, day). Repeats on an already-off adset, and "No budget to change", are no-ops with $0 effect. They're counted, but not as impact.
- **Failed action** (OAuth errors: 30 rows) = the action that *should* have happened. Here the observed path is the no-action path, and the counterfactual is "if it had applied".
- **Omission** = adset-days in the other five accounts where a calibrated rule (R04 only) would have fired and didn't. Reported separately as the cost of the scope gap.

## Counterfactual per action type
| Action | Actual (observed) | Counterfactual (estimated) |
|---|---|---|
| Turn OFF, applied | spend/profit up to the end of the day (includes the ~9% post-kill tail), $0 after | adset stays on: **spend** = expected rest-of-day spend + daily spend for following days; **profit** = that spend × counterfactual ROI |
| Budget cut, applied | observed spend/profit after the cut | spend at the pre-cut budget (old_budget × the adset's own utilization rate) × counterfactual ROI |
| Failed kill / cut | observed (no-action) path | the same estimate run in reverse: the impact the action *would* have had |
| Failed turn-on (R09) | stayed off: $0 | adset back on, estimated as for Turn OFF |

**Counterfactual ROI** = credibility-weighted blend: `w · own_roi + (1−w) · peer_roi`, with `w = spend / (spend + k)`.
- `own_roi`: the adset's matured ROI on the action day plus its prior days.
- `peer_roi`: untouched adsets with the same `spend_day_no` and a similar ROI band at that point. Preferably drawn from the five accounts with no rules, a natural control group, because there nobody killed the comparable adsets.
- `k` sets how much a $0.57 day-1 reading is trusted (barely) vs a $264 day (a lot). Three values of `k` give the low, central and high range.

**Counterfactual spend** = budget (metadata ÷ 100, or `old_budget` from the log) × the adset's own utilization on prior days. Day-1 adsets have no history, so use cohort utilization from untouched day-1 adsets.

**Horizon** = rest of the action day + up to 3 days. It's truncated at week end (06-12) and at the **next buyer action on that adset**, so effects after a human intervened aren't credited to the rule. Sensitivity: same-day-only vs +3 days.

## Validation (the only ground truth available)
Failed actions show what actually happened when the rule's action *didn't* apply: R03 on 06-08 (3 adsets, 19 OAuth-failed attempts), R02 on 31626016833981 on 06-09 (13 "No budget to change" attempts), and the other R12/R02 no-ops. Run the counterfactual model on those adset-days *as if* we didn't know the outcome, and compare predicted spend/profit to observed. The n is small, so it's a sanity check, not a calibration.

## Output
For each action: `applied, Δspend, Δprofit_low/central/high, horizon, truncated_by_buyer`. Rolled up by rule into **saved** (Δprofit > 0), **burned** (Δprofit < 0) and **spend lost**, plus a separate omission line for the five accounts with no rules.

## Assumptions (each one moves the number)
1. **Perf = matured.** There's no snapshot timestamp. 06-12 may be incomplete (2 executions saw more spend than the final row). Revenue that lands days later is ~0.1% of the week, so it's ignored.
2. **Counterfactual ROI** from the blend above. Day-1 ROI on <$1 is mostly noise and is shrunk hard toward peers.
3. **ROI doesn't depend on budget level within ±40%** (no diminishing returns). Bias: this *overstates* the profit burned by cuts on profitable adsets, because the marginal $ earns less than the average $.
4. **Utilization stays stable.** An adset that spent 80% of its budget keeps doing so.
5. **No interaction between adsets** (no cannibalization or auction overlap within an account).
6. **Repeats collapse** into one effective action; "No budget to change" has no effect.
7. **The post-kill tail spend** (~9% of the day median) belongs to the actual path, not the counterfactual.
8. **The other five accounts are valid controls for ACC-04.** Testable: compare their pre-action metrics (day-1 ROI, spend, utilization) before trusting them.
9. **Metadata budget ÷ 100** = the real daily budget (178/185 confirmed).
10. **The horizon stops at the next buyer action.** That understates rule impact where a buyer only fine-tuned the budget.

## Deliberately not attempted
- No full causal model of the auction and learning phase (killing a day-1 adset also loses what it would have learned). The design is out of scope; it's mentioned qualitatively only.
- No impact estimate for the 286 campaign-level buyer actions (they can't be joined to adsets).
