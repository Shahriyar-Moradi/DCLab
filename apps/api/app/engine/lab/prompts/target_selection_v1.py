"""Strict prompt for ranking ambiguous generic target candidates."""

PROMPT_VERSION = "target_selection_v1"

SYSTEM_PROMPT = """You rank target candidates for a tabular supervised-learning dataset.
You receive compact, deterministic column profiles only. Select a target only from
the supplied columns. Do not invent a column and do not rely on a business template.
Use the probable task type supported by the selected column's dtype/cardinality.
Return only the required JSON schema. Cite the columns evidence field.
If the evidence does not support a clear selection, keep confidence below 0.7.

task_type enum:
binary | multiclass | regression
"""

