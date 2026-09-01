"""Frontend Zod Labs schemas must stay aligned with the Pydantic /app models."""

from scripts.check_client_lab_schema_contract import collect_mismatches


def test_client_lab_zod_fields_exist_on_pydantic_with_compatible_types():
    mismatches = collect_mismatches()
    assert mismatches == [], "\n".join(mismatches)
