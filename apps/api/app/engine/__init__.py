"""Generic tabular experimentation engine.

M1 opportunity conversion and the simulation pack call into this package.
Dataset adapters (Olist, synthetic) live under engine.datasets and are not
imported by core modules.
"""

from app.engine.types import Candidate, ExperimentStatus, SearchConfig, TaskSpec

__all__ = ["Candidate", "ExperimentStatus", "SearchConfig", "TaskSpec"]
