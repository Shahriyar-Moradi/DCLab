# Experimentation

An experiment is: dataset version + task spec + search config + seed.

Lifecycle: CREATED → QUEUED → PROFILING → FEATURE_ENGINEERING → GENERATING_CANDIDATES → TRAINING → EVALUATING → FILTERING → SELECTING → ENSEMBLING → REPORTING → COMPLETED | FAILED.

One candidate failure is recorded (`FAILED`) and skipped. The run completes if any valid models remain.

Search is **not** “train hundreds of random models”. Default strategy is progressive with caps (`max_candidates`, `max_feature_group_combinations`). Ensemble is kept only if it beats the best single model on **validation**. Test is scored once.

```bash
dclab experiment run --dataset synthetic --task purchase_prediction
dclab experiment report --id <uuid>
```
