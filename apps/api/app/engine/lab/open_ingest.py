"""Accept a Labs file in ordinary data formats. No required field names.

This is capability 1 of Client Labs open ingest. Capability 2 (turn messy or
headerless files into a usable table via language tools or DCLab's own reading
pipeline) is documented and not implemented yet — see docs/LABS_DATA_UNDERSTANDING.md.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.translation.banned_terms import find_banned_terms

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 500

SPREADSHEET_SUFFIXES = {".csv", ".tsv", ".tab"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
TABLE_SUFFIXES = {".parquet", ".pq"}
EXCEL_SUFFIXES = {".xlsx", ".xls"}
TEXT_SUFFIXES = {".txt", ".log", ".text"}
SUPPORTED_SUFFIXES = (
    SPREADSHEET_SUFFIXES | JSON_SUFFIXES | TABLE_SUFFIXES | EXCEL_SUFFIXES | TEXT_SUFFIXES | {""}
)


class OpenIngestError(ValueError):
    """Safe, client-facing reason the file could not be taken in."""


@dataclass
class OpenIngestPreview:
    kind: str  # spreadsheet | json | table_file | plain_text
    record_count: int
    fields_noticed: list[str] = field(default_factory=list)
    has_named_fields: bool = False


def _safe_field(name: str, index: int) -> str:
    cleaned = " ".join(str(name).split()) or f"field {index + 1}"
    if find_banned_terms(cleaned):
        return f"field {index + 1}"
    return cleaned[:80]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _from_frame(frame: pd.DataFrame, kind: str) -> OpenIngestPreview:
    if frame.empty and list(frame.columns) == []:
        return OpenIngestPreview(kind=kind, record_count=0, fields_noticed=[], has_named_fields=False)
    named = not all(str(col).startswith("Unnamed") or str(col).isdigit() for col in frame.columns)
    fields = [_safe_field(str(col), i) for i, col in enumerate(frame.columns)]
    return OpenIngestPreview(
        kind=kind,
        record_count=int(len(frame)),
        fields_noticed=fields if named else [],
        has_named_fields=named,
    )


def _read_csv_like(data: bytes, *, suffix: str) -> pd.DataFrame:
    text = _decode(data)
    sep = "\t" if suffix in {".tsv", ".tab"} else None
    buffer = io.StringIO(text)
    try:
        return pd.read_csv(buffer, sep=sep, engine="python", on_bad_lines="skip")
    except csv.Error:
        buffer.seek(0)
        return pd.read_csv(buffer, sep=None, engine="python", header=None, on_bad_lines="skip")


def _read_json(data: bytes) -> pd.DataFrame:
    text = _decode(data).strip()
    if not text:
        return pd.DataFrame()
    if text[0] != "[" and "\n" in text:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return pd.json_normalize(rows)
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return pd.json_normalize(parsed)
    if isinstance(parsed, dict):
        for key in ("records", "data", "rows", "items"):
            if isinstance(parsed.get(key), list):
                return pd.json_normalize(parsed[key])
        return pd.json_normalize([parsed])
    raise OpenIngestError("This JSON file did not contain a list of records.")


def _read_plain_lines(data: bytes) -> OpenIngestPreview:
    text = _decode(data)
    lines = [line for line in text.splitlines() if line.strip()]
    return OpenIngestPreview(
        kind="plain_text",
        record_count=len(lines),
        fields_noticed=[],
        has_named_fields=False,
    )


def preview_upload(filename: str, data: bytes) -> OpenIngestPreview:
    """Inspect a file without requiring any particular field names."""
    if not data or not data.strip():
        raise OpenIngestError("This file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise OpenIngestError(
            f"This upload accepts files up to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    suffix = Path(filename or "upload").suffix.lower()
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        raise OpenIngestError(
            "This file type is not supported yet. Try a spreadsheet, JSON, Parquet, Excel, or a plain text log."
        )

    try:
        if suffix in TABLE_SUFFIXES or data[:4] == b"PAR1":
            frame = pd.read_parquet(io.BytesIO(data))
            preview = _from_frame(frame, "table_file")
        elif suffix in EXCEL_SUFFIXES:
            try:
                frame = pd.read_excel(io.BytesIO(data))
                preview = _from_frame(frame, "spreadsheet")
            except Exception:  # noqa: BLE001
                # Capability 1 still takes the file even when we cannot read the grid yet.
                preview = OpenIngestPreview(
                    kind="table_file",
                    record_count=0,
                    fields_noticed=[],
                    has_named_fields=False,
                )
        elif suffix in JSON_SUFFIXES or (not suffix and data.lstrip()[:1] in (b"{", b"[")):
            preview = _from_frame(_read_json(data), "json")
        elif suffix in TEXT_SUFFIXES:
            sample = _decode(data)[:2000]
            if "," not in sample and "\t" not in sample and "|" not in sample:
                return _read_plain_lines(data)
            # Prefer a spreadsheet parse when the log is actually delimited.
            try:
                frame = _read_csv_like(data, suffix=".csv")
            except Exception:  # noqa: BLE001
                return _read_plain_lines(data)
            if frame.shape[1] <= 1:
                return _read_plain_lines(data)
            preview = _from_frame(frame, "spreadsheet")
        else:
            frame = _read_csv_like(data, suffix=suffix or ".csv")
            if frame.shape[1] <= 1 and suffix in {"", ".csv"}:
                sample = _decode(data)[:2000]
                if "," not in sample and "\t" not in sample and "|" not in sample:
                    return _read_plain_lines(data)
            preview = _from_frame(frame, "spreadsheet")
    except OpenIngestError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OpenIngestError(
            "We could not read this file. Try a spreadsheet, JSON, Parquet, Excel, or a plain text log."
        ) from exc

    if preview.record_count > MAX_RECORDS:
        raise OpenIngestError(
            f"This upload notices at most {MAX_RECORDS} rows; the uploaded file has {preview.record_count}."
        )
    return preview
