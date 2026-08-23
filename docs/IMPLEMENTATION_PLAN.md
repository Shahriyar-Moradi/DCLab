# Implementation Plan — Step by Step

## Sprint 1 — Foundation

### Day 1
- create repository
- Python 3.11 environment
- FastAPI skeleton
- config management
- Docker Compose
- PostgreSQL
- MLflow
- tests

### Day 2
- dataset abstraction
- schema profiler
- target/horizon schema
- temporal split utility
- leakage checks

### Day 3
- feature group registry
- feature-set generator
- preprocessing pipelines
- missing/categorical/numeric handling

### Day 4
- candidate model factory
- hyperparameter search budget
- model-run object
- MLflow logging

### Day 5
- evaluation
- OOF predictions
- calibration
- model ranking

### Day 6
- diversity matrix
- greedy selection
- weighted blend
- baseline comparison

### Day 7
- IntelligenceState
- API
- persistence
- integration tests
- first demo

## Sprint 2 — Harden prediction factory

- parallel execution
- retries
- resource limits
- model artifact management
- feature lineage
- dataset versioning
- drift checks
- subgroup evaluation
- threshold optimization
- prediction explanations

## Sprint 3 — Prediction domains

Create reusable layer configs for:
- purchase
- churn
- upsell
- cross-sell
- engagement
- price sensitivity
- discount sensitivity
- email response
- campaign response
- next purchase time

## Sprint 4 — Cross-layer state

Implement:
- layer dependencies
- state store
- graph representation
- state snapshots
- confidence propagation
- disagreement detection

## Sprint 5 — Recommendation factory

Implement action registry and action-specific layer templates.

Example action object:

```json
{
  "action": "offer_discount",
  "parameters": {"discount_pct": 10},
  "eligibility": {},
  "expected_objectives": ["conversion", "revenue", "margin"]
}
```

## Sprint 6 — Outcome factory

Implement action→outcome modeling with strict treatment-time semantics.

## Sprint 7 — Causal/counterfactual engine

Start with randomized experiment data. Then add observational methods.

## Sprint 8 — Simulation

Scenario graph:

CurrentState → Action → OutcomeLayers → BusinessValue

Support scenario comparison and sensitivity analysis.

## Sprint 9 — Decision engine

Implement objective/constraint framework.

Example:

maximize expected_margin
subject to:
- discount <= 15%
- contact_frequency <= 2 / 7 days
- eligible_customer = true

## Sprint 10 — Feedback

Implement event logging and realized-vs-predicted analysis.

## Sprint 11+ — Enterprise

- connectors
- multi-tenancy
- RBAC
- audit
- SSO
- encryption
- secrets
- deployment templates
- monitoring
- cost controls
