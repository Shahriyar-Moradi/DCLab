"""Progress stage keys the experiment runner reports (``progress(stage)``).

The engine owns these strings because it emits them; ``app.domain.lab_run_stages``
re-exports them next to the server-side stages (queued, ingesting, ...) it stores on
``client_lab_uploads.pipeline_status``. Pure constants: no app imports.
"""

from __future__ import annotations

SPLITTING = "splitting"
CROSS_VALIDATION = "cross_validation"
TRAINING = "training"
EVALUATING = "evaluating"
PREDICTING = "predicting"
