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


def _from_columns(*, kind: str, record_count: int, columns: list[str]) -> OpenIngestPreview:
    named = not all(str(col).startswith("Unnamed") or str(col).isdigit() for col in columns)
    fields = [_safe_field(str(col), index) for index, col in enumerate(columns)]
    return OpenIngestPreview(
        kind=kind,
        record_count=record_count,
        fields_noticed=fields if named else [],
        has_named_fields=named,
    )


def _csv_preview_from_path(path: Path, *, suffix: str) -> OpenIngestPreview:
    """Read CSV/TSV metadata without materialising every row in memory."""
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(64 * 1024)
        handle.seek(0)
        if suffix in {".tsv", ".tab"}:
            delimiter = "\t"
        else:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",\t|;").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, [])
        if len(header) <= 1 and suffix in {"", ".csv"} and not any(
            separator in sample for separator in (",", "\t", "|")
        ):
            return _plain_text_preview_from_path(path)
        record_count = sum(1 for row in reader if row and any(value.strip() for value in row))
    return _from_columns(
        kind="spreadsheet", record_count=record_count, columns=[str(value) for value in header]
    )


def _plain_text_preview_from_path(path: Path) -> OpenIngestPreview:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        record_count = sum(1 for line in handle if line.strip())
    return OpenIngestPreview(
        kind="plain_text",
        record_count=record_count,
        fields_noticed=[],
        has_named_fields=False,
    )


def _json_lines_preview_from_path(path: Path) -> OpenIngestPreview:
    first_record: object | None = None
    record_count = 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            value = line.strip()
            if not value:
                continue
            parsed = json.loads(value)
            if first_record is None:
                first_record = parsed
            record_count += 1
    columns = list(first_record.keys()) if isinstance(first_record, dict) else []
    return _from_columns(kind="json", record_count=record_count, columns=columns)


def _has_non_whitespace_bytes(path: Path) -> bool:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if chunk.strip():
                return True
    return False


def preview_upload_path(filename: str, path: Path) -> OpenIngestPreview:
    """Inspect a persisted upload with no application file-, row-, or column cap.

    CSV/TSV and line-delimited JSON are scanned incrementally so files with
    millions of rows do not need a second in-memory copy just to establish basic
    metadata. The training worker still has its own compute requirements.
    """
    if not path.is_file() or path.stat().st_size == 0 or not _has_non_whitespace_bytes(path):
        raise OpenIngestError("This file is empty.")

    suffix = Path(filename or "upload").suffix.lower()
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        raise OpenIngestError(
            "This file type is not supported yet. Try a spreadsheet, JSON, Parquet, Excel, or a plain text log."
        )
    try:
        if suffix in SPREADSHEET_SUFFIXES or not suffix:
            return _csv_preview_from_path(path, suffix=suffix or ".csv")
        if suffix in TEXT_SUFFIXES:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                sample = handle.read(2000)
            if not any(separator in sample for separator in (",", "\t", "|")):
                return _plain_text_preview_from_path(path)
            return _csv_preview_from_path(path, suffix=".csv")
        if suffix in {".jsonl", ".ndjson"}:
            return _json_lines_preview_from_path(path)
        if suffix in TABLE_SUFFIXES:
            import pyarrow.parquet as pq

            parquet = pq.ParquetFile(path)
            return _from_columns(
                kind="table_file",
                record_count=int(parquet.metadata.num_rows if parquet.metadata else 0),
                columns=list(parquet.schema.names),
            )

        # JSON arrays and Excel workbooks have no universally available
        # incremental reader in the current dependency set. They are still not
        # size-limited; this preserves the existing parser for those formats.
        return preview_upload(filename, path.read_bytes())
    except OpenIngestError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OpenIngestError(
            "We could not read this file. Try a spreadsheet, JSON, Parquet, Excel, or a plain text log."
        ) from exc


def preview_upload(filename: str, data: bytes) -> OpenIngestPreview:
    """Inspect a file without requiring any particular field names."""
    if not data or not data.strip():
        raise OpenIngestError("This file is empty.")

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

    return preview
