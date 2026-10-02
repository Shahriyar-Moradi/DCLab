"""Domain errors. HTTP mapping lives in the API adapters, not here."""


class OpportunityNotFoundError(LookupError):
    """No opportunity matches the given id or external_id."""


class DecisionNotFoundError(LookupError):
    """No decision matches the given id."""


class InvalidGenerateRequestError(ValueError):
    """Generate was called without opportunity_id or generate_all."""


class UnknownLabProblemError(ValueError):
    """Client Labs only offers a fixed catalog of problems — not this one."""


class TrialDatasetTooLargeError(ValueError):
    """An uploaded trial file exceeds the fixed row-count bound."""


class TrialDatasetColumnsError(ValueError):
    """An uploaded trial file is missing columns the chosen problem needs."""


class TrialQuotaExceededError(ValueError):
    """This workspace already used its bounded number of trial runs for this problem."""


class UnknownLabCategoryError(ValueError):
    """The open-ingest box is scoped to a business category, not a free-form label."""


class OpenLabFileError(ValueError):
    """The uploaded Labs file could not be taken in (empty, too large, or unreadable)."""


class IdentityError(Exception):
    """Workspace identity, membership, or entitlement rule was violated."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProjectNotFoundError(LookupError):
    """No project matches the given workspace-scoped id."""


class GraphNodeNotFoundError(LookupError):
    """No project-graph node of this kind matches the workspace-scoped id."""


class InvalidCursorError(ValueError):
    """A /v1 page cursor is malformed, tampered with, or minted for another route/scope. Maps to 400."""

    code = "invalid_cursor"


class InvalidGraphCursorError(InvalidCursorError):
    """A project-graph page cursor is malformed."""


class SimulationRunNotFoundError(LookupError):
    """No simulation run matches the given workspace-scoped id."""


class ProblemSpecNotFoundError(LookupError):
    """No problem spec matches the given workspace-scoped id."""


class ArtifactNotFoundError(LookupError):
    """No artifact matches the given workspace-scoped id."""


class DataSourceNotFoundError(LookupError):
    """No data source matches the given workspace-scoped id."""


class DataSourceConfigurationError(ValueError):
    """DataSource.configuration contained a secret or an invalid source_type."""


class DataAccessNotFoundError(LookupError):
    """No data access matches the given workspace-scoped id."""


class DataAccessConfigurationError(ValueError):
    """DataAccess locator, credential pointer, or vocabulary is invalid."""


class DataAccessEventSpecError(ValueError):
    """data_access_events payload stored raw rows, secrets, or invalid vocabulary."""


class MlJobSpecError(ValueError):
    """ml_jobs payload stored raw rows, secrets, or an invalid slug/handler."""


class UnknownJobHandlerError(LookupError):
    """No worker handler is registered for this handler_key."""


class VisualizationSpecError(ValueError):
    """visualizations spec stored bulk series, secrets, or an invalid slug."""


class IngestionRunNotFoundError(LookupError):
    """No ingestion run matches the given workspace-scoped id."""


class WorkflowVersionNotFoundError(LookupError):
    """No workflow version matches the given workspace-scoped id."""


class PipelineDefinitionNotFoundError(LookupError):
    """No pipeline definition matches the given workspace-scoped id."""


class PipelineVersionNotFoundError(LookupError):
    """No pipeline version matches the given workspace-scoped id."""


class TargetIntentConflictError(Exception):
    """Request target disagrees with canonical ProblemSpec target (or another request)."""

    def __init__(
        self,
        *,
        problem_spec_target: str | None,
        requested_target: str,
        conflicting_target: str | None = None,
    ) -> None:
        super().__init__("request target conflicts with the canonical target intent")
        self.status_code = 409
        self.code = "TARGET_INTENT_CONFLICT"
        self.problem_spec_target = problem_spec_target
        self.requested_target = requested_target
        self.conflicting_target = conflicting_target

    def public_detail(self) -> dict[str, str]:
        detail = {
            "code": self.code,
            "message": str(self),
            "requested_target": self.requested_target,
        }
        if self.problem_spec_target is not None:
            detail["problem_spec_target"] = self.problem_spec_target
        if self.conflicting_target is not None:
            detail["conflicting_target"] = self.conflicting_target
        return detail


class ExecutionNotWaitingError(Exception):
    """Confirmation was submitted but this execution is not waiting for input."""

    def __init__(self, status: str) -> None:
        super().__init__("execution is not waiting for target confirmation")
        self.status_code = 409
        self.code = "EXECUTION_NOT_WAITING"
        self.current_status = status

    def public_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "status": self.current_status,
        }


class TargetNotInDatasetError(ValueError):
    """Declared target is not a column on the selected DatasetVersion schema."""

    def __init__(self, column: str) -> None:
        super().__init__(f"target {column!r} is not present in the dataset schema")
        self.status_code = 422
        self.code = "TARGET_NOT_IN_DATASET"
        self.column = column

    def public_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "target_column": self.column,
        }


class ScientificEvidenceLockedError(Exception):
    """This PipelineRun's canonical scientific evidence is frozen.

    Re-running would rewrite CV folds, hyperparameters, the winner decision, or
    the final holdout. PostgreSQL rejects that too; this is the readable form.
    """

    def __init__(self, pipeline_run_id) -> None:
        super().__init__(
            f"scientific evidence for pipeline run {pipeline_run_id} is locked"
        )
        self.pipeline_run_id = pipeline_run_id
        self.status_code = 409


class SplitPlanLineageError(ValueError):
    """A run references a SplitPlan of another source dataset, project or tenant (ADR 0006 §3)."""

    def __init__(self, message: str, *, code: str = "split_plan_dataset_mismatch") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class ExperimentNotFoundError(LookupError):
    """No experiment matches the workspace-scoped id (also: no ML-write access). Maps to 404."""


class InvalidChangeSetError(ValueError):
    """A branch change set is rejected (ADR 0006 §4). Stable code ``invalid_change_set``.

    ``reason`` is a stable sub-code (e.g. ``unknown_family``) and ``path`` points at
    the offending change (``changes[0].family``).
    """

    code = "invalid_change_set"
    status_code = 422

    def __init__(self, reason: str, message: str, *, path: str = "changes") -> None:
        super().__init__(f"{self.code}: {reason}: {message}")
        self.reason = reason
        self.path = path
        self.detail_message = message

    def public_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "reason": self.reason,
            "path": self.path,
            "message": self.detail_message,
        }


class ExperimentNotBranchableError(Exception):
    """The parent cannot be branched (not completed/locked, no split plan, ...). Maps to 409."""

    status_code = 409

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail_message = message

    def public_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.detail_message}


class ExperimentComparisonError(ValueError):
    """Two experiments are not comparable (different SplitPlan or missing evidence)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class ExperimentRequestError(Exception):
    """A root run cannot start on this dataset (P3.1-B2): stable ``code``, 409/422."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status_code = status_code
        self.detail_message = message

    def public_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.detail_message}


class RunQuotaExceededError(Exception):
    """The workspace already holds its maximum of queued/running runs (P3.1-B2). 429."""

    status_code = 429
    code = "run_quota_exceeded"

    def __init__(self, limit: int) -> None:
        super().__init__(f"this workspace already has {limit} queued or running runs; retry later")
        self.limit = limit

    def public_detail(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), "limit": self.limit}


class ExperimentNotCancellableError(Exception):
    """Only a queued or running run can be cancelled (P3.1-B2). Maps to 409."""

    status_code = 409
    code = "not_cancellable"

    def __init__(self, status: str, message: str) -> None:
        super().__init__(f"{self.code}: {message}")
        self.status = status
        self.detail_message = message

    def public_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.detail_message, "status": self.status}


class RunCancelledError(BaseException):
    """The worker reached a checkpoint of a job whose cancellation was requested.

    A ``BaseException`` (like ``asyncio.CancelledError``) on purpose: the engine
    isolates per-candidate failures with ``except Exception``, which must never
    turn a cancellation into a failed candidate and keep training. Only
    ``ml_job_service.execute_job`` catches it.
    """


class DecisionRecordError(Exception):
    """Typed decision-record / ref-move failure with a stable ``code`` (ADR 0006 §2, §5)."""

    status_code = 422
    code = "invalid_decision_record"

    def __init__(self, reason: str, message: str, **extra: object) -> None:
        super().__init__(f"{self.code}: {reason}: {message}")
        self.reason = reason
        self.detail_message = message
        self.extra = extra

    def public_detail(self) -> dict[str, object]:
        return {"code": self.code, "reason": self.reason, "message": self.detail_message, **self.extra}


class DecisionRecordNotFoundError(LookupError):
    """No decision record in this project/workspace (also: another tenant's). Maps to 404."""


class InvalidDecisionRecordError(DecisionRecordError):
    """Bad subject, evidence ref, rationale, payload or ref target semantics. Maps to 422."""


class RefTargetNotFoundError(DecisionRecordError):
    """A ref target is unknown, in another project or another tenant. Maps to 404."""

    status_code = 404
    code = "ref_target_not_found"


class InvalidDecisionTransitionError(DecisionRecordError):
    """The state machine forbids this transition (double accept, accept of rejected...)."""

    status_code = 409
    code = "invalid_decision_transition"


class DecisionActorNotPermittedError(DecisionRecordError):
    """This actor kind may not perform the write (agents only propose). Maps to 403."""

    status_code = 403
    code = "decision_actor_not_permitted"


class RefVersionConflictError(DecisionRecordError):
    """Optimistic ref version mismatch (``expected_version`` is stale). Maps to 409."""

    status_code = 409
    code = "ref_version_conflict"


class ChampionSplitPlanMismatchError(DecisionRecordError):
    """A champion must share the current champion's ``split_plan_id`` (Rev 2). Maps to 409."""

    status_code = 409
    code = "champion_split_plan_mismatch"


class IdempotencyKeyConflictError(DecisionRecordError):
    """The idempotency key was already used for a different decision. Maps to 409."""

    status_code = 409
    code = "idempotency_key_conflict"


class InvalidDecisionQueryError(ValueError):
    """Malformed decision list filter or cursor. Maps to 400."""


class InvalidDecisionCursorError(InvalidDecisionQueryError, InvalidCursorError):
    """The decision list cursor is invalid or bound to another project/filter set. Maps to 400."""


class IdempotencyKeyReusedError(Exception):
    """An Idempotency-Key was replayed with a different request digest (P3.1-A). Maps to 409.

    Generic form of the per-resource digest binding: a key always names one
    request (route, path, body, principal); a different request under the same
    key is refused instead of returning or overwriting the earlier result.
    """

    status_code = 409
    code = "idempotency_key_conflict"

    def __init__(self, message: str = "this Idempotency-Key was used for a different request") -> None:
        super().__init__(message)


class ServiceTokenError(Exception):
    """A service-token lifecycle rule was violated (P3.2-A); ``code`` is stable."""

    def __init__(self, code: str, message: str, status_code: int = 400, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.extra = extra

    def public_detail(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), **self.extra}


class ServiceTokenNotFoundError(LookupError):
    """No visible service token with this id in the workspace (also: another tenant's). 404."""
