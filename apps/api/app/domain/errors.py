"""Domain errors. HTTP mapping lives in the API adapters, not here."""


class OpportunityNotFoundError(LookupError):
    """No opportunity matches the given id or external_id."""


class DecisionNotFoundError(LookupError):
    """No decision matches the given id."""


class InvalidGenerateRequestError(ValueError):
    """Generate was called without opportunity_id or generate_all."""
