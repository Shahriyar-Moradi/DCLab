"""Typed stages of the automatic training job (P1.5-A).

``app.services.auto_train_service.run_auto_train_job`` is a thin orchestrator
over these stages. Each stage is ``run_<stage>(ctx, <Stage>Input) ->
<Stage>Output``; shared run state (current stage, row counts, timings, trace)
lives on :class:`~app.services.auto_train.context.RunContext`. A stage that has
already recorded a terminal outcome (failure or needs-input) raises
:class:`~app.services.auto_train.context.StageHalt`.

This package never imports ``auto_train_service`` at module import time; the
names tests patch on that module (``_mark``, ``profile_frame``,
``_load_upload_frame``, ``materialize_client_upload``, ...) are resolved through
it at call time.
"""
