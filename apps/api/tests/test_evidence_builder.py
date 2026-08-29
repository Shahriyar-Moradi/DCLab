"""Column-evidence builder: missingness, correlation, and co-occurrence.

The Telco fixture has TotalCharges missing exactly where tenure == 0 — the
pattern the crosstab check is meant to surface.
"""

from __future__ import annotations

import pandas as pd

from app.engine.lab.evidence import build_column_evidence


def _telco_fixture() -> pd.DataFrame:
    """Deterministic Telco-shaped rows: new customers (tenure 0) have no bill yet."""
    return pd.DataFrame(
        {
            "customer_id": [f"C{i:03d}" for i in range(12)],
            "tenure": [0, 0, 0, 1, 5, 12, 24, 36, 48, 60, 72, 8],
            "MonthlyCharges": [29.85, 56.95, 53.85, 42.30, 70.70, 99.65, 89.10, 29.75, 104.80, 18.95, 103.70, 66.85],
            "TotalCharges": [None, None, None, 42.30, 353.50, 1195.80, 2138.40, 1071.00, 5036.30, 1137.00, 7466.40, 534.80],
            "Contract": [
                "Month-to-month",
                "One year",
                "Month-to-month",
                "One year",
                "Month-to-month",
                "Month-to-month",
                "Month-to-month",
                "Two year",
                "One year",
                "Two year",
                "Two year",
                "Month-to-month",
            ],
            "Churn": ["No", "No", "Yes", "No", "Yes", "Yes", "No", "No", "Yes", "No", "No", "No"],
        }
    )


def test_total_charges_missing_exactly_where_tenure_is_zero():
    frame = _telco_fixture()
    evidence = build_column_evidence(frame, "TotalCharges", target="Churn")

    assert evidence.column == "TotalCharges"
    assert evidence.missing_count == 3
    assert evidence.missing_fraction == 3 / 12
    # Churn is not numeric, so correlation is not computed.
    assert evidence.correlation_with_target is None

    tenure_flags = [item for item in evidence.missingness_cooccurrence if item.other_column == "tenure"]
    assert tenure_flags, "expected a tenure co-occurrence flag"
    flag = tenure_flags[0]
    assert flag.other_value == 0
    assert flag.exact_match is True
    assert flag.missing_and_value_count == 3
    assert flag.rows_with_value == 3
    assert flag.fraction_of_missing == 1.0
    assert flag.fraction_of_value == 1.0

    assert 1 <= len(evidence.sample_rows) <= 5
    for row in evidence.sample_rows:
        assert set(row) <= {"TotalCharges", "Churn", "tenure"}
        assert "TotalCharges" in row
        assert row["TotalCharges"] is None
        assert row.get("tenure") == 0


def test_same_frame_always_produces_identical_evidence():
    frame = _telco_fixture()
    first = build_column_evidence(frame, "TotalCharges", target="Churn")
    second = build_column_evidence(frame, "TotalCharges", target="Churn")
    assert first == second
    # The input frame is not mutated.
    assert frame["TotalCharges"].isna().sum() == 3


def test_blank_string_total_charges_counts_as_missing():
    # Real Telco files store the unbilled rows as `" "`, not pandas NaN.
    frame = _telco_fixture().copy()
    charges = frame["TotalCharges"].astype(object)
    charges.loc[frame["tenure"] == 0] = " "
    frame["TotalCharges"] = charges
    evidence = build_column_evidence(frame, "TotalCharges")
    assert evidence.missing_count == 3
    flag = next(item for item in evidence.missingness_cooccurrence if item.other_column == "tenure")
    assert flag.other_value == 0
    assert flag.exact_match is True


def test_numeric_target_yields_correlation():
    frame = _telco_fixture()
    evidence = build_column_evidence(frame, "MonthlyCharges", target="tenure")
    assert evidence.missing_count == 0
    assert evidence.correlation_with_target is not None
    assert -1.0 <= evidence.correlation_with_target <= 1.0
    assert evidence.missingness_cooccurrence == []
    assert len(evidence.sample_rows) <= 5
    for row in evidence.sample_rows:
        assert set(row) == {"MonthlyCharges", "tenure"}
