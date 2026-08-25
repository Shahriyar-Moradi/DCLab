# Feature groups

Groups are **named lists of columns** in YAML. The engine does not hardcode “the seven Olist groups”.

With N groups there are `2^N - 1` non-empty combinations. Generation strategies: `exhaustive`, `limited` (smallest first), `sampled`, `priority`. Caps apply.

Feature-level 2^n search is intentionally not implemented. Later strategies (mutual information, RFE) can plug into `app.engine.search`.
