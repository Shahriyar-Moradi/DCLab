"""Frontend Zod Labs schemas must stay aligned with the Pydantic /app models."""

from uuid import UUID

from scripts.check_client_lab_schema_contract import _type_compatible, collect_mismatches


def test_client_lab_zod_fields_exist_on_pydantic_with_compatible_types():
    mismatches = collect_mismatches()
    assert mismatches == [], "\n".join(mismatches)


def test_python_uuid_accepts_zod_guid_for_nil_version_workspace_ids():
    assert _type_compatible(UUID, "z.guid()")
    assert _type_compatible(UUID, "z.uuid()")
    assert not _type_compatible(UUID, "z.number()")
