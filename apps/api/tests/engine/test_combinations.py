from app.engine.features.combinations import generate_group_combinations, non_empty_subsets
from app.engine.search.fingerprint import candidate_fingerprint


def test_seven_groups_have_127_non_empty_subsets():
    groups = ["a", "b", "c", "d", "e", "f", "g"]
    assert len(non_empty_subsets(groups)) == 127


def test_limited_search_respects_cap():
    combos = generate_group_combinations(
        ["customer", "transaction", "temporal", "reviews"],
        strategy="limited",
        max_combinations=5,
    )
    assert len(combos) == 5
    assert all(len(c) >= 1 for c in combos)


def test_fingerprint_is_stable():
    payload = {"task_id": "x", "features": ["a", "b"], "family": "logistic_regression", "seed": 42}
    assert candidate_fingerprint(payload) == candidate_fingerprint(payload)
    assert candidate_fingerprint(payload) != candidate_fingerprint({**payload, "seed": 1})
