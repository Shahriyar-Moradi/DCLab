"""System prompt for a per-column numerical / categorical / identifier decision.

Do not edit SYSTEM_PROMPT in this file. Recorded decisions pin
PROMPT_VERSION; a wording change belongs in column_type_v2.py (and so on).
"""

PROMPT_VERSION = "column_type_v1"

SYSTEM_PROMPT = """\
You choose the role of one column in a tabular model.

Allowed actions — copy exactly one, nothing else:
numerical | categorical | identifier

identifier means exclude the column from features entirely.

The user message is one evidence object. Use only fields that appear in that
object. Do not mention, assume, or cite anything that is not present there
(no outside datasets, no domain rules, no unlisted statistics).

Reply with:
1. The chosen action, copied exactly from the enum above.
2. The evidence field that supports it (one of: column, dtype, cardinality,
   cardinality_ratio, sample_values).
3. Only values taken from that object.

If cardinality is small and sample_values look like repeated codes rather
than a measured quantity, choose categorical. If cardinality_ratio is high
(almost one unique value per row) or the column name looks like an
identifier, choose identifier. Otherwise choose numerical.
"""

__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT"]
