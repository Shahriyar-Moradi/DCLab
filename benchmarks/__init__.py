"""Case study benchmark harness.

Sits on top of the DCLab experimentation engine (``app.engine.*``) and answers
one question: does the multi-model search + ensemble architecture produce
better business decisions than a single, competitively-tuned baseline model —
honestly, case by case, including when the answer is "no."

This package never reimplements engine internals (candidate search, leakage
detection, validation splits, ensembling) — it calls them, repeatedly, across
a fixed battery of case studies defined in ``configs/case_studies/*.yaml``.
"""
