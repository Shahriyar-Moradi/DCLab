# DCLab Verification Baseline

- Commit SHA: `de2af56824b81400624f34758308324db34f4da9`
- Branch: `main`
- Recorded at: `2026-09-04T11:20:01Z`
- Initial working tree: clean (`main...origin/main`)
- Python available before setup: `3.14.7`
- Repository/CI Python: `3.12`
- Node available before setup: `24.11.1`
- Repository/CI Node: `20`
- npm: `11.6.2`
- PostgreSQL client: `16.15 (Homebrew)`
- PostgreSQL server: `16.15 (Homebrew)`
- OpenAI SDK before setup: not installed in the system Python
- OpenAI SDK requirement: `>=3.6,<4`

## Important environment modes

- Configured database target before isolation: PostgreSQL on `localhost:5432`, database `decisionai`.
- Verification database policy: disposable isolated databases only; the configured development database must not be mutated.
- `OPENAI_API_KEY`: configured (value not recorded).
- `DECISION_AGENT_ENABLED`: not configured.
- `DECISION_AGENT_API_KEY`: not configured.
- `PIPELINE_LLM_VERIFIER_ENABLED`: not configured.
- `PIPELINE_LLM_VERIFIER_API_KEY`: not configured.
- Authorized live checks: Luna routine smoke/audit and Terra deep audit, if credentials and provider/model access are valid.

## Initial history

```text
de2af56 intial workflow pipeline
d59a2d5 DCLab Multi-Tenant Identity and Four-Role Administration Foundation
9599a18 phase2.1
cafe943 start phase2
f1f408e phas 1.0
e584de6 fix api cors
ea1fc97 first codex fixed implementation
6a48a5a deterministic run pipeline, with small evidence-based LLM checks from cluad
060e457 Run Labs uploads through a hidden ML pipeline and show clients only processing then results.
ee39c01 Split admin and client surfaces and auto-train simple Labs uploads.
```

This file records the pre-verification state. Later tool installation, generated evidence,
tests, repairs, and documentation are changes made after this baseline.
