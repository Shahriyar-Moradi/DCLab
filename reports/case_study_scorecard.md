# DCLab Case Study Benchmark — Unified Scorecard

_Generated 2026-08-26 10:47 UTC_

One consolidated comparison across all 6 case studies: a single competitively-tuned baseline model (Step 1) vs. DCLab's full multi-model search + ensemble (Step 2), on ML metrics, actual business decisions (Step 3), calibration against ground truth where it exists (Step 4), and stability across segments and walk-forward folds (Step 5).

## Verdict methodology (stated thresholds, not a subjective read)

1. If the sign of the realized-value lift is **not consistent** across all 4 of Step 5's walk-forward folds, the verdict is **"no meaningful difference"** regardless of Step 3's single-split number — a result that flips depending on which time window you measure is not treated as a real advantage.
2. Otherwise, the verdict is driven by the **mean lift across the 4 folds** (the robust number, not the single Step 3 split): >= **+10%** -> "ensemble meaningfully better"; <= **-10%** -> "baseline preferable"; otherwise -> "no meaningful difference".

This rule is symmetric — nothing in the code favors one outcome — so all three verdicts are reachable; which one actually appears below is determined only by what the numbers show.

| Case Study | Data | ML Metric Δ | Decision-Impact Δ (Step 3, single split) | Fold-Mean Decision-Impact Δ (Step 5) | Fold Sign Consistent? | Calibration Δ (Brier vs. truth) | Segments Shrinking ≥10pp | Verdict |
|---|---|---|---|---|---|---|---|---|
| Purchase Prediction | olist | -13.8% (pr_auc) | +25.3% | +12.9% | no | n/a (real data, no ground truth) | 8 | **no meaningful difference** |
| Reactivation (Repeat-Purchase Proxy) *(proxy — see note)* | olist | +167.3% (pr_auc) | -59.7% | -23.2% | no | n/a (real data, no ground truth) | 5 | **no meaningful difference** |
| Customer Value | olist | +12.6% (mae) | -57.9% | -28.1% | yes | n/a (real data, no ground truth) | 8 | **baseline preferable** |
| Lead Conversion | synthetic | -1.4% (pr_auc) | -2.2% | -6.8% | no | -15.1% (baseline measurably better calibrated) | 0 | **no meaningful difference** |
| Upsell / Cross-sell Probability | synthetic | +0.7% (pr_auc) | -2.5% | -16.1% | yes | +1.3% (no meaningful calibration difference) | 0 | **baseline preferable** |
| Campaign Response | synthetic | +0.7% (pr_auc) | -4.1% | +0.4% | no | -14.4% (baseline measurably better calibrated) | 0 | **no meaningful difference** |

## Per-case-study detail

### Purchase Prediction (`purchase_prediction`)

- **Verdict:** no meaningful difference — fold-to-fold lift sign is not consistent across the 4 walk-forward folds (['-5.5%', '+3.2%', '+28.7%', '+25.3%']) — an advantage that flips sign depending on which window it's measured on is not treated as real, regardless of Step 3's single-split number
- **ML metric (pr_auc):** baseline=0.015482932975953342, engine=0.013351972544786046 (-13.8% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=+25.3%, agreement rate=21.8%, ensemble better in 0.4% of disagreements, baseline better in 0.1%
- **Robustness (Step 5):** fold lifts=['-5.5%', '+3.2%', '+28.7%', '+25.3%'], mean=+12.9%, std=14.4pp, sign consistent=False, 8 segment(s) shrink the aggregate lift by >=10pp

### Reactivation (Repeat-Purchase Proxy) (`reactivation`)

> **Honesty note:** Olist has no subscription/contract relationship, so there is no real churn event in this data. This case study is a repeat-purchase proxy: it scores already-dormant customers (no order in 90+ days) on whether they return within 60 days. Treat every result here as evidence about win-back targeting on a proxy label, not as a validated churn model.

- **Verdict:** no meaningful difference — fold-to-fold lift sign is not consistent across the 4 walk-forward folds (['+106.8%', '-63.2%', '-76.9%', '-59.7%']) — an advantage that flips sign depending on which window it's measured on is not treated as real, regardless of Step 3's single-split number
- **ML metric (pr_auc):** baseline=0.006400924131939632, engine=0.017107665367010298 (+167.3% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=-59.7%, agreement rate=77.4%, ensemble better in 0.1% of disagreements, baseline better in 0.3%
- **Robustness (Step 5):** fold lifts=['+106.8%', '-63.2%', '-76.9%', '-59.7%'], mean=-23.2%, std=75.4pp, sign consistent=False, 5 segment(s) shrink the aggregate lift by >=10pp

### Customer Value (`customer_value`)

- **Verdict:** baseline preferable — mean fold lift -28.1% <= -10% threshold, consistent sign across all 4 folds (['-10.2%', '-6.7%', '-37.6%', '-57.9%'])
- **ML metric (mae):** baseline=2.189771040145023, engine=1.914650926965363 (+12.6% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=-57.9%, agreement rate=49.8%, ensemble better in 0.1% of disagreements, baseline better in 0.8%
- **Robustness (Step 5):** fold lifts=['-10.2%', '-6.7%', '-37.6%', '-57.9%'], mean=-28.1%, std=21.0pp, sign consistent=True, 8 segment(s) shrink the aggregate lift by >=10pp

### Lead Conversion (`lead_conversion`)

- **Verdict:** no meaningful difference — fold-to-fold lift sign is not consistent across the 4 walk-forward folds (['-7.8%', '+9.1%', '-26.1%', '-2.2%']) — an advantage that flips sign depending on which window it's measured on is not treated as real, regardless of Step 3's single-split number
- **ML metric (pr_auc):** baseline=0.6344043288030773, engine=0.6257090189153875 (-1.4% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=-2.2%, agreement rate=89.2%, ensemble better in 43.1% of disagreements, baseline better in 56.9%
- **Robustness (Step 5):** fold lifts=['-7.8%', '+9.1%', '-26.1%', '-2.2%'], mean=-6.8%, std=12.7pp, sign consistent=False, 0 segment(s) shrink the aggregate lift by >=10pp
- **Calibration vs. true probability (Step 4):** -15.1% relative Brier improvement from ensemble — baseline measurably better calibrated

### Upsell / Cross-sell Probability (`upsell_crosssell`)

- **Verdict:** baseline preferable — mean fold lift -16.1% <= -10% threshold, consistent sign across all 4 folds (['-23.1%', '-11.2%', '-27.4%', '-2.5%'])
- **ML metric (pr_auc):** baseline=0.6493817338530898, engine=0.6540586358961153 (+0.7% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=-2.5%, agreement rate=89.3%, ensemble better in 45.3% of disagreements, baseline better in 54.7%
- **Robustness (Step 5):** fold lifts=['-23.1%', '-11.2%', '-27.4%', '-2.5%'], mean=-16.1%, std=9.8pp, sign consistent=True, 0 segment(s) shrink the aggregate lift by >=10pp
- **Calibration vs. true probability (Step 4):** +1.3% relative Brier improvement from ensemble — no meaningful calibration difference

### Campaign Response (`campaign_response`)

- **Verdict:** no meaningful difference — fold-to-fold lift sign is not consistent across the 4 walk-forward folds (['+1.3%', '+5.4%', '-0.9%', '-4.1%']) — an advantage that flips sign depending on which window it's measured on is not treated as real, regardless of Step 3's single-split number
- **ML metric (pr_auc):** baseline=0.7019667634368758, engine=0.7067140142982321 (+0.7% relative, higher-is-better primary score basis)
- **Decision impact (Step 3, single split):** lift=-4.1%, agreement rate=80.8%, ensemble better in 47.8% of disagreements, baseline better in 52.2%
- **Robustness (Step 5):** fold lifts=['+1.3%', '+5.4%', '-0.9%', '-4.1%'], mean=+0.4%, std=3.5pp, sign consistent=False, 0 segment(s) shrink the aggregate lift by >=10pp
- **Calibration vs. true probability (Step 4):** -14.4% relative Brier improvement from ensemble — baseline measurably better calibrated

## Summary

- **ensemble meaningfully better:** 0 of 6 case studies
- **no meaningful difference:** 4 of 6 case studies
- **baseline preferable:** 2 of 6 case studies

