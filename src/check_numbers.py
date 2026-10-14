"""Consistency pass for INVESTIGATION.md: every number -> the out/ file + line it comes from.
Each entry = (claim as it appears in INVESTIGATION.md, source file, regex the source line must match).
Fails loudly if a claim is missing from the doc or no source line matches. Writes notes/number_sources.md."""
import re
import sys

from db import ROOT

DOC = (ROOT / "INVESTIGATION.md").read_text(encoding="utf-8")
F, I, L, R, S, P, D, A, Q = ("task_a_facts.txt", "impact_summary.txt", "losers_and_reversals.txt", "revenue_delay.txt",
                             "sanity_checks.txt", "profile.txt", "rule_roi_delta.csv", "impact_actions.csv", "sanity_checks.txt")
DERIVED = None  # arithmetic on cited lines; explained in the note column

E = [
    # ---- verdict
    ("cuts (−$16 to −$31)", I, r"^1 budget cuts .* -15\.73 .* -31\.47", ""),
    ("kills other than R04 (−$10)", I, r"^2 kills excl\. R04 .* -9\.81", ""),
    ("**Measurable net: −$26 to −$41 profit.**", I, r"^4 total excl\. R04 .* -25\.54 .* -41\.28", ""),
    ("**3–5% of all six accounts' profit ($878)**", L, r"^ +NaN +983 8912\.0 +878\.0", "25.54/878 = 2.9%, 41.28/878 = 4.7%"),
    ("**5–8% of ACC-04's ($490)**", L, r"^ +ACC-04 +114 2586\.0 +490\.0", "25.54/490 = 5.2%, 41.28/490 = 8.4%"),
    ("**$154 on cuts**", I, r"^1 budget cuts .* -153\.72", ""),
    ("**$72 on other kills**", I, r"^2 kills excl\. R04 .* -71\.85", ""),
    ("9 of 12 effective cuts fired before 09:00 local", F, r"^cuts_before_09 +9$", ""),
    ("**6 of 12 cut an adset at ROI ≤ 0 on a day that ended positive**", F, r"^cuts_seen_le0_matured_gt0 +6$", ""),
    ("**one account (ACC-04) out of six**", P, r"^9913405683583663 +ACC-04 +214$", ""),
    ("**78 effective actions** out of 214 log rows", I, r"^5 total incl\. R04 +78\.0", ""),
    ("mostly kills of ~$1–$5/day test adsets", F, r"^ +R04 +39 +1\.270 +1\.27 +5\.0800", "49 of 66 kills are R03/R04 at $1.27–$5.08; median of all 66 = $1.27"),
    ("R04's 39 kills judged $32 of spend in total", I, r"^3 R04 kills +39\.0 +31\.94", ""),
    ("lost $690 across the week", L, r"^ +NaN .* -690\.0 +630", ""),
    ("would have saved only **~$31**", I, r"^TOTAL +293\.0 .* 31\.06$", ""),
    ("left 27 actions unapplied (30 OAuth failures across the week)", F, r"^ +True 27$", "F2; 3 more outside the window"),
    ("including 8 attempts by the rule that exists to undo automation mistakes", F, r"^ +R09 +8 +0 +1 +8 +0$", ""),
    # ---- 1.1 funnel
    ("214  rule executions logged (all ACC-04)", F, r"^ +214 +30 +20 +164 +152 +66 +12 +8 +0$", "F1"),
    ("−30  failed: OAuth", F, r"^ +214 +30 ", ""),
    ("06-08 ~03:00–17:00 UTC, plus 3 later", F, r"^ +False +3$", "F2"),
    ("−20  no-op", F, r"^ +214 +30 +20 ", ""),
    ("(15 of 20: engine could not read the live budget)", F, r"^ +True 15$", "F3"),
    ("164  SUCCESS", F, r"^ +214 +30 +20 +164 ", ""),
    ("152 Turn OFF rows → on 66 adsets", F, r"^ +214 +30 +20 +164 +152 +66 ", ""),
    ("(86 are repeats on an adset already off)", F, r"^ +214 +30 +20 +164 +152 +66 ", "152 − 66"),
    ("12 budget cuts", F, r"^ +214 +30 +20 +164 +152 +66 +12 ", ""),
    ("(the 8 Turn ON attempts all failed)", F, r" +8 +0$", "turn_on_rows 8, turn_on_success 0"),
    # ---- 1.2 per rule
    ("| R01 | Turn OFF, total days ≥ 5 | 4 | 4 | 3 | 3 |", F, r"^ +R01 +4 +4 +3 ", "effective 3: impact_summary R01 n"),
    ("| R02 | −20%, −30 < ROI ≤ −10 | 23 | 6 | 6 | 4 |", F, r"^ +R02 +23 +6 +4 ", "effective 6: impact_summary"),
    ("13 no-ops on one adset (06-09)", F, r"^ +R02 31626016833981 +2026-06-09 .* 13$", "F23"),
    ("| R03 | Turn OFF, 0 positive days, > 2 days | 36 | 17 | 10 | 13 |", F, r"^ +R03 +36 +17 +13 +19 ", "effective 10: impact_summary"),
    ("its \"age\" disagrees with perf in 28/36", P, r"^ +R03 +36 +28$", ""),
    ("| 109 | 109 | 39 | 39 |", F, r"^ +R04 +109 +109 +39 ", ""),
    ("2.8 firings per kill; median spend at firing **$0.57**", F, r"^ +109 +39 +2\.8 +0\.57$", "F5"),
    ("| 7 | 6 | 4 | 6 |", F, r"^ +R05 +7 +6 +6 +1 ", "effective 4: impact_summary"),
    ("| R08 | Turn OFF, total days = 4 | 15 | 13 | 9 | 10 |", F, r"^ +R08 +15 +13 +10 +2 ", "effective 9: impact_summary"),
    ("| 8 | 0 | 0 | 1 | All 8 failed (OAuth).", F, r"^ +R09 +8 +0 +1 +8 ", ""),
    ("| R10 | −15%, −10 < ROI ≤ 5, budget ≥ $100 | 2 | 2 | 2 | 1 |", F, r"^ +R10 +2 +2 +1 ", ""),
    ("Both firings came after R03 had already killed the same adset", F, r"^31570891150705 2026-06-07T21:30:15.* 2026-06-07T21:30:09", "F6"),
    ("| R12 | −40%, ROI ≤ −50, budget ≤ $65 | 6 | 3 | 3 | 3 |", F, r"^ +R12 +6 +3 +3 +0 +3$", ""),
    ("Firing counts match `auto_rules.firings` exactly for all 12 rules.", P, r"^ +R04 +109 +109$", "all 12 rows equal"),
    # ---- 1.3 scope
    ("All 214 executions are on **ACC-04**.", P, r"^9913405683583663 +ACC-04 +214$", ""),
    ("**None** of the 75 adsets acted on is named `own_rsoc`", S, r"^ +False +slg +75$", ""),
    ("ACC-04's 23 `own_rsoc` adsets got zero actions", F, r"^ +23 +0$", "F7"),
    ("reproduce all 39 of its real firings", S, r"^ +R04 +48 +39 +39$", ""),
    ("**15 / 97 / 50 / 96 / 35**", S, r"^ +ACC-06 +544 .* 35 ", "3b r04 column, ACC-01/02/03/05/06"),
    # ---- 1.4 timing
    ("05, 05, 05, 06, 06, 07, 07, 08, 08, 14, 18, 22", F, r"^cut_hours_local +\[5, 5, 5, 6, 6, 7, 7, 8, 8, 14, 18, 22\]", ""),
    ("76% of the spend the cut rules judged", F, r"^share_cut_spend_judged_before_09 +0\.76$", ""),
    ("(16 / 9 / 27 / 14 across the four 6-hour blocks)", F, r"'00-05': 16, '06-11': 9, '12-17': 27, '18-23': 14", ""),
    ("0 of 66 killed adsets spent on any later day", S, r"^ +66 +0$", "2d"),
    ("57/66 show spend after the kill (median 9% of the day, $9.44 total)", S, r"^ +66 +57 +9\.44 +0\.09$", "2a"),
    # ---- 2 delay
    ("| 00–06 | **0.69** (median 0.43) | 8 |", R, r"^ +a 00-06 +8 .* 0\.69 +0\.43$", "1c"),
    ("| 06–12 | **0.83** (median 0.93) | 15 |", R, r"^ +b 06-12 +15 .* 0\.83 +0\.93$", "1c"),
    ("| 12–18 | 1.00 | 25 |", R, r"^ +c 12-18 +25 .* 1\.00 +1\.01$", "1c"),
    ("| 18–24 | ~1.0 (1.08) | 32 |", R, r"^ +d 18-24 +32 .* 1\.08 +1\.05$", "1c"),
    ("revenue stays flat for ~2 h while spend rises", R, r"^ +21 +69\.10 +51\.22", "1d: rev 50.47 (19h) → 51.22 (21h) while spend 55.50 → 69.10"),
    ("13 no-op attempts", F, r"^ +R02 31626016833981 +2026-06-09 .* 13$", "F23"),
    ("6 rows show revenue on a zero-spend day", Q, r"^ +False +False +277 +6 +11\.42$", "sanity 1"),
    ("(1–4 days after the last spend)", R, r"^ +31206122123796 2026-06-11 .* 2026-06-07", "1f: max gap 4 days; min 1 day"),
    ("$11.42 total, ~0.1% of the week's revenue", Q, r"^ +False +True +1922 +1473 +9778\.21$", "11.42 / 9778.21 = 0.12%"),
    ("2 executions saw *more* spend than the final perf row", R, r"^2026-06-12 +8 +2 +0\.75$", "1e"),
    ("These are 80 executions", R, r"^ +d 18-24 +32 ", "1c: 8 + 15 + 25 + 32"),
    ("understates the day's revenue per $ by 17–31% (1 − 0.83 and 1 − 0.69)", R, r"^ +a 00-06 +8 .* 0\.69 ", ""),
    # ---- 3.1 method
    ("revenue per $ ranges 0.79–1.19 across accounts", F, r"^ +ACC-05 +0\.786 ", "F11: min ACC-05 0.786, max ACC-04 1.186"),
    ("day-1 revenue per $ ranges 0.70–1.09", F, r"^ +ACC-01 +1\.181 +1\.088 ", "F11: min ACC-05 0.695, max ACC-01 1.088"),
    ("ACC-02: 0 of 69 R04-qualifiers spent the next day", F, r"^ACC-02 +69 +0$", "F12"),
    ("ACC-06: 17 of 29", F, r"^ACC-06 +29 +17$", "F12"),
    # ---- 3.3 results
    ("| **Budget cuts** | 12 | **$154** | **−$16 / −$24 / −$31**", I, r"^1 budget cuts +12\.0 .* -153\.72 +-15\.73 +-23\.63 +-31\.47$", "SPLIT"),
    ("| Kills (excl. R04) | 27 | $72 | −$10 |", I, r"^2 kills excl\. R04 +27\.0 .* -71\.85 +-9\.81", ""),
    ("over-predicted 1.7–35.5×", I, r"^0 +13 +8 +5 +1\.7 +35\.5$", "VALIDATION summary"),
    ("| **Measurable total (excl. R04)** | 39 | **$226** | **−$26 to −$41** |", I, r"^4 total excl\. R04 +39\.0 .* -225\.57 +-25\.54 .* -41\.28$", ""),
    ("| R04 kills | 39 | $86", I, r"^3 R04 kills +39\.0 .* -86\.28 +83\.01", ""),
    ("(model: +$83)", I, r"^3 R04 kills .* 83\.01$", ""),
    ("| **All 78 actions, as modelled** | 78 | **$312** |", I, r"^5 total incl\. R04 +78\.0 .* -311\.85", ""),
    ("**Spend lost: $312 as modelled = $154 (cuts) + $72 (other kills) + $86 (R04).**", I, r"^5 total incl\. R04 .* -311\.85", "153.72 + 71.85 + 86.28 = 311.85"),
    ("The kill figures ($158 together)", I, r"^2 kills excl\. R04 .* -71\.85", "71.85 + 86.28 = 158.13"),
    ("−$16.81 profit and −$77.59 spend over two early-morning cuts", I, r"^ +R10 +2 +179\.59 +-77\.59 .* -16\.81", "rule table"),
    # ---- 3.4 R04
    ("**peer ROI comes from 7 adset-days** (forward ROI −1.0)", I, r"R04 SENSITIVITY \(model central = 83\.01; peer n = 7", "peer ROI −1.000: Top-10 saves rows"),
    ("moves R04 from +$85 to +$3", I, r"^3 +peer_roi = \+0\.00 +3\.13$", "and peer_roi = −1.00 → 84.83"),
    ("the saving is $2–$48", I, r"^5 +modelled spend / 35\.5 .* 2\.34$", "and / 1.7 → 47.84"),
    ("**$0.57** (all 109 firings)", F, r"^ +109 +39 +2\.8 +0\.57$", "F5"),
    ("its 39 effective kills judged **$31.94** in total", I, r"^3 R04 kills +39\.0 +31\.94", ""),
    ("(mean gap +0.006)", F, r"^ +0\.006$", "F9"),
    ("And the adsets it killed were losers on the day.", F, r"^kills=39 matured_roi_gt_0=0 ", "F24"),
    # ---- 3.5 validation
    ("### 3.5 Validation: a sanity check, n = 13 adset-days", I, r"^0 +13 +8 ", ""),
    ("5 OAuth-failed kills and 8 no-op cuts", I, r"^0 +13 +8 +5 ", "13 − 5 = 8 cuts"),
    ("in **8 of 13**", I, r"^0 +13 +8 ", ""),
    ("**1.7–35.5× the observed spend**", I, r"^0 +13 +8 +5 +1\.7 +35\.5$", ""),
    # ---- 3.6 losers
    ("lost **$63 on $179 spend**. 7 of the 9", L, r"^ +NaN +9 +179\.0 +-63\.0 +-62\.0 +7$", "2b"),
    ("| 730121092582511364 | ACC-05 | 4 | 3 | $20.27 | −$9.39 | −46% |", L, r"^730121092582511364 +ACC-05 +4 +3 +20\.27 +-9\.39 +-0\.463", "2b-list (all 9 rows)"),
    ("| 31326123629255 | ACC-04 | 4 | 3 | $9.13 | −$5.09 | −56% |", L, r"^ +31326123629255 +ACC-04 +4 +3 +9\.13 +-5\.09 +-0\.558", ""),
    ("699 losing adsets that no rule touched lost **$690 on $2,032**", L, r"^ +NaN +983 .* 699 +2032\.0 +-690\.0", "2a"),
    ("460 of them at a week ROI ≤ −50%", L, r"^a roi <= -50% 460 ", "2d"),
    ("$658 of it was in the five accounts without rules", L, r"^ +ACC-04 .* 22 +103\.0 +-32\.0", "690 − 32 = 658"),
    ("the post-trigger 6% of day 1", I, r"After-trigger = 6% of day-1", ""),
    ("**saves $36, burns $5, net ~$31**", I, r"^TOTAL +293\.0 .* 36\.51 +-5\.45 +31\.06$", ""),
    # ---- 4.1
    ("spent $216, $320, $264 and $127 over 06-06 to 06-09, at ROI **+0.50, −0.00, +0.22, +0.33**", F, r"^2026-06-09 +126\.78 +169\.20 +0\.33", "F21 daily perf 31302925337341 (06-06..06-09)"),
    ("On 06-07 at 15:03 UTC a buyer set its budget to **$254** (`ui_update_budget`)", F, r"^2026-06-07 15:03:55.* buyer:ui_update_budget +213\.89 +254\.0", ""),
    ("R10 cut it 15% to $215.90", F, r"^ +2026-06-08 02:00:05.*R10 .* 254\.0 +215\.9 +-0\.04 +70\.4469", "02:00 UTC = 05:00 local"),
    ("had seen $70 of the day's $264 spend (**27%**) at ROI −0.04 (≈ 0.00 at full precision)", D, r"^129,2026-06-08,.*R10.*,70\.4469,264\.0584,0\.27,", "roi_seen 0.000 (rpc/cpa): impact_actions.csv R10 06-08"),
    ("The day matured at **+0.22**.", D, r"^129,.*,-0\.04,0\.2175,", ""),
    ("R10 had already cut it from $314.55 to $267.36", F, r"^ +2026-06-07 05:30:07.*R10 .* 314\.5536 +267\.3706", "05:30 UTC = 08:30 local"),
    ("the reading is +0.23", A, r"^56,|^31302925337341,R10,cut,2026-06-08,5,.*,0\.229,", "roi_own_corrected 0.229"),
    # ---- 4.2
    ("Adset `31314467522499` ($1.27/day) had ROI −0.61 on day 2 and −0.20 on day 3", F, r"^2026-06-07 +1\.66 +1\.33 +-0\.20 +3$", "and 06-06 −0.61 day 2"),
    ("at ROI −0.37 on $0.27 of spend", F, r"^2026-06-08 01:00:09.*R08 Turn OFF SUCCESS +1\.27 .* -0\.37 +0\.2667$", "01:00 UTC = 04:00 local"),
    ("84% of the day's spend but only **27% of the day's revenue**", D, r"^128,2026-06-08,.*,0\.84,0\.17,0\.635,", "0.17 / 0.635 = 27%"),
    ("The day matured at **+1.00**.", D, r"^128,.*,-0\.37,1\.0,", ""),
    ("It fired 8 times between 06:30 and 15:30 UTC", F, r"^2026-06-08 15:30:14.*R09 Turn ON", "8 rows 06:30..15:30"),
    ("tiny ($0.31 modelled; the budget is $1.27)", A, r"^31314467522499,R08,kill,2026-06-08,.*,-0\.31", ""),
    # ---- 4.3
    ("had ROI **+1.03, +0.10, −0.23, +0.20, +0.20** on 06-06 to 06-10", F, r"^2026-06-10 +12\.88 +15\.42 +0\.20 +8$", "F21 daily perf 31273956645572"),
    ("raising the budget 8.00 → 13.60 on 06-09 (`ui_adjust_budget`)", F, r"^2026-06-09 14:12:25\.877476.*ui_adjust_budget +8\.0 +13\.6", ""),
    ("R02 cut it 20% (13.60 → 10.87) on ROI −0.14 (−0.10 at full precision)", F, r"^ +2026-06-11 11:30:04.*R02 .* 13\.6017 +10\.8814 +-0\.14 +8\.5471", "−0.095 in impact_actions.csv"),
    ("with about half the day's spend seen", D, r"^200,2026-06-11,.*,8\.5471,16\.5227,0\.52,", ""),
    ("At **16:46 local a buyer raised it to 17.22.**", F, r"^2026-06-11 13:46:08.*ui_adjust_budget +10\.87 +17\.22", "13:46 UTC; API echo 13:46:04 on the line above"),
    ("The day matured at **+0.12**.", D, r"^200,.*,-0\.14,0\.1214,", ""),
    ("(23.57, 29.92) and 06-12 closed at **+0.40**", F, r"^2026-06-12 +15\.27 +21\.42 +0\.40 +10$", ""),
    ("ignored 4 of the previous 5 days being positive", F, r"^2026-06-08 +9\.88 +7\.63 +-0\.23 +6$", "the one negative day"),
    ("$ impact is negligible (−$0.03)", A, r"^31273956645572,R02,cut,2026-06-11,.*,-0\.03", ""),
    # ---- 5 data issues
    ("72 rows, all on 2026-06-09", P, r"^2026-06-09 +72$", ""),
    ("All 2,676 null-revenue rows (deduped) have spend = 0", Q, r"^ +True +False +2676 +0", ""),
    ("6 rows, $11.42, 1–4 days after the last spend", Q, r"^ +False +False +277 +6 +11\.42$", ""),
    ("14-digit = ACC-04 only", P, r"^ +ACC-04 +14 +117$", ""),
    ("100% of IDs in the rule log and buyer log join to perf + metadata verbatim", P, r"^ +buyer_actions +185 +185 +185$", "and rule_executions 75/75/75"),
    ("= 100 for 178/185 adsets across all accounts", F, r"^ +185 +178$", "F13"),
    ("58 of 66 rule-killed adsets show `ACTIVE` + `delivery_status = ADSET_OFF`; 1 shows `ACTIVE/ACTIVE`", F, r"^ +ACTIVE +ADSET_OFF +58$", "F14"),
    ("For 10/12 cuts the engine's own API row", F, r"^ +R02 31626016833981 2026-06-12T15:00:05.* False$", "F15: 10 True, 2 False"),
    ("one predates buyer-log coverage", F, r"^2026-06-06 14:00:10", "F16 vs R12 at 06-06 04:30 UTC"),
    ("21 rows at 21:00–23:59 UTC carry the next date", P, r"^ +23 +11$", "2 + 8 + 11"),
    ("66 (rule, adset, day) groups fired > once", F, r"^ +66 +193\.0$", "F10"),
    ("30 OAuth errors, mostly 06-08 03:00–17:00 UTC", F, r"^ +True 27$", ""),
    ("20 rows; 15 have `current_budget_from_fb` = null", F, r"^ +True 15$", ""),
    ("~2.3× the matching 3-day perf revenue", R, r"^ +34 +2\.28 +1\.0$", "1h"),
    ("(R04 107/109, R10 2/2, R12 6/6, R02 21/23; misses are rounding)", F, r"^ +R04 +109 +107 +0$", "F17; rounding F18"),
    ("in 28/36 firings", P, r"^ +R03 +36 +28$", ""),
    ("R03 fired on adsets 1–2 spend-days old by perf's count", F, r"^ +2 +27$", "F25: 1 + 27 = 28 at day 1–2"),
    ("contradicted 5× in the log", F, r"^ +R02 2026-06-10T19:30:05.* True +False$", "F19: 5 literal=True rows"),
    ("10 of 48 `update_ad_set_budget` rows are the rule engine", L, r"^ +update_ad_set_budget +48 +10$", "4d"),
    ("4 exact duplicate rows", P, r"^ +buyer_actions +4$", ""),
    ("same-second triplicates", F, r"^ +4 +11\.0 +7\.0$", "F20"),
    ("log starts 06-06 14:00 UTC", F, r"^2026-06-06 14:00:10", "F16"),
    ("286 rows with no adset_id", P, r"^ +campaign +update_campaign_budget +25 +0$", "108 + 153 + 25"),
    ("R04 fired once at a precise ROI of −0.46", F, r"^31500462861313 .* -0\.4634$", "F18"),
    ("2 executions on 06-12 saw more spend than the final row", R, r"^2026-06-12 +8 +2 +0\.75$", ""),
    ("Median 9% of the day's spend lands after a kill", S, r"^ +66 +57 +9\.44 +0\.09$", ""),
    ("recompute exactly (1,922 rows with spend)", P, r"^1922 +0 +0$", ""),
    ("No negative spend; no clicks > impressions.", P, r"^ +0 +0 +42 +10$", ""),
    ("42 rows with Meta conversions > clicks; 10 rows with ROI > 5", P, r"^ +0 +0 +42 +10$", ""),
]

rows, bad = [], []
for claim, fname, pattern, note in E:
    in_doc = claim in DOC
    src = (ROOT / "out" / fname).read_text(encoding="utf-8").splitlines()
    hit = next((i + 1 for i, line in enumerate(src) if re.search(pattern, line)), None)
    status = "ok" if in_doc and hit else ("CLAIM NOT IN DOC" if not in_doc else "NO SOURCE LINE")
    if status != "ok":
        bad.append((status, claim))
    rows.append(f"| {claim.replace('|', '\\|')} | `out/{fname}:{hit}` | {note} | {status} |")

md = ["# INVESTIGATION.md: number sources", "",
      "Generated by `src/check_numbers.py`. Each claim is matched verbatim in INVESTIGATION.md, and the cited line "
      "is matched by a regex that pins the value (see the script). Line numbers refer to the current `out/` files.", "",
      "| Claim in INVESTIGATION.md | Source | Derivation / note | Check |", "|---|---|---|---|"] + rows
(ROOT / "notes" / "number_sources.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print(f"{len(E)} claims checked, {len(bad)} problems")
for b in bad:
    print("  ", *b)
sys.exit(1 if bad else 0)
