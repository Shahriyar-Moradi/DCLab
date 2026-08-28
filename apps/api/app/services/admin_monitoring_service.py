"""Step 6 — Admin Monitoring: retrains, metric deltas, and dataset-sync health.

Metric deltas are computed from real, consecutive retrains of the same task or
simulation use case — never invented. Drift detection has no implementation in
this build (nothing in app.ml/app.engine computes it), so it is reported as
absent rather than faked with a placeholder number.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientLabRunAudit, Dataset, DatasetProfile, Experiment, PredictionTask, SimulationRun
from app.domain.admin_monitoring import DatasetHealth, MetricDelta, MonitoringOverview, RetrainEvent

METRIC_KEYS = ("roc_auc", "pr_auc")

DRIFT_NOTE = (
    "Drift detection is not implemented in this build. The retrain history and "
    "metric deltas below are the monitoring signal currently available; dataset "
    "row/column counts and last-profiled time are shown as a proxy for data-sync "
    "health."
)


def _metric_deltas(current: dict, previous: dict | None) -> dict[str, MetricDelta]:
    if not previous:
        return {}
    deltas: dict[str, MetricDelta] = {}
    for key in METRIC_KEYS:
        current_value = current.get(key)
        previous_value = previous.get(key)
        if isinstance(current_value, (int, float)) and isinstance(previous_value, (int, float)):
            deltas[key] = MetricDelta(
                previous=round(float(previous_value), 4),
                current=round(float(current_value), 4),
                delta=round(float(current_value) - float(previous_value), 4),
            )
    return deltas


def list_retrain_events(db: Session) -> list[RetrainEvent]:
    events: list[RetrainEvent] = []

    experiments = db.scalars(
        select(Experiment)
        .where(Experiment.status == "COMPLETED")
        .order_by(Experiment.task_id, Experiment.created_at)
    ).all()
    task_names = {task.id: task.name for task in db.scalars(select(PredictionTask)).all()}
    previous_by_task: dict = {}
    for exp in experiments:
        metrics = (exp.result or {}).get("test_metrics") or {}
        events.append(
            RetrainEvent(
                id=exp.id,
                source="experiment",
                name=task_names.get(exp.task_id, str(exp.task_id)),
                status=exp.status,
                metrics=metrics,
                metric_deltas=_metric_deltas(metrics, previous_by_task.get(exp.task_id)),
                created_at=exp.created_at,
            )
        )
        previous_by_task[exp.task_id] = metrics

    # Both admin-run simulations and client-triggered Labs trials (Step 7) retrain
    # the same eight use cases via the same engine call — merged into one
    # chronological-per-use-case delta series regardless of who triggered the
    # run, since it's the same underlying retrain event either way.
    use_case_events: list[tuple[str, RetrainEvent]] = []
    for run in db.scalars(select(SimulationRun)).all():
        metrics = (run.payload or {}).get("metrics") or {}
        use_case_events.append(
            (
                run.use_case,
                RetrainEvent(
                    id=run.id,
                    source="simulation",
                    name=run.use_case,
                    status="COMPLETED",
                    metrics=metrics,
                    metric_deltas={},
                    created_at=run.created_at,
                ),
            )
        )
    for audit in db.scalars(select(ClientLabRunAudit)).all():
        metrics = (audit.payload or {}).get("metrics") or {}
        use_case_events.append(
            (
                audit.use_case,
                RetrainEvent(
                    id=audit.id,
                    source="client_trial",
                    name=audit.use_case,
                    status="COMPLETED",
                    metrics=metrics,
                    metric_deltas={},
                    created_at=audit.created_at,
                    client_lab_run_id=audit.client_lab_run_id,
                ),
            )
        )
    use_case_events.sort(key=lambda pair: pair[1].created_at)
    previous_by_use_case: dict = {}
    for use_case, event in use_case_events:
        event.metric_deltas = _metric_deltas(event.metrics, previous_by_use_case.get(use_case))
        previous_by_use_case[use_case] = event.metrics
        events.append(event)

    events.sort(key=lambda event: event.created_at, reverse=True)
    return events


def list_dataset_health(db: Session) -> list[DatasetHealth]:
    datasets = db.scalars(select(Dataset).order_by(Dataset.created_at.desc())).all()
    results: list[DatasetHealth] = []
    for dataset in datasets:
        latest_profile = db.scalars(
            select(DatasetProfile)
            .where(DatasetProfile.dataset_id == dataset.id)
            .order_by(DatasetProfile.created_at.desc())
        ).first()
        if dataset.row_count <= 0:
            status = "empty"
        elif latest_profile is None:
            status = "not_profiled"
        else:
            status = "healthy"
        results.append(
            DatasetHealth(
                id=dataset.id,
                name=dataset.name,
                row_count=dataset.row_count,
                column_count=dataset.column_count,
                last_profiled_at=latest_profile.created_at if latest_profile else None,
                status=status,
            )
        )
    return results


def get_monitoring_overview(db: Session) -> MonitoringOverview:
    return MonitoringOverview(
        retrain_events=list_retrain_events(db),
        dataset_health=list_dataset_health(db),
        drift_detection_note=DRIFT_NOTE,
    )
