"""Business Translation Layer.

Everything the DCLab ML engine produces — predictions, feature importances, model
metadata, experiment results — passes through here before it can reach a
legacy business-decision / client surface. Developer workbench and platform/admin
UI may show the technical evidence directly. See `banned_terms.py` for the client
word list, `surfaces.py` for audience classification, and `docs/ACCESS_MODEL.md`
for the architecture this protects.
"""

from app.translation.models import ClientFacingInsight, ConfidenceBand, InsightCategory

__all__ = ["ClientFacingInsight", "ConfidenceBand", "InsightCategory"]
