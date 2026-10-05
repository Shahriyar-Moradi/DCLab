"""Prompt releases from code-owned prompt files (ADR 0009 §1, §2.8).

Convention: ``app/agents/prompts/<agent_key>/v<N>.md`` (real prompts only; test
fixtures live under ``tests/fixtures/prompts``) is the system prompt text
and ``v<N>.schema.json`` the JSON schema of the typed output. ``prompt_digest`` =
sha256 of the file text; ``output_schema_digest`` = sha256 of the canonical schema
JSON, which must equal the digest of the output model the caller passes to the
gateway. ``sync_prompt_releases`` (``dclab agents sync-prompts``) inserts missing
rows (the highest version ``released``, older ones ``retired``), retires
superseded releases, and never rewrites a row: an edited released file is a
mismatch (a text change is a new version). ``verify_prompt_releases`` lists
released rows whose file is missing or differs (CI). Jev purpose releases
(``jev:<purpose>``) are owned by ``agents/semantic/releases.py`` (``sync_jev_releases``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PromptRelease
from app.domain.agent_records import KEY_PATTERN

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"
_VERSION_FILE = re.compile(r"^v([1-9][0-9]{0,5})\.md$")
_KEY = re.compile(KEY_PATTERN)
JEV_PREFIX = "jev:"


class PromptReleaseMismatch(Exception):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_digest(schema: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(schema)).hexdigest()


def output_schema_digest(model: type[BaseModel]) -> str:
    return schema_digest(model.model_json_schema())


@dataclass(frozen=True)
class PromptFile:
    agent_key: str
    version: int
    path: Path
    prompt_digest: str
    output_schema_digest: str


def discover(root: Path = PROMPTS_ROOT) -> list[PromptFile]:
    files: list[PromptFile] = []
    if not root.is_dir():  # no prompt has been released from code yet
        return files
    for directory in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        if not _KEY.fullmatch(directory.name):
            raise PromptReleaseMismatch(f"prompt directory {directory.name!r} is not an agent key")
        for path in sorted(directory.glob("v*.md")):
            match = _VERSION_FILE.fullmatch(path.name)
            schema_path = path.with_suffix(".schema.json")
            if match is None or not schema_path.is_file():
                raise PromptReleaseMismatch(f"{path} needs the name v<N>.md and a v<N>.schema.json")
            files.append(PromptFile(
                agent_key=directory.name,
                version=int(match.group(1)),
                path=path,
                prompt_digest=text_digest(path.read_text(encoding="utf-8")),
                output_schema_digest=schema_digest(json.loads(schema_path.read_text(encoding="utf-8"))),
            ))
    return files


def _path(root: Path, agent_key: str, version: int) -> Path:
    return root / agent_key / f"v{version}.md"


def prompt_text(agent_key: str, version: int, root: Path = PROMPTS_ROOT) -> str:
    """The code-owned prompt file's text (the file is the single owner; legacy modules
    that still export ``SYSTEM_PROMPT`` read it from here)."""

    return _path(root, agent_key, version).read_text(encoding="utf-8")


def load_release_text(release: PromptRelease, root: Path = PROMPTS_ROOT) -> str:
    """The released prompt text; refuses a missing or changed file."""

    path = _path(root, release.agent_key, release.version)
    if not _KEY.fullmatch(release.agent_key) or release.agent_key.startswith(JEV_PREFIX) or not path.is_file():
        raise PromptReleaseMismatch(f"no prompt file for {release.agent_key} v{release.version}")
    text = path.read_text(encoding="utf-8")
    if text_digest(text) != release.prompt_digest.strip():
        raise PromptReleaseMismatch(f"{release.agent_key} v{release.version} differs from its release")
    return text


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {name: getattr(self, name) for name in ("created", "retired", "unchanged", "mismatched")}


def sync_prompt_releases(db: Session, root: Path = PROMPTS_ROOT) -> SyncResult:
    result = SyncResult()
    files = discover(root)
    latest: dict[str, int] = {}
    for item in files:
        latest[item.agent_key] = max(latest.get(item.agent_key, 0), item.version)
    for item in files:
        label = f"{item.agent_key}@v{item.version}"
        row = db.scalar(select(PromptRelease).where(
            PromptRelease.agent_key == item.agent_key, PromptRelease.version == item.version
        ))
        current = item.version == latest[item.agent_key]
        if row is None:
            db.add(PromptRelease(
                agent_key=item.agent_key, version=item.version, prompt_digest=item.prompt_digest,
                output_schema_digest=item.output_schema_digest, status="released" if current else "retired",
                released_at=datetime.now(UTC) if current else None,
                notes=f"synced from {item.agent_key}/v{item.version}.md",
            ))
            result.created.append(label)
        elif (row.prompt_digest.strip(), row.output_schema_digest.strip()) != (
            item.prompt_digest, item.output_schema_digest
        ):
            result.mismatched.append(label)
        elif not current and row.status == "released":
            row.status = "retired"
            result.retired.append(label)
        else:
            result.unchanged.append(label)
    db.flush()
    return result


def verify_prompt_releases(db: Session, root: Path = PROMPTS_ROOT) -> list[str]:
    """Released rows whose file is missing or whose digests differ from the file."""

    files = {(item.agent_key, item.version): item for item in discover(root)}
    problems = []
    for row in db.scalars(select(PromptRelease).where(PromptRelease.status == "released")):
        if row.agent_key.startswith(JEV_PREFIX):
            continue
        item = files.get((row.agent_key, row.version))
        if item is None:
            problems.append(f"{row.agent_key}@v{row.version}: no prompt file")
        elif (row.prompt_digest.strip(), row.output_schema_digest.strip()) != (
            item.prompt_digest, item.output_schema_digest
        ):
            problems.append(f"{row.agent_key}@v{row.version}: digest differs from the file")
    return problems
