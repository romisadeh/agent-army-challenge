# DECISIONS.md — AI usage log

Format per step: **Asked** (what I asked Claude Code) · **Got wrong / rejected** · **My call** (what I decided myself, and why).

---

## Step 0 — Planning & setup

**Asked:** Read the brief + the data folder and propose how to plan the assignment.

**What Claude did:** Before planning, profiled the CSVs (row counts, header, rows-per-date, event types, rule responses, adset_id length). That surfaced the first red flags early: 14- vs 18-digit adset_ids, perf rows per date growing 357 → 999 (lag or new adsets?), 50/214 rule executions not `SUCCESS`, R01 firing twice on the same adset 30 min apart, a rule literally called "Automation Mistake".

**Got wrong / rejected:**
- Its first quick `awk` pass split `buyer_actions.csv` on commas, so notes containing commas/quotes came out broken. Fine for a peek, unusable for analysis → decision: every real parse goes through a CSV-aware reader (DuckDB / pandas), never `cut`/`awk`.
- Its first dependency-age check (a shell loop) printed empty dates for every package and would have read as "all fine". Re-ran in Python: **numpy 2.5.4, pydantic 2.14.1 and pydantic_core 2.50.1 were < 7 days old** (pulled in transitively). Pinned numpy 2.5.3 / pydantic 2.13.5 and re-verified the whole lock file. Also skipped anthropic 1.13.0 (3 days old) → 1.11.0.

**My call:**
- Stack: Python + DuckDB (SQL straight over the CSVs, so the queries themselves are the deliverable) + pandas.
- Order of work: data quality first, then rule impact, then architecture, then the agent — the design should be driven by what the investigation finds (especially the revenue-lag characteristics), not the other way around.
- I started by understanding the data before touching the questions: what each CSV holds, how it’s built, and what the rules actually do. Then I planned the investigation in Claude’s plan mode before letting it near the raw data, so the analysis followed a plan I’d agreed to rather than whatever came out first.

---

## Step 1 — Data profiling + "what did the rule see vs. what matured"

**Asked:** Load all 5 CSVs; report row counts, column nulls, duplicate (adset_id, date) keys — and explicitly list what *looks* fine but wasn't checked. Then ONE query only: join each rule execution to the matured perf row for the same (adset, day), show seen-ROI vs matured-ROI delta, sorted by biggest delta. No attribution model yet. Flag anything showing the system knows it made a mistake.

**Got wrong / corrected (by Claude, against its own Step-0 plan):**
- Plan said metadata (7.1k rows) "likely has snapshot/duplicate rows". **Wrong** — 7,129 rows = 7,129 distinct adset_ids. It's just a big catalog of mostly inactive adsets.
- Plan said "perf rows per date grow 357 → 999 → revenue lag". **Wrong reading** — every row with null revenue has spend = 0 (and roi hard-coded to 0). Row growth is zero-spend adsets entering the table, not missing revenue. Total spend per day actually *falls* (1,739 → 843).
- Plan said "must build an ID-mapping table before any join". **Not needed for this join** — the 14-digit IDs are simply ACC-04's format; all 75 rule adset_ids exist verbatim in perf and metadata.
- **Got a number wrong:** a first ad-hoc query reported 2026-06-09 spend as $1,507. That included the 72 duplicated perf rows; deduped it is **$1,281**. All scripts now dedupe perf (`select distinct`) before aggregating.
- Several first-run queries failed because DuckDB reserves words like `rows`, `matched`, `logged` as aliases — trivial, but it means the first numbers Claude "had" were never actually printed; nothing was reported from a failed query.

**Claude's own addition (kept — flag for review):** the brief asks for ROI delta only. Claude added `share_of_day_spend_seen` and `revenue_seen_implied` columns, because a rule firing at 03:00 sees a partial day — a raw ROI delta conflates *intraday incompleteness* with *revenue lag*. These are two different failure causes with different fixes.

**My call:**
- Join on `action_date`, not the UTC date of `action_time`: 21 executions at 21:00–23:59 UTC already carry the next `action_date` → reporting day is UTC+3 (Israel summer time), same as perf `date`.
- "Matured" = the perf snapshot as delivered. This is an **assumption**, not verified: there is no extraction timestamp, so 06-11/06-12 revenue may itself still be incomplete.
- The first ROI-delta result didn’t match the cases I’d seen in the data, so I didn’t accept it and tried to work out why the numbers came out that way. I chose to apply the rule’s action to the matured rest of the day, with marginal ROI as a range, because that answers the client’s question in dollars. I assumed at first that spend scales with budget; I learned from the data that it doesn’t always.

### Step 1b — I cross-checked Claude's numbers; three didn't hold up

**My review of its reply:** (1) perf row counts don't reconcile: 4,875 unique − 2,721 null-revenue − 1,947 with-spend leaves ~207 unexplained, and it's unclear whether 1,947 is pre- or post-dedup. (2) Its own R08 example has 16% of the day's spend *after* a successful Turn OFF: either spend is reported with a lag or the kill didn't apply. Either way this undermines its "rules fire too early" timing story. (3) "All accounts in scope, only ACC-04 fired" was presented as an anomaly without testing whether other accounts had qualifying adset-days.

**What the checks found (`src/sanity_checks.py`, `out/sanity_checks.txt`):**
1. **Mixed bases.** Claude subtracted a *raw* count (2,721, 1,947) from a *deduped* total. Deduped: 4,875 = 2,676 (no spend, null revenue) + 1,922 (spend > 0) + **277 (no spend, revenue non-null)**. Of the 277, 271 have revenue = 0 and **6 have revenue on a zero-spend day ($11.42)**: revenue landing after spend stopped. That's small but direct evidence of revenue lag. The profiling script now dedupes before every count.
2. **Turn-offs did stick.** 0 of 66 killed adsets spent on any later date. But 57 of 66 had spend after the kill on the same day ($9.44 total, median 9% of the day's spend). Post-kill spend rises with hours left in the day and then plateaus (4% → 11% → 20% → 19%), and in the R08 case the spend reading is flat from 5.5 h after the kill onward. **Reading:** a tail of a few hours, from reporting lag or a delivery wind-down, which the data can't separate. **Consequence:** `spend_at_action` understates spend by roughly 10–20% of the day, so "share of day spend seen" carries that error. It does not explain the R07/R02 cases where the rule saw ~30% (a 70-point gap). The early-firing finding stands, with a stated error band.
3. **The anomaly is real, and the first test of it was wrong.**
   - Claude's first calibration matched **0** ACC-04 adset-days for R04 against **39** actual firings. Cause: **`daily_budget` in the metadata is in cents (100× the real budget)**. This holds for 178/185 adsets across all six accounts, checked against buyer budgets and the rule log's `old_budget`. Without this check every "budget usage" condition silently evaluates false.
   - After fixing: R04 calibrates (all 39 fired adset-days qualify; 48 qualify in total). R12 does not calibrate (117 qualify vs 3 fired; probably the "min 9" floor makes cuts on ~$1.27 budgets no-ops), so it's excluded from inference.
   - **Using R04 as the reliable probe:** ACC-01/02/03/05/06 had 15/97/50/96/35 qualifying day-1 adset-days and **zero firings**. Rules that say "all accounts" acted on one.
   - Also: **none** of the 75 adsets the rules acted on is named `own_rsoc` (all are `slg:` campaigns), so the scope label in the rule names doesn't describe what the rules actually touch.
- **Claude's own bug, caught while doing (2):** the "hours left in day" math used DuckDB's session timezone, which inherits this machine's (Israel, +03). That shifted every value by 3 h and put 22 kills in the wrong bucket. Fixed by forcing `TimeZone = 'UTC'`; the 2b numbers above are post-fix.

**My call:** Yes. It went from “the rule lost money” to “the rule cut too early in the morning, before revenue had matured.” The investigation showed the rule itself wasn’t necessarily the problem — its timing was. That finding is what let us change the decision window in the architecture.

### Step 1c: my gap review against the Task A requirements

**My review:** The work so far was groundwork, not deliverables. Three gaps: (1) only "killing winners" had been examined; "letting losers run" is the other half of leadership's suspicion. (2) Claude leaned toward "it's not late revenue", but the brief treats the delay as a given that Task A should *characterize*. It feeds Task B's constraint and Task C's last-3-days decisions. (3) Impact has to be in **$ profit and $ spend, separately**, because that's the mandate; ROI deltas don't answer it. buyer_actions also hadn't been used to look for manual reversals.

**Asked:** (1) measure revenue visibility by hour (UTC+3) from engine-seen vs final values, preferring actions that didn't change the outcome, plus the zero-spend revenue rows; (2) losers no rule touched, by account; (3) *propose* an impact method (don't build it); (4) buyer reversals of rule actions.

**Where Claude was wrong or overstated:**
- **"It's not late revenue" was an over-reach from the wrong evidence.** The null-revenue rows really aren't lag (they're zero-spend). But the median ROI delta ≈ 0 came mostly from afternoon firings, where revenue has already caught up. Measured by hour, revenue per $ visible is **~0.7 of final before 06:00 local (median 0.45), ~0.7–0.9 at 06–12, ~1.0 from midday on**. Revenue also arrives in **batches** (the one clean series is flat for ~2 h while spend rises). A tail of 1–4 days exists but is ~0.1% of the week's revenue. So the delay is real, concentrated within the day, and front-loaded, which is exactly when R02/R07/R10/R12 fire.
- **Claude's draft of the impact proposal said "R03 on 06-08 (4 adsets)"** from memory. Verified: **3 adsets, 19 attempts**. Fixed before handing it over.
- **Small samples**, stated rather than hidden: the hour buckets rest on 31 adset-days with spend ≥ $1; the "unaffected only" subset is 3–5 adsets per bucket.

**New data issues found:**
- `last_3_days_revenue_at_action` is **~2.3× the matching 3-day perf revenue** and grows during the day, so it isn't the window its name says. `last_3_days_spend_at_action` *does* match D-3..D-1 (ratio 1.00). The revenue field is unusable for multi-day lag.
- **buyer_actions isn't purely human.** 10 of 48 `update_ad_set_budget` rows are the rule engine's own API calls (same adset, within 120 s of a rule execution, at the :00/:30 cadence). The other 38 come from an unidentified API source. Only the `ui_*` events (927 rows) are safely human. This matters for Task B/C if buyer history is used as a training signal.
- buyer_actions has **no status/on-off event type**, so a manual re-enable of a killed adset is invisible. Only budget reversals can be detected.

**Results:**
- **Losers that ran:** 699 adsets with negative week profit that no rule touched lost **$690 on $2,032 spend**, vs total week profit of **$878**. It's mostly many small bleeds: 460 of them at ROI ≤ −50% (a pattern like R04's day-1 kills) lost $427. Only 9 are sustained losers (≥3 spend days, loss ≥ $5): $63. The five accounts with no rules hold $658 of the $690.
- **Manual reversals:** adset 31273956645572: R02 cut it 13.60→10.87 at 11:30 UTC on 06-11; a buyer raised it to 17.22 2h15m later. The day matured at ROI +0.12 vs −0.14 seen. → **the human was right.** Adset 31302925337341 (the week's biggest, ~$264/day): a buyer restored its budget on 06-07; R10 cut it again at 05:00 local on 06-08 on 27% of the day's spend, ROI −0.04 seen, **+0.22 final**.

**Claude's design choices in the impact proposal (`notes/impact_method_proposal.md`), for me to accept or reject:** credibility-weighted counterfactual ROI (own vs peers, weighted by spend); the five accounts with no rules used as the control group; horizon truncated at the next buyer action; failed actions used as the only ground-truth back-test.

**My call: impact method approved with changes.**
- (a) Correct the action-time ROI with the step-1 delay curve *before* blending, and state the correction used.
- (b) First establish whether ACC-04 is the only `slg` account. If it is, drop the other accounts as a control and use ACC-04 adset-days that met the conditions but weren't acted on. If it isn't, run the similarity test and show it.
- (c) Present the failed-action validation as a sanity check, with its sample size, not as proof.
- (d) Build the low/central/high range on assumption #3: marginal ROI at 50/75/100% of average for budget cuts.
- Also: losses after first meeting R04's conditions outside ACC-04 = the "rules would have saved" number ($690 is only a ceiling), leading with the 9 sustained losers. For both reversal cases show event type + source, and only say "buyer was right" if it's `ui_*`. List which rules read `last_3_days`, and whether the 2.3× inflation changed any decision. Then build.

### Step 1d: build the impact model

**What the pre-build checks changed:**
- **(b) ACC-04 is not the only slg account.** All six are ~all `slg:` (61/228/133/114/201/243 spending slg adsets). The similarity test **fails**: revenue per $ 0.79–1.19 across accounts (ACC-04 1.19); day-1 revenue per $ 0.70–1.09; and the forward behaviour of day-1 losers differs completely (ACC-02: 0 of 69 R04-qualifiers spent the next day; ACC-06: 17 of 29). → **Control = within ACC-04 only.**
- **Reversal case 2 withdrawn.** 31302925337341: no buyer action follows R10's 06-08 cut; the 06-07 `ui_*` actions were a further −20% cut then a partial raise (254, still below the pre-rule 314.55). Not a reversal. **Case 1 stands:** 31273956645572's raise after R02 appears as `update_ad_set_budget` 13:46:04 + `ui_adjust_budget` 13:46:08, a human UI action that also writes an API row. That also means some of the 38 "unidentified" API rows are probably echoes of UI actions.
- **`last_3_days`: no rule's decision is explained by these fields.** ROI-threshold rules match *today's* ROI (R04 107/109, R10 2/2, R12 6/6, R02 21/23; all 4 misses are 2-decimal rounding at the boundary). `last_3_days_roi` isn't derived from the inflated revenue field (5/34 consistent). → **the 2.3× inflation changed no testable decision.** R01/R08 have no metric in their names, so they can't be tested.
- **New data issue: the rule log's `old_budget`/`new_budget` are one change behind** (they show the *previous* budget transition). The real values are `current_budget_from_fb` → `set_budget`, confirmed by the engine's own API row for 10/12 cuts. Any analysis using `old_budget→new_budget` as "what the rule did" is wrong.
- **R07's name is likely mistyped.** "-50 <= ROI" fired at ROI −0.51; it's the >$65 twin of R12 ("ROI <= -50 | Budget <= 65$").
- Minor: R04 fired once at a precise ROI of −0.46 (rpc/cpa), which doesn't meet "< −50%".

**Claude's error in the first build (caught by the output, not by me):** the first run reported R04 kills as **$879 burned** on $2,271 of counterfactual spend from $1.27–$5.08-budget adsets. Cause: (1) the peer group matched on age + ROI band but **ignored the budget-used ≥35% half of R04's condition**, so peers were mostly adsets that started late on day 1 with $0.01–$0.08 of spend; (2) later-day spend was scaled *relative to day-1 spend* (ratios of 44×) with **no budget cap**. Fix: peers also match on used ≥35%; later-day counterfactual spend = budget × peer survival × peer utilization, capped at 1.25× budget. R04 moved from −$879 to +$83. Kept as a warning: the headline flipped sign on a modelling detail.

**Implementation choices:**
- (a) Delay correction: revenue seen ÷ {0.69 before 06:00, 0.83 06–12, 1.00 after 12:00} (step-1 pooled curve, 1c).
- Own vs peer ROI weight `w = own_spend/(own_spend+$20)`.
- Peer fallback when fewer than 5 adset-days.
- Kills are not affected by (d) (they remove average spend, not marginal), so the low/central/high range only moves the cuts.

**Result (`out/impact_summary.txt`):**
- 78 effective actions (66 kills, 12 cuts). **Spend lost: $312.** Profit: **saved $89–91, burned $32–49, net +$57 / +$50 / +$42** at marginal ROI 50 / 75 / 100% (a lower marginal ROI means cuts burned less).
- **But the dominant uncertainty isn't #3, it's R04's peer ROI** (7 peer adset-days, forward ROI −1.0): it drives +$83 of the net. Varying the peer ROI from −1.0 to 0.0 moves R04 from +$85 to +$3, so **overall net ≈ −$30 to +$57**. Against $2,586 of ACC-04 spend, the rules' net effect is roughly zero, not "destroyed money".
- The biggest single burn is R10 on 31302925337341 (the $264/day adset): −$17 central over two early-morning cuts on a +22% ROI adset.
- **(c) Validation, n = 13 adset-days** (5 failed kills, 8 no-op cuts): the predicted ROI direction matched in 7/13; for failed kills the model **over-predicted no-action spend 2–50×** (those adsets mostly stopped spending anyway). So kill $ figures are overstated in magnitude in both directions. It's a sanity check that the model is weak at the adset-day level, not a calibration.
- **Omission outside ACC-04:** the 9 sustained untouched losers lost $63 (7 of 9 had buyer actions, so humans were watching). R04 applied to the other five accounts would have **saved a net $31** (saves $36, burns $5) of the **$690 ceiling**, because most day-1 losers there stopped spending without any rule.

**My call: accepted, with changes.**
- **R04 is reported as "near zero, not precisely measurable", not +$83.** The failed-kill validation shows the model over-predicts post-kill spend 2–50×, and R04's +$83 is that over-predicted spend × a peer ROI drawn from 7 adset-days.
- Split every result into kills vs budget cuts and say which estimate is more reliable.
- Headline spend lost alongside profit, with the overstatement caveat.
- Explain the 2 of 12 cuts the budget-field fix didn't confirm. Test whether R07 reproduces under "ROI ≤ −50"; if it doesn't, label it an unverified guess.
- **Verdict: total impact is roughly zero. The suspicion holds in pattern (early cuts on real spenders before revenue arrived), not in total.** Concrete cases: R10 on the $264 adset, R08 + the failed R09 undo, and the R02 case-1 reversal.

### Step 1e: closing out Task A analysis

**Asked:** apply the step-1d changes above, then draft INVESTIGATION.md (what the rules did incl. the 214 → 78 funnel, impact with method + assumptions, the cases, and a data-issues table). Nothing for Task B yet.

**Checks run:**
- **The 2 unconfirmed cuts.** R12 06-06 04:30 UTC happened *before buyer_actions starts* (06-06 14:00 UTC), so there's no log to confirm it against. R02 06-12 15:00 UTC (31626016833981) is inside the log's coverage, but the engine's API row is missing. Both still satisfy `set_budget / current_budget_from_fb` = exactly the rule's % (0.60, 0.80). For the R02 one, `current_budget_from_fb` (39.5986) also equals the last logged change (→ 39.60). → The field mapping holds; buyer_actions has gaps.
- **R07 under "ROI ≤ −50".** The literal reading ("−50 ≤ ROI, budget > 65") is contradicted 5 times in the rule log, where adsets with budget > $65 and ROI ≥ −0.5 were evaluated and R07 didn't fire (31626016833981 on 06-07 ×2 and 06-10; 31302925337341 on 06-07 and 06-08). The flipped reading reproduces the single firing (−0.51, $66) and nothing contradicts it. But it rests on **one firing**, so INVESTIGATION.md labels it "likely, unverified (n = 1)".
- **"No budget to change" explained:** 15 of the 20 have `current_budget_from_fb` = null, so the engine failed to read the live budget. They're mostly read failures, not floor hits.
- **The timing pattern behind the verdict:** 9 of 12 effective cuts fired before 09:00 local (76% of cut spend). 8/12 matured better than the ROI the rule saw, and **6/12 were cut at ROI ≤ 0 on days that ended positive**.

**Split result (central; cuts range = marginal ROI 50–100%):**
| | n | spend lost | profit | reliability |
|---|---|---|---|---|
| Budget cuts | 12 | $154 | −$16 to −$31 | **More reliable.** The post-cut path is observed; the only assumptions are spend ∝ budget and the marginal ROI |
| Kills excl. R04 | 27 | $72 | −$10 | Weak: counterfactual spend, which validation shows is over-predicted 2–50× |
| R04 kills | 39 | $86 (model) | **≈ 0, not measurable** | Model +$83 = over-predicted spend × peer ROI from 7 adset-days; peer ROI 0 → +$3 |

Net excluding R04: **−$26 to −$41**, i.e. 5–8% of ACC-04's week profit ($490). Spend lost: $312 modelled, of which only the cuts' $154 is reasonably reliable.

**Claude's errors in the INVESTIGATION.md draft (caught by re-checking each case claim against perf before handing over):**
- Case 4.3 said the R02-cut adset had "5 of the previous 6 days positive". The data shows **4 of 5** (+1.03, +0.10, −0.23, +0.20, +0.20).
- Case 4.1 said "3 of the 4 days were strongly positive", which used days *after* the cut that the rule couldn't have seen. Rewritten to the two prior days only (+0.50, −0.00).
- Case 4.1 also credited the +0.23 to the delay correction alone, but it's delay correction *plus* prior days. Reworded.

**My final Task A fixes:**
- Rewrite the verdict: where measurable, the rules lost a small amount (cuts −$16 to −$31 + other kills −$10 = **−$26 to −$41**), concentrated in early-morning cuts; R04 not measurable; express it as a share of the week's profit.
- Make the spend table add up to the headline.
- Note that `delivery_status`, not `effective_status`, identifies active adsets (input for Task C).
- Consistency pass: every number → its `out/` file and line; fix and log mismatches. Then stop. Task A is done.

**Done:**
- Verdict rewritten: −$26 to −$41 = **3–5% of the six accounts' week profit ($878)**, 5–8% of ACC-04's ($490).
- The spend table now has a measurable row ($226) and an all-actions row (**$312 = $154 + $72 + $86**, with R04's $86 marked as modelled/overstated).
- The `delivery_status` note was added to data issue 6 and to a new "Carried forward to Tasks B and C" section.

**How the consistency pass was done:**
- Several cited numbers came from one-off queries that never reached an `out/` file. Claude moved them into `src/task_a_facts.py` (→ `out/task_a_facts.txt`, sections F1–F25), added SPLIT / VALIDATION-summary / R04-SENSITIVITY / omission TOTAL sections to `impact_model.py`, and reran every script in dependency order.
- `src/check_numbers.py` then checks **147 claims**. Each claim must appear verbatim in INVESTIGATION.md, and its cited source line must match a regex that pins the value. The output is `notes/number_sources.md`.
- The first checker run flagged 5 claims; all 5 were wrong regexes in the checker (spacing, `rule:R10`), not wrong numbers.

**Mismatches found and fixed (doc vs output):**
| Where | Doc said | Output says | Cause |
|---|---|---|---|
| §3.5 validation | ROI sign matched in **7 of 13** | **8 of 13** | Claude's hand count in step 1d treated −0.007 vs −1.000 as a miss; same sign |
| §3.3/3.4/3.5 | kill spend over-predicted **2–50×** | **1.7–35.5×** | Stale: "2–50×" came from the *first, buggy* model run (step 1d) and was never re-derived after the fix |
| §3.4 | R04 saving **$2–$42** if spend deflated | **$2–$48** | Same stale over-prediction range |
| §1.2 R04 | "~3 repeats per kill" | **2.8 firings per kill** (≈ 1.8 repeats) | Wrong word: firings ≠ repeats |
| Verdict | outage "left **30** actions unapplied" | **27** in the 06-08 window, 30 across the week | Conflated the week total with the outage |
| §2 implication | "understates ROI by roughly **15–30%** of revenue" | revenue per $ understated by **17–31%** | Loose rounding + wrong quantity (ROI vs revenue per $) |
| §1.3 | R04 calibration cited as "(§3.1)" | it's in `out/sanity_checks.txt` 3a | Wrong cross-reference |

**Claims removed or softened because no output supported them:**
- "460 losers at ROI ≤ −50%, **mostly day-1**": no output computes the day-1 share. Removed.
- "human UI actions **also write** an API echo": only one instance is observed (case 4.3). Softened to "at least one", and stated that the other 38 API rows can't be attributed.

**Sources added for previously unsourced claims (all confirmed true):**
- "13 no-ops on one adset" (F23).
- "~$1–$5/day test adsets" (F22: 49 of 66 kills at $1.27–$5.08, median $1.27).
- R04's kills were losers on the day (F24: 39/39 matured ROI ≤ 0).
- R03 fired on adsets 1–2 spend-days old (F25: 28/36).

**Not covered by the checker:** the method constants (m, K = $20, 1.25× cap, ≥ 5 peers, 3-day horizon). They are code constants in `impact_model.py`, cited as such in INVESTIGATION.md.

**Claude's correction this round:** in step 1d it had headlined "net +$50 (range −$30 to +$57)". That number was carried by R04, the least reliable component. The split shows the reliable part (cuts) is consistently negative, which is what the verdict now says.

**Task A closed.**

## Step 3 — Architecture

**Asked:** plan the Task B architecture *from* the INVESTIGATION findings, not from a generic template. Then write ARCHITECTURE.md, plus a sizing script ([src/arch_sizing.py](src/arch_sizing.py) → `out/arch_sizing.txt`) so the economics numbers have a source, the same way Task A's numbers do.

**What I pushed back on in Claude's first plan:**
- **"Data flow" was a list per CSV, not per agent.** The brief asks what each agent is fed. I made it produce a who-sees-what table. The principle that came out of it: only the Feature Builder touches raw tables.
- **It was unclear whether the LLM executes.** I asked directly. The answer is now explicit everywhere: the LLM only recommends, and code (the Executor) writes. Limits live in code, so a prompt can't talk its way past them.
- **"Top-3 lessons" was a guess.** Claude admitted it had no data behind the number. It's now "only lessons matching the segment, capped at ~5, tunable", with relevance and focus as the reasons.
- **The plan used terms and rules without explaining them.** I asked about each one before approving. Every answer is now written into ARCHITECTURE.md:
  - *What is an "adset card"?* → the glossary, plus an example card (§6.3).
  - *What does "escalate" mean, and how is it different from `llm_review`?* → with `llm_review` the LLM decides and the Executor acts within limits; with `escalate` a human decides and nothing runs until they answer. Plus the explicit list of escalation triggers (§4).
  - *Explain component #3 (the Decision Agent) and #4 (the Executor).* → the "what it can do" column in the topology table (§2).
  - *What is T+3?* My question exposed a mix-up in the plan: I'd linked T+3 to the Executor (#4), but T+3 grading belongs to the Auditor (#5). The glossary now defines it, and it appears only under the Auditor.
  - *What is a "window"? What is a "buyer lock"?* → the glossary and the cadence table (§3).
  - *What does "acting on data more than 2h old" mean?* → spelled out: a stale data pull means recommend-only until fresh data arrives (§4).
  - *What happens when the LLM budget is exhausted?* → spelled out: code-only `keep` plus the deterministic route, and urgent cases go to a human (§4).

**My additions (not in Claude's plan):**
- **Routing-spike alert:** if `llm_review` volume runs above normal, alert a human and say which triage rule caused it. Too many LLM calls usually means a threshold is unclear. Claude set the trigger (> 1.5× the 7-day median for that window, or > 30% of all cards) and extended the same idea to an **escalation-spike alert**, so humans don't get flooded with approvals.
- **LLM budget alerts** before hitting $30. Claude turned this into the 50% / 80% / projected-pace / hard-stop-at-100% tiers.
- **A daily human digest.** I asked whether humans get a daily summary. Claude's plan only mentioned one in a single line. It's now a full section with a fixed time (08:30, before the first window) and fixed contents, exceptions first.

**Claude got wrong, and the data corrected it:**
- **The cost argument.** The plan said "an LLM call on a $1.47 adset can cost more than the adset makes", as the reason to keep small adsets away from the LLM. The sizing script disproved it: at current Haiku 5.5 prices a call costs ~$0.0004. The whole POC is ~$0.47/day, and Haiku on *every* adset at every window is still only $0.40/day.
  - The real reasons for the materiality gate are noise (R04 ruled on $0.57; §3.4) and Meta API/human-attention load.
  - Cost does bind for the *model choice*: Opus on everything would be $32/day, over budget. ARCHITECTURE §5 says this honestly.
- **The buyer lock's reach was underestimated.** It was proposed as an edge-case guard. The data shows buyers touch ~70 adsets/day holding 55% of spend (S7), so during co-existence the lock covers most of the money. Rollout §9 now treats this as intended for the shadow phase, and a buyer touch on an agent-owned adset becomes a graded override.

**My calls:**
- **Scope: design for all six accounts, roll out on ACC-04 first.**
  - I raised the question: the rules only ever acted on one account, so should the design cover only ACC-04 and plan the rest later?
  - Claude checked the data and recommended all six: buyers, whom the system replaces, do 89% of their `ui_*` work outside ACC-04. A design for ACC-04 only would repeat finding §1.3 ("all accounts" in name, one in practice).
  - I accepted it, with ACC-04 as the first rollout account.
- **Approved the plan** only after the clarifications, the per-agent data table and the alerts above were in.
- **Reviewed and accepted Claude's proposed numbers** as they stand. Claude first listed these as open, because they were its assumptions and not mine:
  - the salary figure (~$6k/month), and the framing that at this scale the case is buyer hours saved, not profit uplift (the portfolio makes ~$125/day);
  - 15–25 escalations/day as the human-oversight load, to be measured in the shadow week;
  - the decision limits in §4: ±20% and $50 per action, the $20 pause line, the $100 approval line, the 85% spend floor.

## Step 4 — Adset Decision Agent

**Asked:** plan component #3 from ARCHITECTURE.md (the Adset Decision Agent) as a working slice, with thin versions of the code around it (Feature Builder, Triage, a guard in place of the Executor, a stubbed Auditor), so it runs end to end on the snapshot.

**My calls before the plan was written:**
- **Decision moment: the 10:00 window of day D, using matured data ≤ D−1.** The snapshot has one row per adset per day, so any intraday reading would be invented. Deciding on the morning of D is honest about what was knowable, and it lets D's matured row grade the decision against what buyers and rules did during D.
- **Follow the triage design.** Only `llm_review` cards reach the LLM. Sending every adset would have cost < $1, but it would drift from the architecture and hide the materiality gate.

### Step 4a: plan revision 2: my review of Claude's first plan (16 items)
I reviewed the first plan against ARCHITECTURE.md and the brief, and sent back 16 changes. Claude applied all of them; item 9 is applied only in part because of what the data allows.

*Uncertainty mechanism (the part the brief cares most about):*
1. **One evidence score mixed two different problems.** "Not enough data" and "the data disagrees with itself" need different responses. Split into `data_sufficiency` (own spend vs K, matured days, peer n, stability) → low means **keep / wait**, and `signal_agreement` (D−1 vs history, trend vs level, buyer vs evidence) → low means **escalate**. A combined score is kept for reporting.
2. **The guard only stopped negative actions.** A scale-up on thin evidence burns real spend at an unknown ROI just as a bad cut forgoes profit, and Task A showed thin-data ROI readings don't tell you the direction (8/13, §3.5). The guard is now symmetric: thin-evidence scale-ups become keep or a ≤ 10% step.
3. **No rule for Haiku vs Sonnet disagreeing.** Now defined: opposite directions → escalate; same direction, different size → the more conservative action. Both responses are logged.
4. **Calibration only on the capped confidence would hide whether the cap helps.** It is now reported on the raw LLM confidence too.

*Consistency with ARCHITECTURE.md:*

5. **Buyer-locked cards went to escalate with no LLM call.** ARCH §2/§6.4 says the agent still *proposes* under a lock. They now go through `llm_review` with approval forced.
6. **"Requires approval" was vague.** The guard now names every ARCH §4 trigger (`budget_ge_100`, `pause_with_profitable_day_last3`, `change_gt_20pct`, `reactivation`, `buyer_lock`, `critical_data_flag`, `insufficient_evidence`), and `approval_reason[]` lists every one that fired.
7. **The per-account caps and the spend floor (ARCH §4, §8) were missing.** Added a portfolio-level guard after all cards are decided; it logs what it trimmed.
8. **The card only knew whether a rule acted on D−1.** Added "a rule acted on D before 10:00", the ARCH §6.2 stand-down signal. The comparison uses it: a rule at ~08:00 on immature data vs the agent at 10:00 on matured history.
9. **The maturity gate (ARCH §8 failure #1) would go untested.** I asked whether the snapshot supports a partial-day view. Claude checked: only through `rule_executions` snapshots, so only for ACC-04 adsets the rules touched, at the moments they fired. In 06-10..12 that's 14 snapshots with spend ≥ $1. Usable near 14:00: case 4.3 (31273956645572, 14:30 local on 06-11) and 31626016833981 on 06-10/06-12; case 4.1 is on 06-08 and runs as an extra demo outside the window. A `--snapshot-pass` covers these; RESULTS.md must say the gate is otherwise the main untested piece.
10. **Escalations are graded twice:** once as the proposed action, once as the ARCH §2 default (`keep`).
11. **Report the day-1 adsets** that get no decision by design (count and spend share).

*Evaluation:*

12. **Leakage.** features.py asserts every card value comes from dates ≤ D−1, and peer forward-ROI windows end by D−1. A failure stops the run.
13. **Without an ablation, we can't tell what the LLM adds.** `--no-llm` replaces it with a simple code rule, scored on the same metrics.
14. **Raw LLM responses are saved, and `--replay` reruns everything without an API key,** so a reviewer can reproduce the results.
15. **Grading confound, stated explicitly.** Day D's outcome happened under what buyers and rules actually did, so it grades *direction*, not the agent's action. D+1 is preferred where it exists.
16. **Coverage and honesty items:** the share of adsets and spend that never reaches the LLM (auto_keep); the timezone (UTC+3, from INVESTIGATION issue 8); the code-decided (deterministic) route overlaps the client's existing kill rules, and RESULTS.md must say so.

### Step 4b: plan revision 3: four more corrections
- **Budget ≥ $100 also went to escalate with no LLM call.** ARCH §6.1 says the human queue sees "the agent's reasoning, the proposed action", and the ≥ $100 adset is 31302925337341: the case 4.1 showcase, carrying 18% of spend. It now goes through `llm_review` (and Sonnet, via the ≥ $50 rule) with approval forced. Escalating without an LLM call is kept only for critical data flags, where a proposal on bad data is worthless.
- **The portfolio guard trimmed the lowest-confidence actions first.** That could drop a code-decided pause on a clear loser ahead of a marginal 10% bump. It now trims by lowest expected profit impact, and the deterministic route is exempt.
- **The spend floor is defined on spend (ARCH §8), but at 10:00 only budgets exist.** The plan now says the budget sum is a proxy. A full 7-day trail doesn't exist for 06-10..12 (data starts 06-06), so it uses the available days and flags it.
- **"Replay reproduces decisions.jsonl byte for byte" would fail** on the run timestamp and cost fields. Now "identical except run metadata", and the check diffs with those fields removed.

### Step 4c: building it: what the data and the first runs changed
- **Claude got the "performance before creation" check wrong the first time.** It fired on 14–111 adsets a day. The cause: zero-spend placeholder rows exist for dates before many adsets were created. Restricted to rows with spend, it fires on exactly one adset, 730122709357812221. That adset spent $27 on 06-09 but was created on 06-10; its spend-day counter is 0; and a buyer note posted *before it existed* says "client brand test – keep live through the weekend regardless of ROI (account manager request)". This is the clearest no-clean-answer case in the data, so it escalates to a human with no LLM call.
- **Spend above 1.25× budget is common** (about 20% of spending rows, under both campaign- and adset-level budgets). It's a non-critical card flag, not a blocker.
- **Volume is higher than the architecture estimated:** 115–167 `llm_review` cards a day, not ~60. The $3 materiality line is low. I kept it rather than tuning it to hit a number; cost still fits.
- **The LLM layer can't use forced tool use:** Sonnet 5.5 returns a 400 on forced `tool_choice`. JSON comes back through structured outputs (`output_config.format`) and is validated with pydantic.

### Step 4d: my code review before the full run (10 items)
1. **Sonnet on every buyer-locked card could blow the run budget.** The dry run counted locked `llm_review` cards at 53 / 67 / 63 a day. *My call:* do both of the review's options. The Sonnet trigger is narrowed to *lock + evidence against the buyer* (the `buyer_conflict` part of `signal_agreement`): 15 / 21 / 5 a day. A lock where the evidence agrees with the buyer isn't a conflict worth a second opinion. `RUN_BUDGET_USD` also goes from 2.00 to 3.00, because the number of low-Haiku-confidence cards that also trigger Sonnet isn't known before the run, and a stop on day 3 would skew the evaluation.
2. **Reactivated adsets were excluded.** `active_adsets` dropped any adset with a kill on any earlier day, even if it spent afterwards (a re-enable the log can't show, issue 16). Now only a kill dated D−1 excludes it.
3. **Rule grading said "unclear" for most kills.** After a morning kill there's no spend left on D or D+1. Rules are now graded the Task A way: the ROI the rule saw vs the matured ROI of that day's spend. Result on the 11 rule actions in the window: 6 right, 4 wrong (the day matured positive), 1 neither.
4. **LLM escalations were all labelled `insufficient_evidence`.** Now `llm_escalated`, `llm_output_invalid` or `critical_data_flag` say who escalated; `insufficient_evidence` comes only from the sufficiency/agreement gates.
5. **`run_ts` on every row broke the replay check.** Moved to `out/run_meta.json`. `cost_usd` per row is now the cost from the stored response, so replayed rows match exactly. A `--limit` run writes `decisions_dev.jsonl` and can't overwrite a full run.
6. **The note penalty had no time window, and "test" matched "testing new bid".** Notes now count only in the last 72h, and "test" is gone from the instruction words.
7. **The floor check could never fire** after the 15% cut cap. It's now defined on expected account spend after actions vs trailing spend. It does fire: in ACC-05 it trims a cut and a pause, because expected spend is already below the floor before any action. That's a real tension: ACC-05 is the week's losing account (−$147), and the floor stops the agent from cutting it further without a human. RESULTS.md must say so.
8. **Caching may silently not apply.** The system prompt is ~3,600 characters (~900 tokens), above the 512-token minimum for these models. This gets confirmed from `cache_read` in the cost log after the first live run.
9. **Nits:** the dead `HUMAN_NOTE_WORDS` was removed. Confirmed `auto_rules` has no "increase" action (only Turn OFF / Turn ON / Decrease), so the `turn_on` fallback label is safe.
10. **The ±10% label band makes keeps look wrong and rewards activity.** compare.py now reports correct / harmful / neither, names the band as an assumption, and treats the harmful rate as the safety number. RESULTS.md will do the same.

### Step 4e: consistency with Task B, and the run order
- **The LLM load was ~2–3× what ARCHITECTURE sized.** I flagged 115–167 `llm_review` cards a day against ARCH §1's "a few dozen" and asked Claude to raise the materiality line toward $5–8.
  - **What Claude found:** raising the line alone couldn't get there. Even at $20, 75–82 cards a day remained, because buyer-locked cards (53–67 a day) skipped the materiality gate entirely.
  - **A correction to our own plan:** ARCH §5.1 actually sizes ~60 cards *per window* (3 × 60 = 180 a day); the plan's "40–60 a day" was a misreading.
  - **My call:** materiality goes to **$8 over 3 days**, and **a lock no longer exempts an immaterial adset**: a keep under a lock executes nothing and the buyer is already on it. This reverses part of rev 2 item 5. Locked adsets above $8 still get a proposal with approval forced.
  - **Result:** 91 / 78 / 61 cards a day; auto_keep holds 11% / 9% / 16% of 3-day spend.
  - **Side effect:** the deterministic route's only case (a $4 zero-revenue adset) is now below the line, so that route doesn't fire this week.
- **The spend floor trimmed proposals into silent keeps.** ARCH §8 says the floor holds "unless a human approves", so a floor breach now sets `requires_approval` with reason `spend_floor`, and the proposal survives into the approval queue. The cut, add and 40-action caps still trim to keep: those are autonomy limits the Executor enforces, and I left them as they were.
- **Run order (planned):** `--limit 5` first (cost log, `cache_read` check), then **06-10 alone in full** to measure the real Sonnet rate (including low-Haiku-confidence triggers). Multiply that by three and check it fits under the $3 run budget before running 06-11 and 06-12.

### Step 4f: the smoke test (5 cards, 10 calls, $0.034) and what it changed
- **Caching works.** A 1,869-token cached prefix is read on every call after each model's first one (code review item 8 closed). About $0.006 per card, almost all of it Sonnet.
- **Claude found a leak its own assertion missed.** Two of the five reasonings cited `metadata_status_end_of_week:ADSET_OFF` as evidence. That status comes from the end-of-week metadata snapshot, so on 06-10 it partly describes what happened *after* the decision (later rule kills). The leakage assertion only covered performance dates and event timestamps. The flag is removed from the card; the metadata status stays out of every card. The prompt version goes to v2, so no smoke-test response is reused.
- **The Haiku→Sonnet cascade collapsed.** At `low` effort Haiku stated 0.35–0.45 on every card, so the < 0.6 trigger sent all 5 to Sonnet. Worse, "one said keep → keep" let Haiku's unsure keep veto Sonnet. On 31124038476129 (3/3 profitable days, 3-day ROI +0.40) Sonnet proposed scale-up and the result was keep.
- **Claude proposed option A:** Haiku at `medium`, a trigger set relative to Haiku's confidence distribution measured on one day, and an unsure keep that doesn't count as a vote. **I took A with two changes:**
  - **No threshold fitted to the data.** A trigger calibrated on Haiku's confidences on 06-10 is tuned on the evaluation set. The threshold is fixed at **0.5**, chosen once before the first full day: below 0.5 Haiku is saying it doesn't know. If Haiku stays below 0.5 at medium effort too, Sonnet runs on everything, and that's a finding (these cards aren't routine), not a bug. The calibration table will show whether the low confidence was honest.
  - **The combine rule is the real fix.** When one model says keep and the other proposes a change, the change wins unless the keep is the *more confident* of the two. A tie goes to the change. Opposite directions still escalate; same direction takes the smaller change; the guard caps and approval triggers still apply afterwards. Checked on fabricated pairs before spending money: the 31124038476129 pattern now scales up, and a confident keep still stops a thin scale-up.

### Step 4g: one live day (06-10) before committing to three
- **06-10 in full:** 91 LLM cards, 138 calls, **$0.296**, 0 invalid outputs. That projects to ~$0.80 for three days, well under the $3 cap, so the run order paid off as a check rather than a surprise.
- **Haiku at `medium`:** median confidence 0.50 (0.35–0.72); 45% of cards are below the fixed 0.5 trigger, so Sonnet ran on 52%. The new keep-vs-change rule fired twice (one unsure keep overruled, one confident keep upheld).
- **Claude flagged a second veto.** Haiku escalated 13 times and Sonnet never escalated. On the 8 where Sonnet ran, it said keep, but "either escalates → escalate" kept Haiku's escalation. The other 5 got no second opinion at all, because an escalation wasn't a Sonnet trigger.
- **My call: the second opinion decides.** A Haiku escalation always triggers Sonnet; Sonnet's answer goes forward if it is confident (≥ 0.5, the same fixed threshold), otherwise it escalates; Sonnet escalating still escalates. The guard's agreement gate still escalates conflicting evidence regardless of what the models say. Rerunning 06-10 reused every stored response and billed only 5 new Sonnet calls ($0.031).
- **Consequence to report honestly:** 06-10 escalations fell from 14 to 1 (only the critical-flag brand-test adset). After this change the agent shows "I don't know" mostly through `keep` with low capped confidence and the sufficiency gate, not through escalation. RESULTS.md must show whether that was the right trade.

## Step 5 — Results & evaluation

**Asked:** after the full run ($0.85 for 728 decisions; replay byte-identical without an API key), review the evaluation before writing RESULTS.md.

### Step 5a: the first full run, and the analysis I asked for before writing
- **Headline from the first read: safe but passive.** 698 of 728 decisions were keep. On LLM cards the agent was harmful on 2% (vs 18% for the no-LLM rule). Buyers beat it on direction.
- **Claude found two bugs in the results:**
  - **Rounding** sent ±20% steps to approval (17.78 → 21.34 = +20.02%). It's a guard fix: snap steps within half a point of 20% to exactly 20%, and allow a 1-cent tolerance. Replayed at $0, it changed **3** rows, not the 1 Claude first spotted: on small budgets, cents can't hit 20% exactly.
  - **pandas NaN** passed `is not None` checks and put code-decided rows into a fake "escalate" calibration segment. Fixed with a JSON round trip before the ledger is written.
- **Claude flagged that the feedback loop would learn the wrong lesson.** All 8 lessons said "keep was wrong", and calibration would have made the agent *more* cautious. The cause is the ±10% direction band, which grades a keep as wrong on any non-flat day. **My call:** this isn't changing the metric after seeing results; it brings the stub in line with ARCH §7.2, which grades in dollars. Lessons and calibration now use a **$ grade**: regret = best of {keep, +20%, −20%, the action} minus the action's profit effect on day D (m = 0.75); right in $ if regret ≤ $0.50. The ±10% direction grade stays in the evaluation exactly as set before the run, and the $ grade sits next to it, **labelled as added after the run**.
- **My hypothesis was wrong, and the data showed it.** I asked for the 698 keeps to be decomposed, expecting the evidence gates (`DS_MIN` / `SA_MIN`) to explain the passivity: a config choice, not the model. Result:
  - 495 are auto_keep (materiality; 14% of D's spend);
  - **203 are "LLM said keep"** (68% of D's spend), with median data_sufficiency 0.68 and signal_agreement 0.80: the evidence was *not* thin;
  - **0 are gate conversions** (1 agreement-gate escalation, 0 thin-evidence caps).

  The sensitivity replay at `DS_MIN` 0.25 produced **identical** decisions. The passivity comes from the model and the prompt (its "scale up only on consistent matured profit" rules), not from the guard.
- **Buyers graded the same way as the agent** (I asked for symmetric grading; the first comparison counted every agent keep as wrong whenever a buyer moved). On the 187 adset-days buyers touched: buyers correct 75%, harmful 13%; agent correct 11%, harmful 1%.
- **Calibration on change decisions only:** n = 16, Brier 0.265. Too few to say anything. The cap never bound on changes (raw = capped).

### Step 5b: closing the loop: a prompt revision from the top $ lesson, with a held-out split
- **My call: the headline is "passive", not "safe".** The mandate is absolute profit with spend maintained, so giving up ~$7 in three days at 1% harm is a miss against the mandate, not a trade-off the business asked for. The low harm rate is what makes a fix safe to attempt.
- **I asked for one prompt revision, done properly.** Turn the top $ lesson into policy, choose the wording on 06-10 only, and hold out 06-11 and 06-12.
  - **Claude flagged a split problem first:** the lesson had been computed from the whole week's ledger, held-out days included. It re-derived the lessons from 06-10 alone; the same top pattern holds there (keep on 4+-day adsets with positive 3-day history, day matured positive: 6 cases, $5.04 regret). The profile also showed 3 kept winners that matured *negative* ($2.48), so the policy couldn't simply be "positive history → scale".
  - **Naming:** the version that ran was already labelled v2 (bumped for the leak fix), so the revision is **v3**. Version names key the response store, so changed text always gets a new name.
- **v3 (development iteration 1):** a proven winner (4+ spend days, ≥ 2 of 3 matured days profitable, 3-day ROI and blended ROI > +0.10, using the evaluation's own label band fixed in advance, not fitted) is a scale-up candidate, kept only if a named card field argues against it; plus looser "more positive than not" wording.
  - **On 06-10:** scale-ups went from 5 to 8; regret $16.60 → $15.94; harm unchanged at 3%; profit vs keep +$1.51 → +$0.85.
  - Of 26 proven-winner cards, v3 scaled 8 and kept 18, almost always naming a card field as instructed. But on 5 cards it kept *because a buyer had just raised the budget*: it read agreeing with the buyer as overriding them.
- **v3b (development iteration 2, still 06-10):** one added sentence: scaling in the same direction a buyer just moved is agreeing, not overriding. **Freeze rule, written before seeing the result:** freeze v3b only if it adds scale-ups on proven winners without raising the 06-10 harm rate; otherwise freeze v3.
  - **Result:** scale-ups 5 → 14 (7 under locks), but scale-downs 2 → 10, harm 3% → 6%, regret $17.86, profit vs keep −$1.00. The sentence also pulled the model into following buyer *cuts*.
  - **v3b rejected, v3 frozen.** v3 then ran on the held-out days.
- **Held-out result for frozen v3 (06-11 + 06-12, 139 LLM cards), vs v2:** changes 12 → 26; profit vs keep +$3.86 → **+$6.23**; regret $35.17 → $32.80; harm 2% → 6% (2 → 6 cases). The no-LLM rule on the same cards: +$10.66, harm 20%. v3 moves along the frontier in the intended direction, on small numbers. The buyer-raise misread survives in v3 (it kept the case 4.3 adset on 06-12, which closed at +0.40); the next iteration should be narrower than v3b.
- **Total API spend for all of Task C: $2.05** over 877 calls (the production run of 728 decisions: $0.85).
- **The first v3b run died on a network error** (`APIConnectionError` after the SDK's 3 retries). Stored responses made the rerun cheap, but one failed card stopping the whole run is a gap against ARCH §8 (escalate the card and continue). It goes into RESULTS.md's weaknesses.

### Step 5c: final review of RESULTS.md (my wording fixes)
- **Two action counts that looked inconsistent.** `out/portfolio_guard.txt` shows 21 executable actions (11 / 5 / 5); the §5.1 counterfactual counts 15. My guess was zero-spend adsets; Claude traced `compare.dollar_impact` and confirmed it: the 6 uncounted actions hit adsets that spent $0 on day D (no effect by construction). Claude added a detail I hadn't asked for: all 6 are cuts, so 6 of the agent's 9 executable cuts landed on adsets that had already stopped spending. RESULTS §5.1 now says so.
- **The headline harm rate hid where the harm was.** I asked for an unsoftened line: the 2% is carried by keeps; on the 14 graded scale-ups the agent was harmful 21% of the time by direction, while their dollar effect was positive (+$4.21 profit vs keep, $2.81 regret). This matters because §4 argues for scaling more.
- **Earlier in the same review** (five clarifications): the two profit figures (+$4.59 executable counterfactual vs +$5.38 profit vs keep on all LLM cards); the direction numbers favouring v3 by construction (its bar is the label band), with the dollar metrics as the fair test; the human load (9 approvals in three days, 17 under v3, vs the 15–25/day ARCHITECTURE assumed); and why keep the LLM (a 9× lower harm rate and decisions that say why). **One correction to my own request:** I wrote that the 51 confidence caps were all keeps. Claude checked: 45 keeps, 4 pauses, 1 scale-down, 1 escalation. It also corrected "the cap never bound" to what is actually true (raw = capped on the 16 graded change decisions).
- **The Verdict contradicted the new §3.1 line.** It still presented the 2% harm rate as what makes a fix safe. I rewrote that bullet: the 2% comes mostly from how rarely the agent acts; where it did act it was about as error-prone as the plain rule (21% on scale-ups vs 18%). What makes loosening safe to attempt is that the loop measures harm as it goes: v3 doubled the changes on the held-out days and harm went from 2% to 6%, still far below the rule's 20% on the same days.
