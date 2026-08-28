"""Business Translation Layer.

Everything the DCLab ML engine produces — predictions, feature importances, model
metadata, experiment results — passes through here before it can reach a
client-facing surface. Nothing downstream of this package should ever see a raw
model name, a probability float presented as fact, a candidate/ensemble count, or
any other internal-engine vocabulary. See `banned_terms.py` for the enforced list
and `docs/ACCESS_MODEL.md` for the architecture this protects.
"""

from app.translation.models import ClientFacingInsight, ConfidenceBand, InsightCategory

__all__ = ["ClientFacingInsight", "ConfidenceBand", "InsightCategory"]
