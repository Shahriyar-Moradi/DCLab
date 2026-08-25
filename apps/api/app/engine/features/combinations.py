"""Feature-group combination search. Not brute-force feature-level 2^n."""

from __future__ import annotations

import itertools
import random
from collections.abc import Sequence


def non_empty_subsets(groups: Sequence[str]) -> list[tuple[str, ...]]:
    names = list(groups)
    combos: list[tuple[str, ...]] = []
    for size in range(1, len(names) + 1):
        for combo in itertools.combinations(sorted(names), size):
            combos.append(combo)
    return combos


def generate_group_combinations(
    groups: Sequence[str],
    *,
    strategy: str = "limited",
    max_combinations: int = 32,
    seed: int = 42,
    priority: Sequence[str] | None = None,
) -> list[tuple[str, ...]]:
    """Return group combinations under a configurable cap.

    Strategies: exhaustive, limited (smallest first), sampled, priority.
    """
    all_combos = non_empty_subsets(groups)
    if strategy == "exhaustive":
        return all_combos[: max(1, max_combinations)] if max_combinations else all_combos
    if strategy == "sampled":
        rng = random.Random(seed)
        if len(all_combos) <= max_combinations:
            return all_combos
        singles = [c for c in all_combos if len(c) == 1]
        rest = [c for c in all_combos if len(c) > 1]
        rng.shuffle(rest)
        picked = singles + rest
        return picked[:max_combinations]
    if strategy == "priority":
        preferred = list(priority or groups)
        ranked = sorted(
            all_combos,
            key=lambda combo: (
                -sum(1 for name in combo if name in preferred),
                len(combo),
                combo,
            ),
        )
        return ranked[:max_combinations]
    # limited / default: prefer smaller combinations, then alphabetical
    ordered = sorted(all_combos, key=lambda combo: (len(combo), combo))
    return ordered[:max_combinations]


def features_for_groups(groups: dict[str, list[str]], names: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in groups:
            raise KeyError(f"Unknown feature group: {name}")
        for column in groups[name]:
            if column not in seen:
                ordered.append(column)
                seen.add(column)
    return ordered
