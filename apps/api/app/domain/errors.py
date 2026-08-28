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
