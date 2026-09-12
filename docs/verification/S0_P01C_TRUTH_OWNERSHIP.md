# S0-P01C truth ownership and generated artifacts

**Status:** CURRENT  
**Plan/prompt:** S0-P01C  
**Canonical artifacts:** [contracts ownership and refresh guide](../../contracts/README.md)

Every mechanically checked fact has one generator and one declared artifact
owner. `scripts.generate_truth_artifacts` is the only writer;
`scripts.record_repo_truth` reports live/dynamic Git context and reuses the
canonical inventory collector; `scripts.check_truth_drift` is read-only.

## Ownership map

| Fact family | Canonical checked artifact | Generator input |
| --- | --- | --- |
| `/v1` request/response compatibility | [`v1_openapi.json`](../../contracts/v1_openapi.json) | runtime FastAPI OpenAPI |
| HTTP operation membership | [`openapi_operations.json`](../../contracts/openapi_operations.json) | runtime FastAPI OpenAPI |
| ORM table registry | [`sqlalchemy_tables.json`](../../contracts/sqlalchemy_tables.json) | SQLAlchemy metadata |
| Alembic aggregates and repository/source/test/web inventory | [`truth_baseline.json`](../../contracts/truth_baseline.json) | migration graph plus Git tracked/non-ignored worktree candidates |
| Generator, source and artifact provenance | [`truth_manifest.json`](../../contracts/truth_manifest.json) | generator identity, non-generated source bytes, generated artifact bytes |

README, the verification indexes, and the master plan link to these artifacts
instead of owning another CURRENT numeric inventory. API/database/ERD/migration
reports that contain older numbers remain explicitly HISTORICAL and link to the
CURRENT truth package; their frozen evidence bodies were not rewritten.

## Commands

```bash
make truth-generate
make truth-check
make truth-idempotence

.venv/bin/python -m scripts.generate_truth_artifacts --check
.venv/bin/python -m scripts.check_truth_drift
```

CI runs two consecutive generations, requires byte-identical files, checks
that `contracts/` has no resulting Git diff, and then runs the read-only drift
and migrated-metadata checks. Generation and checking require no network.

## Evidence

```text
Plan/prompt ID: S0-P01C
Claim: Mechanical truth has one writer, declared artifact owners, content provenance, and idempotent regeneration
Status: VERIFIED
Commit/image digest: uncommitted working tree on top of 3d54994
Environment: local macOS; .venv CPython 3.12
Migration path tested: no migration or schema change
Commands: generate --check; generate --verify-idempotent; check_truth_drift; focused pytest; CI-equivalent contracts diff
Expected result: manifest digests match; two generations are byte-identical; regeneration adds no Git diff; all drift detectors clean
Observed result: manifest/source/artifact digests match; 27 truth tests passed; 32 combined guard tests passed; generator --check clean; two generations byte-identical; contracts diff unchanged by consecutive live regeneration; isolated fixture Git diff clean
Artifact: contracts/truth_manifest.json; docs/verification/S0_P01C_TRUTH_OWNERSHIP.md
Security and tenant checks: generated source hashing excludes ignored runtime artifacts; existing secret and tracked-artifact detectors remain mandatory
Rollback/kill switch: revert generator/checker/manifest/Make/CI/current-doc changes; no data or schema rollback
Known limitations: GitHub exact-SHA result requires a separately authorized commit and push
Reviewer/date: S0-P01C / 2026-09-11
```

## Closure

**S0-P01D is VERIFIED** in
[`S0_P01D_BASELINE_GATE.md`](S0_P01D_BASELINE_GATE.md). The next implementation
prompt is **S0-P02C**.
