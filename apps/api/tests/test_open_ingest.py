"""Open ingest: any usual data file, no required field names."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from app.engine.lab.open_ingest import MAX_RECORDS, MAX_UPLOAD_BYTES, OpenIngestError, preview_upload


def test_csv_with_arbitrary_columns_is_accepted():
    preview = preview_upload("events.csv", b"widget,count\ngizmo,3\n")
    assert preview.kind == "spreadsheet"
    assert preview.record_count == 1
    assert preview.has_named_fields is True
    assert preview.fields_noticed == ["widget", "count"]


def test_headerless_log_is_accepted_as_plain_text():
    data = (
        b"2026-08-27T10:00:00 INFO started request id=abc\n"
        b"2026-08-27T10:00:01 WARN timeout for request id=abc\n"
    )
    preview = preview_upload("access.log", data)
    assert preview.kind == "plain_text"
    assert preview.record_count == 2
    assert preview.has_named_fields is False
    assert preview.fields_noticed == []


def test_json_records_with_arbitrary_keys_are_accepted():
    preview = preview_upload("dump.json", b'[{"sku": "A-1", "qty": 2}, {"sku": "B-9", "qty": 1}]\n')
    assert preview.kind == "json"
    assert preview.record_count == 2
    assert "sku" in preview.fields_noticed
    assert "qty" in preview.fields_noticed


def test_parquet_is_accepted():
    buffer = io.BytesIO()
    pd.DataFrame({"account": ["acme"], "region": ["uae"]}).to_parquet(buffer, index=False)
    preview = preview_upload("accounts.parquet", buffer.getvalue())
    assert preview.kind == "table_file"
    assert preview.record_count == 1
    assert preview.fields_noticed == ["account", "region"]


def test_empty_file_is_rejected():
    with pytest.raises(OpenIngestError, match="empty"):
        preview_upload("blank.csv", b"   \n")


def test_oversized_file_is_rejected():
    with pytest.raises(OpenIngestError, match="MB"):
        preview_upload("huge.csv", b"x" * (MAX_UPLOAD_BYTES + 1))


def test_unsupported_type_is_rejected():
    with pytest.raises(OpenIngestError, match="not supported"):
        preview_upload("photo.png", b"\x89PNG\r\n" + b"x" * 20)


def test_too_many_rows_are_rejected():
    lines = ["a,b"] + [f"{i},x" for i in range(MAX_RECORDS + 1)]
    with pytest.raises(OpenIngestError, match=str(MAX_RECORDS)):
        preview_upload("wide.csv", "\n".join(lines).encode())
