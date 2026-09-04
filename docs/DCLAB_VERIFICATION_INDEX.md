# DCLab Verification Index

| Document | What it is | How it was produced |
| --- | --- | --- |
| [verification/BASELINE.md](verification/BASELINE.md) | Frozen `main` SHA, tool versions, isolation policy | Recorded before repairs |
| [DCLAB_DATABASE_ARCHITECTURE.md](DCLAB_DATABASE_ARCHITECTURE.md) | Physical schema, FKs, indexes, JSONB risks | Alembic head + `models.py` + pg_catalog on 55432 |
| [DCLAB_API_REFERENCE.md](DCLAB_API_REFERENCE.md) | Runtime OpenAPI operations | FastAPI `app.openapi()` |
| [DCLAB_RBAC_CAPABILITY_MATRIX.md](DCLAB_RBAC_CAPABILITY_MATRIX.md) | Role/capability enforcement | Route guards + capability service + tests |
| [DCLAB_PIPELINE_DEEP_DIVE.md](DCLAB_PIPELINE_DEEP_DIVE.md) | 90 implementation-linked pipeline topics | `auto_train_service`, `runner`, `auto_prepare`, verifier |
| [DCLAB_E2E_VERIFICATION_RUNBOOK.md](DCLAB_E2E_VERIFICATION_RUNBOOK.md) | Reproducible commands | Isolated 55432 cluster |
| [DCLAB_SYSTEM_VERIFICATION_REPORT.md](DCLAB_SYSTEM_VERIFICATION_REPORT.md) | Claim ledger, P0–P3, checklists, command log | Runtime evidence from isolated 55432 + Playwright + live OpenAI |
| [DCLAB_ACCESS_ARCHITECTURE.md](DCLAB_ACCESS_ARCHITECTURE.md) | Identity and route trees | Corrected where runtime disproved a claim |
| [DCLAB_BUSINESS_ADMINISTRATION.md](DCLAB_BUSINESS_ADMINISTRATION.md) | Business plane | Source doc; see report checklists |
| [DCLAB_PLATFORM_ADMINISTRATION.md](DCLAB_PLATFORM_ADMINISTRATION.md) | Platform plane | Source doc; see report checklists |
| [DCLAB_DATA_AND_MODEL_LINEAGE.md](DCLAB_DATA_AND_MODEL_LINEAGE.md) | Lineage hierarchy | Source doc; 0027 repair noted in report |
| [DCLAB_PIPELINE_OBSERVABILITY.md](DCLAB_PIPELINE_OBSERVABILITY.md) | Events and LLM ledger | Source doc; observatory role gate updated |
| [DCLAB_ADAPTIVE_MODEL_BUILDER.md](DCLAB_ADAPTIVE_MODEL_BUILDER.md) | Phase 1 scientific planning layer plus adaptive final holdout | ProblemProfile, HoldoutPlan, ValidationPlan, MetricPlan, LeakageAuditor, verifier, benchmarks |
| [DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md](DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md) | Production Labs E2E scientific proof (Repairs 1–3) | `/app/labs/uploads` → `run_auto_train_job` → ModelVersion → verifier → monitor |

Evidence artifacts (gitignored): `artifacts/e2e-verification/`.
