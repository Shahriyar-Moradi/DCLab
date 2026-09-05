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


class ProblemSpecNotFoundError(LookupError):
    """No problem spec matches the given workspace-scoped id."""


class ArtifactNotFoundError(LookupError):
    """No artifact matches the given workspace-scoped id."""


class DataSourceNotFoundError(LookupError):
    """No data source matches the given workspace-scoped id."""


class DataSourceConfigurationError(ValueError):
    """DataSource.configuration contained a secret or an invalid source_type."""


class IngestionRunNotFoundError(LookupError):
    """No ingestion run matches the given workspace-scoped id."""


class WorkflowVersionNotFoundError(LookupError):
    """No workflow version matches the given workspace-scoped id."""


class PipelineDefinitionNotFoundError(LookupError):
    """No pipeline definition matches the given workspace-scoped id."""


class PipelineVersionNotFoundError(LookupError):
    """No pipeline version matches the given workspace-scoped id."""
