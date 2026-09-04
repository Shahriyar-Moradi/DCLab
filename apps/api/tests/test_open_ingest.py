"""Open ingest: any usual data file, no required field names."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from app.engine.lab.open_ingest import OpenIngestError, preview_upload, preview_upload_path


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


def test_legacy_row_count_ceiling_is_not_applied():
    preview = preview_upload("wide.csv", b"feature,target\n" + b"x,1\n" * 501)
    assert preview.record_count == 501


def test_unsupported_type_is_rejected():
    with pytest.raises(OpenIngestError, match="not supported"):
        preview_upload("photo.png", b"\x89PNG\r\n" + b"x" * 20)


def test_path_preview_streams_more_than_the_legacy_row_and_file_size_limits(tmp_path):
    path = tmp_path / "millionish.csv"
    path.write_bytes(b"a,b\n" + b"1,x\n" * 600_000)

    preview = preview_upload_path("millionish.csv", path)

    assert path.stat().st_size > 2 * 1024 * 1024
    assert preview.record_count == 600_000
    assert preview.fields_noticed == ["a", "b"]
