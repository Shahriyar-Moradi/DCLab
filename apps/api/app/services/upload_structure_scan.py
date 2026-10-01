"""Bounded structural checks for local/development upload compatibility.

This is NOT malware detection or a production publication receipt. Production
upload routes remain closed until an approved scanner and classifier exist.
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

from app.engine.lab.open_ingest import OpenIngestError

_TEXT = {"", ".csv", ".tsv", ".tab", ".json", ".jsonl", ".ndjson", ".txt", ".log", ".text"}
_MIMES = {
    ".csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
    ".tsv": {"text/tab-separated-values", "text/plain"},
    ".tab": {"text/tab-separated-values", "text/plain"},
    ".json": {"application/json", "text/json"},
    ".jsonl": {"application/x-ndjson", "application/json", "text/plain"},
    ".ndjson": {"application/x-ndjson", "application/json", "text/plain"},
    ".parquet": {"application/vnd.apache.parquet", "application/octet-stream"},
    ".pq": {"application/vnd.apache.parquet", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
}
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"%PDF", b"\x89PNG", b"\xff\xd8\xff")


def inspect_upload_structure(filename: str, path: Path, *, declared_mime: str | None) -> None:
    """Reject deceptive metadata before any parser sees uploaded bytes."""
    if (
        not filename or len(filename) > 200 or filename in {".", ".."}
        or "/" in filename or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise OpenIngestError("This filename is not safe to upload.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".xls":
        raise OpenIngestError("Legacy Excel files are not accepted; export as .xlsx or CSV.")
    if suffix not in _TEXT | {".parquet", ".pq", ".xlsx"}:
        raise OpenIngestError("This file type is not supported yet.")
    mime = (declared_mime or "").split(";", 1)[0].strip().lower()
    allowed = _MIMES.get(suffix, {"text/plain"})
    if mime and mime not in allowed:
        raise OpenIngestError("The file type does not match its declared content type.")
    with path.open("rb") as handle:
        head = handle.read(8)
        handle.seek(-min(path.stat().st_size, 8), 2)
        tail = handle.read(8)
    if suffix in _TEXT:
        if head.startswith(_EXECUTABLE_MAGIC) or head.startswith(b"PK\x03\x04"):
            raise OpenIngestError("The file content does not match its extension.")
    elif suffix in {".parquet", ".pq"}:
        if not head.startswith(b"PAR1") or not tail.endswith(b"PAR1"):
            raise OpenIngestError("The Parquet file signature is invalid.")
    else:
        if not head.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(path):
            raise OpenIngestError("The Excel file signature is invalid.")
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > 2000:
                    raise OpenIngestError("The Excel archive contains too many entries.")
                seen: set[str] = set()
                total_size = 0
                for member in members:
                    name = member.filename.lower()
                    parts = name.split("/")
                    total_size += member.file_size
                    if (
                        name in seen or name.startswith("/") or "\\" in name or ".." in parts
                        or member.flag_bits & 1
                        or stat.S_ISLNK(member.external_attr >> 16)
                        or "vbaproject" in name or "externallinks/" in name
                        or "embeddings/" in name
                        or member.file_size > 100 * max(member.compress_size, 1)
                        or total_size > 256 * 1024 * 1024
                    ):
                        raise OpenIngestError("The Excel archive contains unsafe entries.")
                    seen.add(name)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise OpenIngestError("The Excel archive is invalid.") from exc
