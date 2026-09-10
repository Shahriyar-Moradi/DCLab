"""Frontend Zod Labs schemas must stay aligned with the Pydantic /app models."""

from uuid import UUID

from scripts.check_client_lab_schema_contract import (
    SCHEMA_FILE,
    _type_compatible,
    collect_mismatches,
    extract_z_object_fields,
)

CLIENT_LAB_UUID_FIELDS = ("id", "run_id", "pipeline_run_id", "dataset_id")


def test_client_lab_zod_fields_exist_on_pydantic_with_compatible_types():
    mismatches = collect_mismatches()
    assert mismatches == [], "\n".join(mismatches)


def test_python_uuid_is_compatible_with_zod_uuid():
    assert _type_compatible(UUID, "z.uuid()")
    assert _type_compatible(UUID, "z.uuid().nullable()")
    assert _type_compatible(UUID | None, "z.uuid().nullable()")
    assert not _type_compatible(UUID, "z.number()")
    assert not _type_compatible(UUID, "z.guid()")


def test_client_lab_upload_uuid_ids_use_z_uuid():
    fields = extract_z_object_fields(SCHEMA_FILE.read_text(encoding="utf-8"), "ClientLabUploadSchema")
    for name in CLIENT_LAB_UUID_FIELDS:
        expr = fields[name].replace(" ", "")
        assert "z.uuid(" in expr, f"{name} must use z.uuid(): {fields[name]}"
        assert "z.guid(" not in expr, f"{name} must not use z.guid(): {fields[name]}"


def test_client_lab_upload_workspace_id_uses_z_guid():
    fields = extract_z_object_fields(SCHEMA_FILE.read_text(encoding="utf-8"), "ClientLabUploadSchema")
    expr = fields["workspace_id"].replace(" ", "")
    assert "z.guid(" in expr, fields["workspace_id"]
    assert "z.uuid(" not in expr, fields["workspace_id"]
