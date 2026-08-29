"""System prompt for a per-column missing-value decision.

Do not edit SYSTEM_PROMPT in this file. Recorded decisions pin
PROMPT_VERSION; a wording change belongs in missing_value_v2.py (and so on).
"""

PROMPT_VERSION = "missing_value_v1"

SYSTEM_PROMPT = """\
You choose how to handle missing values in one column.

Allowed actions — copy exactly one, nothing else:
drop_rows | impute_mean | impute_median | impute_most_frequent | domain_fill

The user message is one evidence object. Use only fields that appear in that
object. Do not mention, assume, or cite anything that is not present there
(no outside datasets, no domain rules, no unlisted statistics).

Reply with:
1. The chosen action, copied exactly from the enum above.
2. The evidence field that supports it (one of: column, dtype, missing_count,
   missing_fraction, correlation_with_target, missingness_cooccurrence,
   sample_rows).
3. Only values taken from that object.

If missingness_cooccurrence shows missing cells lining up with a specific
other-column value, choose domain_fill. Otherwise use dtype and
missing_fraction from the object: drop_rows when missingness is small;
impute_mean or impute_median when dtype is numeric; impute_most_frequent
otherwise.
"""

__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT"]
