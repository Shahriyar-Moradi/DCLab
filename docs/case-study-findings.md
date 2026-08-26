# Case Study Benchmark Findings

**What this document is:** the honest result of running six business decision case
studies — three on real Olist marketplace data, three on controlled synthetic data with
known ground truth — through both a single, genuinely-tuned baseline model and DCLab's
full multi-model search + ensemble, then comparing not just ML metrics but the actual
business decisions each would have produced. Full methodology, code, and raw artifacts
are in `benchmarks/`, `configs/case_studies/`, `configs/policies/case_studies/`, and
`artifacts/case_studies/*/`. The consolidated numbers behind every claim in this
document are in `reports/case_study_scorecard.md`.

**The one-paragraph honest answer:** across these six case studies, DCLab's multi-model
search-and-ensemble architecture did **not** produce measurably better business
decisions than a single, competitively-tuned baseline model — in zero of six case
studies did the ensemble's advantage clear the (symmetric, pre-stated) bar for
"meaningfully better," in two it was measurably worse, and in the remaining four the
result was statistically indistinguishable from no difference once tested across
several time windows rather than one. The dominant, identifiable cause is not that the
ensemble architecture is unsound — it is that the DCLab engine, as it stands today, runs
its candidate search with **zero hyperparameter tuning** (`SearchConfig.max_hyperparameter_trials
= 0` in `apps/api/app/engine/types.py`), while the baseline it's being compared against
received a genuine 40-trial random search. A multi-model ensemble of under-tuned
candidates is not competing on equal footing with one well-tuned model, and on this
evidence, it does not win anyway.

---

## Verdict summary (from `benchmarks/scorecard.py`, thresholds below)

| Case study | Data | Decision-impact Δ (Step 3) | Fold-mean Δ (Step 5) | Fold-stable? | Verdict |
|---|---|---|---|---|---|
| Purchase Prediction | real (Olist) | +25.3% | +12.9% | **no** | no meaningful difference |
| Reactivation *(repeat-purchase proxy)* | real (Olist) | −59.7% | −23.2% | **no** | no meaningful difference |
| Customer Value | real (Olist) | −57.9% | −28.1% | yes | **baseline preferable** |
| Lead Conversion | synthetic, ground truth | −2.2% | −6.8% | **no** | no meaningful difference |
| Upsell / Cross-sell | synthetic, ground truth | −2.5% | −16.1% | yes | **baseline preferable** |
| Campaign Response | synthetic, ground truth | −4.1% | +0.4% | **no** | no meaningful difference |

**Verdict rule** (symmetric, defined in `benchmarks/scorecard.py::compute_verdict`,
computed the same way for every case study): if the realized-value lift's sign is not
consistent across all 4 of Step 5's walk-forward folds, the verdict is "no meaningful
difference," full stop — a number that flips depending on which time window it's
measured on is not evidence of anything, no matter how large Step 3's single-split
number looked. Otherwise, the mean fold lift decides: ≥+10% → "ensemble meaningfully
better," ≤−10% → "baseline preferable," between → "no meaningful difference." Nothing in
this rule can only ever favor one side — see `benchmarks/scorecard.py`'s own unit-level
proof that all three outcomes are reachable from the same function.

**Result: 0 "ensemble meaningfully better," 4 "no meaningful difference," 2 "baseline
preferable."** This threshold and this fold count were fixed before the fold reruns
were executed and were not adjusted afterward to change the outcome.

---

## Real-data findings (CS1 Purchase Prediction, CS2 Reactivation, CS3 Customer Value)

These three ran on actual Olist marketplace history — real customers, real orders, real
revenue — not simulated data. All three share a structural feature that turned out to
dominate the results: **the outcomes being predicted are extremely rare.** Positive
rates: 0.54% (purchase-within-60-days), 0.42% (repeat-purchase-within-60-days on an
already-dormant population), and a customer-value target where the overwhelming
majority of customers generate $0 in the next 90 days (mean $1.14, but a max of $1,237
and a standard deviation of $18.68 — a heavy-tailed, near-zero-inflated distribution).
With test sets of ~20-30K rows but only on the order of 100-160 actual positive
outcomes, both models' apparent "advantage" is being decided by how they rank a
small, hard-to-learn handful of rare events — which is exactly the kind of number that
should be expected to swing wildly across different time windows and geographic
segments, and it did.

### CS1 — Purchase Prediction: **no meaningful difference**

Step 3's single test split showed the ensemble producing 25.3% more realized decision
value than the baseline. That looked like a real win. Step 5's walk-forward re-test
told a different story: across four folds, the lift was −5.5%, +3.2%, +28.7%, +25.3% —
the sign of the "advantage" flips depending on which historical window is used, and the
+25.3% Step 3 number turns out to be the two best folds, not a representative one.
Segment breakdown reinforces this: of 8 Brazilian states with enough volume to measure,
every one shows the ensemble's lift shrinking by ≥10 percentage points versus the
aggregate, with several states (e.g. state `'7'`, n=592) showing the ensemble
**75 points worse** than baseline in that state alone. The raw ML metric (pr_auc) also
favors the baseline outright (−13.8% for the ensemble). **Genuine hypothesis:** with a
0.54% positive rate, both models are trying to rank an extremely rare event, and
neither is confidently right often enough for the difference between them to be
anything but noise from which handful of rare positive customers happened to land in
which split. This is not "the ensemble sometimes helps and sometimes doesn't" — it's
that neither model has enough positive signal in this data to reliably beat the other.

### CS2 — Reactivation (Repeat-Purchase Proxy): **no meaningful difference**

> **Required honesty note, carried through from the case study config:** Olist has no
> subscription or contract relationship, so there is no real churn event in this data.
> This case study scores customers who have already gone quiet (no order in the 90 days
> before the snapshot) on whether they place another order in the next 60 days. Every
> result below is evidence about win-back targeting on a repeat-purchase proxy label —
> **it is not, and should never be presented as, a validated churn model.**

This case study produced the single most striking split between "the raw ML metric"
and "the business decision" in the entire benchmark: the ensemble's pr_auc was **167%
higher** than the baseline's (0.0171 vs. 0.0064) — by the metric the engine actually
optimizes for internally, the ensemble looks dramatically better. But Step 3's
decision-impact number went the other way (−59.7% realized value for the ensemble), and
Step 5's fold retest shows why neither number should be trusted alone: fold lifts were
+106.8%, −63.2%, −76.9%, −59.7% — a swing of over 180 percentage points across four
walk-forward windows on a population that starts at only ~91K training rows and an
already-rare 0.42% positive rate, filtered down further to only already-dormant
customers. **Genuine hypothesis:** a large pr_auc gap on a target with well under 1%
prevalence is easy to produce from correctly ranking a tiny handful of extra positives
near the top of the list — it does not take much of a shift in which few rare positives
a model happens to rank first to move pr_auc by triple digits, and the decision policy
(which cares about aggregate dollar value, not rank order alone) is far less sensitive
to that same handful of cases. The two metrics are measuring genuinely different things
here, and on a proxy label already this noisy, neither is stable enough to declare a
winner.

### CS3 — Customer Value: **baseline preferable** (the clearest, most mechanistic finding in this benchmark)

This is the one real-data case study where the evidence is not just "noisy, no
verdict" — it's a specific, diagnosable failure mode, visible directly in the
predictions. The ensemble's raw regression accuracy is *better* than the baseline's
(MAE 1.91 vs. 2.19, a genuine +12.6% improvement) — if this were reported as only an ML
metric comparison, the ensemble would look like a clear winner. But look at what the
ensemble actually predicts: its outputs range from **0.47 to 1.00** with a standard
deviation of 0.14, compared to the baseline's range of −11.6 to 55.8 (std 1.35) against
a true target that itself ranges up to $1,237. The policy's top action,
`prioritize_high_value`, requires a predicted value ≥ $1.43 — a threshold the ensemble's
predictions **never once cross** in the entire 29,477-row test set (confirmed directly:
its action distribution is 100% `no_action`/`standard_nurture`, zero
`prioritize_high_value`, versus the baseline recommending `prioritize_high_value` for
3,380 customers). **Genuine hypothesis, grounded in the actual prediction values, not
speculation:** averaging (weighted-blending) several under-tuned regression candidates
compresses the ensemble's output toward the population mean — which mechanically lowers
average error on a target that is mostly near zero (a free win on MAE), but destroys
exactly the tail differentiation the business decision needs to identify which
customers are actually high-value. Better on the metric it was scored on; structurally
incapable of making the one decision this case study exists to drive. This is a real
architectural finding about ensembling regression outputs without tuning, not a
one-off fluke — it is visible in the raw prediction distribution, not just the
aggregate score.

---

## Synthetic / controlled findings (CS4 Lead Conversion, CS5 Upsell/Cross-sell, CS6 Campaign Response)

These three use documented synthetic generators (`benchmarks/case_studies/synthetic_generators.py`)
with a known true generating probability that is never fed to either model — chosen
because Olist has no lead-funnel, subscription-upsell, or marketing-campaign data to
support these targets defensibly (per the build brief's own instruction: where real
data can't defensibly support a target, build a controlled synthetic benchmark and say
so). Unlike CS1-3, class balance here is reasonable (44-56% positive rate) — the
challenge in this group is small absolute sample size (4,000 total rows, 600-row test
sets, and the smallest walk-forward fold trains on only 1,120 rows), not rarity.

### CS4 — Lead Conversion: **no meaningful difference**

Step 3's decision-impact lift was small to begin with (−2.2%), and Step 5 confirms it
isn't even a stable small effect: fold lifts of −7.8%, +9.1%, −26.1%, −2.2% flip sign
twice. The calibration-against-truth comparison (Step 4) — the cleanest test available,
since it compares each model directly to the *true* generating probability rather than
noisy labels — shows the baseline is measurably better calibrated (Brier against truth
0.0103 vs. the ensemble's 0.0118, a −15.1% relative difference, past the stated ±5%
"meaningful" bar). **Genuine hypothesis:** on a controlled, well-specified logistic
relationship with clean features, a single well-tuned model is fully sufficient to
recover it; searching across untuned candidates and blending them adds noise rather
than signal when there was never a nonlinear or heterogeneous relationship for an
ensemble to help find in the first place.

### CS5 — Upsell / Cross-sell: **baseline preferable**

Decision-impact lift was small on the single Step 3 split (−2.5%) but Step 5's folds
show a consistent, non-trivial baseline advantage that Step 3 undersold: −23.1%,
−11.2%, −27.4%, −2.5% — every fold favors the baseline, mean −16.1%, clearing the −10%
threshold. Calibration against true probability shows no meaningful difference (+1.3%,
within the ±5% band) — so this is not a calibration failure, it is specifically a
decision-policy-execution gap. **Genuine hypothesis:** with the same "no HPO" ensemble
disadvantage as every other case study, plus a modest dataset (4,000 rows total), an
untuned multi-candidate search has fewer effective observations per candidate to work
with than one model gets when all the data goes toward tuning it — the tuning budget
matters more, relatively, when there isn't much data to begin with.

### CS6 — Campaign Response: **no meaningful difference**

The smallest effect size in the whole benchmark: Step 3 lift −4.1%, fold lifts +1.3%,
+5.4%, −0.9%, −4.1% — genuinely close to zero and inconsistent in sign, a textbook "no
difference" result rather than a disguised win or loss for either side. Calibration
against truth does favor the baseline outright (−14.4%, past the meaningful bar),
consistent with the same "no HPO, added ensembling noise on an already-clean synthetic
relationship" pattern as CS4.

---

## Cross-cutting explanation (why this happened, stated plainly)

1. **The engine's candidate search runs with zero hyperparameter tuning today**
   (`max_hyperparameter_trials: int = 0`, `apps/api/app/engine/types.py`), while the
   baseline in every one of these six comparisons received a genuine 40-trial random
   search (`benchmarks/baseline_runner.py`). This is the single largest, most
   consistent explanatory factor across all six case studies. It does not, by itself,
   mean the ensemble architecture is a bad idea — it means this benchmark is honestly
   reporting what happens when an architecture that hasn't been given its own tuning
   budget is compared against one that has.
2. **Extreme class rarity dominates CS1 and CS2** (positive rates under 0.6%), making
   both models' comparative "advantage" highly sensitive to a small number of rare
   positive cases — this shows up as large, sign-flipping variance across folds and
   segments in both directions, not a consistent story favoring either model.
3. **CS3 shows a specific, mechanistic failure mode**: blending several under-tuned
   regression candidates compresses predictions toward the population mean, which
   improves average error (MAE) on a near-zero-inflated target while destroying the
   tail differentiation the actual business decision (identify the top ~10% of
   customers) requires. This is visible directly in the raw predicted-value
   distributions, not inferred.
4. **CS4-CS6's smaller, cleaner synthetic datasets show the same "no HPO" pattern
   without the rare-event noise of CS1/CS2** — a single tuned model reliably matches or
   beats the untuned ensemble on a well-specified relationship, most clearly in the
   calibration-against-true-probability comparison, which is not affected by label
   noise the way the observed-outcome comparisons in CS1-3 are.

No claim above is stronger than the numbers in `reports/case_study_scorecard.md` and
the underlying artifacts in `artifacts/case_studies/*/` support. Where a result was
noisy or inconclusive, it is reported as "no meaningful difference," not rounded up or
down to a more decisive-sounding story.
