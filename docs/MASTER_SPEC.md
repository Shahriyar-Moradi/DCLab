# Decision.ai Master Specification

## 1. Vision

Decision.ai is intended to disrupt the machine-learning/data-science workflow by turning a large part of the repetitive model-building and hypothesis-testing process into an automated, parallel, measurable intelligence engine.

Traditional workflow:

Business question → data request → data cleaning → feature engineering → hypothesis → model → experiment → evaluation → another hypothesis → another model → interpretation → deployment → monitoring.

Decision.ai target workflow:

Business objective + governed data → profile → feature intelligence → hypothesis/layer definition → candidate feature/model search → parallel training → validation → diversity-aware selection → ensemble → semantic intelligence state → cross-layer reasoning → candidate actions → outcome prediction → simulation → decision → action → measured outcome → feedback/retraining.

The objective is not to eliminate data scientists. The objective is to remove repetitive exploratory work and give ML/data teams a much larger experimental surface with stronger engineering, traceability and speed.

## 2. Product positioning

Avoid positioning as:
- “100 models”
- generic AutoML
- another dashboard
- an LLM wrapper
- another feature store
- another model registry
- a chatbot that explains charts

Position it as:

**An automated multi-perspective predictive and decision intelligence engine that turns business data into tested predictions, action recommendations, outcome forecasts and scenario comparisons.**

The most defensible initial claim is measurable speed and breadth:
- evaluate hundreds of candidate configurations automatically
- retain only strong and useful models
- preserve model diversity
- create semantic intelligence layers
- connect predictions to actions and outcomes
- measure actual business impact

Never promise that more models automatically means higher accuracy.

## 3. Fundamental hierarchy

### 3.1 Model
The atomic ML unit.

A model has:
- model_id
- layer_id
- algorithm
- feature_set_id
- target
- hyperparameters
- training dataset/version
- validation strategy
- metrics
- calibration metrics
- subgroup metrics
- temporal robustness metrics
- predictions
- artifact location
- code/version
- training timestamp
- status

### 3.2 Intelligence Layer
A semantic business question.

Examples:
- purchase_probability
- churn_probability
- upsell_probability
- email_response
- discount_sensitivity
- probability_of_success_if_email
- expected_revenue_if_discount

A layer owns its target, feature policy, candidate models, evaluation criteria, selection policy and final intelligence state.

A layer may generate 100s of candidates and retain ~20–50 only when those models are strong, diverse, stable and relevant. The retained count is a configurable outcome, not a magic number.

### 3.3 Intelligence Domain
A group of related layers.

PredictionDomain:
- purchase
- churn
- upsell
- cross-sell
- engagement
- price/discount sensitivity
- campaign/email response
- next purchase time

RecommendationDomain:
- email
- call
- discount
- retarget
- pricing
- product
- channel
- message
- no-action

OutcomeDomain:
- purchase
- revenue
- margin
- churn
- engagement
- customer value
- satisfaction/reaction

SimulationDomain:
- scenario construction
- action comparison
- expected value
- constraints
- uncertainty

### 3.4 Intelligence State
The current semantic output of a layer.

Example purchase state:
- probability = 0.78
- confidence = high
- model_count_evaluated = 312
- model_count_selected = 37
- model_agreement = 0.91
- strongest feature signals = recent purchase, engagement, product affinity, session frequency
- data quality = 0.96
- drift = low
- validation metrics
- lineage
- timestamp

A state must preserve provenance. A business user should be able to trace a conclusion back to models, features, dataset versions and validation results.

## 4. Feature Intelligence Engine

This is one of the most important parts of Decision.ai.

The platform does not simply search model algorithms. It searches the **feature space + model space**.

### Feature families

Customer:
- age
- geography
- segment
- tenure
- customer value

Behavioral:
- sessions
- clicks
- views
- searches
- engagement
- frequency
- recency
- sequences

Transactional:
- orders
- average order value
- purchase frequency
- returns
- discount usage
- days since purchase

Marketing:
- impressions
- campaigns
- emails
- opens
- clicks
- response history
- channel

Product:
- category
- SKU
- product views
- affinity
- inventory
- product history

Temporal:
- day/week/month
- season
- lag
- rolling windows
- trend
- time since event

Economic:
- revenue
- margin
- CAC
- discount cost
- LTV
- expected value

Contextual:
- geography context
- channel context
- campaign context
- operational context
- market context

Intervention history:
- previous treatment/action
- previous discount
- previous contact
- previous channel

### Feature operations

The engine should eventually support:
- group combinations
- transformations
- ratios
- deltas
- rolling statistics
- lag features
- recency/frequency/monetary features
- trend and acceleration
- sequence features
- interaction terms
- target-encoded/categorical representations when appropriate
- embeddings for high-dimensional/unstructured data where justified

### Feature governance

Every feature needs:
- name
- definition
- data source
- owner
- type
- availability timestamp
- allowed use by layer
- transformation
- lineage
- leakage risk
- missingness
- cardinality
- stability
- version

### Leakage prevention

This is non-negotiable.

Examples of illegal features for a prediction made before purchase:
- purchase confirmation
- post-purchase revenue
- post-action engagement
- future customer status

The feature engine must know the prediction timestamp and only use information available at that time.

## 5. Candidate generation

A layer can start with hundreds of candidate configurations.

Example:

Feature group combinations:
- behavioral
- transactional
- behavioral + transactional
- behavioral + marketing
- transactional + temporal
- customer + behavioral + product
- marketing + temporal + behavioral
- economic + transaction + customer

Then algorithm families:
- logistic regression
- elastic net
- random forest
- extra trees
- gradient boosting
- LightGBM
- XGBoost
- CatBoost when useful
- calibrated variants
- stacking/blending

Then hyperparameters.

Candidate count may become:

feature sets × algorithms × hyperparameters × preprocessing variants.

The search engine must be budget-aware. A fixed maximum prevents runaway compute.

## 6. Validation strategy

Random train/test splitting is not always valid.

For time-dependent business data, use temporal validation:

Train: older period
Validation: later period
Test: latest period

For repeated entities, prevent customer-level leakage across splits when required.

For imbalanced classification, evaluate:
- PR-AUC
- ROC-AUC
- precision
- recall
- F1
- calibration
- confusion matrix at business threshold

For regression:
- MAE
- RMSE
- MAPE/SMAPE where appropriate
- quantile loss when uncertainty is needed

For ranking:
- NDCG
- MAP
- precision@k

The optimization metric must be configurable per layer.

## 7. Model selection

The platform should NOT select the top 30 models by one metric.

Selection should be multi-objective:

performance + diversity + calibration + stability + subgroup robustness + business relevance + compute cost.

### Diversity

If 30 models make almost identical predictions and errors, they are not 30 independent sources of evidence.

Measure:
- prediction correlation
- residual/error correlation
- disagreement
- feature-set overlap
- algorithm diversity

A practical first selector:
1. rank models by validation score
2. select best
3. reject candidates whose prediction behavior is too correlated with selected models
4. continue until budget is reached

Later:
- determinantal point processes
- submodular selection
- ensemble optimization
- stacking with out-of-fold predictions

## 8. Ensemble

Candidate methods:
- weighted average
- soft voting
- stacking
- blending
- calibrated stacking

Never train the meta-model on in-sample predictions. Use out-of-fold predictions to prevent leakage.

The ensemble should output:
- final prediction
- model agreement
- uncertainty proxy
- selected model IDs
- weights
- layer version

## 9. Calibration and uncertainty

A probability of 0.80 should mean approximately 80% under appropriate calibration conditions.

Support:
- Platt scaling
- isotonic regression
- calibration curves
- Brier score
- expected calibration error

Also track:
- model disagreement
- data quality
- distribution shift
- distance from training distribution

A system that says “I do not have enough evidence” is more valuable than one that gives false precision.

## 10. Prediction layers

### Purchase probability
Question: probability customer purchases within defined horizon.

### Churn probability
Question: probability customer churns within defined horizon.

### Upsell probability
Question: probability customer accepts/executes upsell opportunity.

### Cross-sell probability
Question: probability customer buys another relevant product/category.

### Discount sensitivity
Question: how customer purchase behavior varies with discount exposure.

### Price sensitivity
Question: expected response across price conditions.

### Engagement probability
Question: probability of meaningful engagement.

### Email response
Question: probability of open/click/conversion under email exposure.

### Campaign response
Question: expected response to campaign exposure.

### Next purchase time
Question: expected time until next purchase; can be regression/survival modeling.

## 11. Recommendation layers

Recommendation layers represent candidate interventions.

Examples:
- send_email
- call_customer
- offer_discount
- change_price
- change_product
- retarget
- cross_sell
- upsell
- do_nothing
- change_channel
- change_message

Each action layer can contain multiple models for different objectives:

For email:
- probability of success
- expected conversion
- expected revenue
- expected margin
- cost
- churn impact
- engagement impact

For discount:
- purchase probability
- incremental purchase probability
- expected revenue
- expected margin
- discount cost
- churn impact
- long-term value

For call:
- probability of success
- expected revenue
- expected margin
- contact cost
- customer annoyance/risk where measurable

## 12. Recommendation fusion

Example state:

Purchase probability = 78%
Churn probability = 8%
Upsell probability = 61%
Discount sensitivity = 82%
Email response = 73%

The recommendation engine combines layer states and action-specific outcome predictions.

Do not blindly concatenate all values into one opaque model. Preserve semantic meaning and provenance.

## 13. Outcome/reaction layers

After a candidate action, predict downstream outcomes.

Examples:
- purchase after email
- revenue after call
- margin after discount
- churn after campaign
- engagement after message
- long-term value after intervention
- customer reaction

The system should distinguish:

Predictive:
P(Y | X)

Counterfactual/causal:
P(Y | do(A), X)

The second requires treatment/intervention evidence.

## 14. Simulation

Current state + candidate action → predicted outcome distribution.

Example:

CALL:
- success probability 71%
- expected revenue AED 8,400
- expected margin AED 3,200

EMAIL:
- success probability 64%
- expected revenue AED 5,800
- expected margin AED 2,700

DISCOUNT:
- success probability 79%
- expected revenue AED 9,200
- expected margin AED 2,900

NO ACTION:
- expected revenue AED 1,500

The simulator compares scenarios under explicit objectives and constraints.

Do not present simulation as certainty. Show ranges, confidence and assumptions.

## 15. Decision engine

The final decision can optimize:

Expected business value = expected revenue − expected cost − expected risk, subject to constraints.

Constraints can include:
- margin floor
- customer eligibility
- budget
- contact frequency
- legal/compliance policies
- inventory
- channel capacity
- business rules

The engine returns:
- recommended action
- expected value
- confidence
- alternatives
- evidence
- assumptions
- models used
- approval requirement

## 16. Feedback loop

Every decision creates an immutable event trail:
- decision_id
- entity_id
- state snapshot
- layer versions
- selected model versions
- recommendation
- action actually executed
- timestamp
- experiment/treatment group
- observed outcome
- financial outcome
- human override
- override reason

The feedback system later supports:
- evaluation
- drift monitoring
- retraining
- causal learning
- policy optimization

## 17. Dashboard concept

Each layer should have a dashboard/state view, but dashboards are not the product itself.

Purchase Probability:
- probability
- confidence
- model count evaluated
- models selected
- model agreement
- top signals
- calibration
- drift
- data quality

Recommendation Intelligence:
- best action
- expected success
- expected revenue
- expected margin
- expected cost
- confidence
- alternatives

Simulation:
- scenarios
- outcome distributions
- expected value
- risk
- assumptions
- sensitivity

Cross-layer view:
- prediction states
- recommendation states
- outcome states
- dependencies
- contradictions
- decision rationale

## 18. Data model

Core entities:

Tenant
Dataset
DatasetVersion
FeatureDefinition
FeatureSet
FeatureSetVersion
Domain
Layer
LayerVersion
ModelCandidate
ModelRun
ModelArtifact
Ensemble
IntelligenceState
DecisionGraph
Decision
Action
Scenario
ScenarioResult
Outcome
Experiment
Treatment
FeedbackEvent
MonitoringMetric
DriftEvent

Suggested relationships:

Tenant 1→N Dataset
Dataset 1→N DatasetVersion
FeatureDefinition N→N DatasetVersion
Layer 1→N LayerVersion
LayerVersion 1→N ModelRun
ModelRun N→1 FeatureSetVersion
ModelRun 1→1 ModelArtifact
LayerVersion 1→N IntelligenceState
IntelligenceState N→N DecisionGraph
Decision 1→N Scenario
Decision 1→1 Action
Action 1→N Outcome
Outcome 1→N FeedbackEvent

## 19. Service architecture

Start modular monolith. Split services only when scale requires it.

Suggested modules:
- API service
- Dataset service
- Feature intelligence service
- Experiment/orchestration service
- Training workers
- Model evaluation service
- Ensemble service
- Layer state service
- Recommendation service
- Outcome service
- Simulation service
- Decision service
- Feedback service
- Monitoring service

Later distributed architecture:

API → job queue → orchestration → parallel workers → object storage/model registry → state store → decision engine.

## 20. Technology baseline

Python:
- FastAPI
- Pydantic
- pandas/Polars as appropriate
- NumPy
- scikit-learn
- LightGBM
- XGBoost
- CatBoost when useful

Experiment/model tracking:
- MLflow

Storage:
- PostgreSQL for metadata/state
- object storage for datasets/artifacts
- feature store later if scale requires it

Orchestration:
- start with Celery/RQ/async jobs or a cloud queue
- move to distributed orchestration when needed

Containerization:
- Docker

Cloud options:
- AWS/GCP/Azure depending on customer environment

Observability:
- Prometheus
- Grafana
- structured logs

Do not introduce Kubernetes on day one unless deployment needs justify it.

## 21. Repository layout

```text
decision-ai/
├── app/
│   ├── api/
│   ├── core/
│   ├── schemas/
│   ├── registry/
│   ├── data/
│   ├── features/
│   ├── ml/
│   ├── layers/
│   ├── domains/
│   ├── decision/
│   ├── recommendation/
│   ├── outcome/
│   ├── simulation/
│   ├── feedback/
│   ├── monitoring/
│   └── db/
├── configs/
│   ├── layers/
│   └── models/
├── data/
│   ├── raw/
│   ├── processed/
│   └── feature_sets/
├── experiments/
├── models/
├── notebooks/
├── tests/
├── docs/
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 22. First vertical slice

Use one dataset and one target.

Target:
`purchase_within_30d`

Build:

1. Dataset loader
2. Schema profiler
3. Timestamp detection
4. Leakage checks
5. Feature groups
6. Temporal split
7. Candidate feature-set generator
8. Candidate algorithm generator
9. Parallel training
10. Validation metrics
11. Out-of-fold predictions
12. Diversity analysis
13. Model selection
14. Weighted blend/stacking
15. Calibration
16. Intelligence state
17. Persist metadata/artifacts
18. API endpoint
19. Compare against a strong single-model baseline

The benchmark should compare:
- manual baseline
- best single automated model
- selected ensemble
- time to result
- compute cost
- predictive quality
- calibration
- robustness

## 23. Engineering principles

### Reproducibility
Every run must have:
- run_id
- random seed
- code version
- dataset version
- feature version
- configuration version
- environment version

### Lineage
Prediction must be traceable to:
entity → layer → ensemble → model → feature set → dataset version.

### Idempotency
Jobs must be safely retryable.

### Determinism
Use explicit seeds where possible.

### Isolation
A failed candidate model must not kill the layer run.

### Resource limits
Each layer run gets CPU/RAM/time/model-count budgets.

### Security
Tenant data must never cross tenant boundaries.

### Human governance
Allow approval and override for consequential actions.

## 24. What makes it technically different

The defensible system is the combination of:

1. Semantic business layers
2. Automated feature-space exploration
3. Model-space exploration
4. Diversity-aware model selection
5. Layer-level ensemble state
6. Cross-layer decision graph
7. Action-specific prediction
8. Outcome/reaction prediction
9. Counterfactual/causal evidence
10. Scenario simulation
11. Decision optimization
12. Real-world feedback
13. Full lineage and evaluation

The moat is the accumulated decision/outcome data and the orchestration/evaluation system, not the existence of a particular algorithm.

## 25. Anti-patterns

Do NOT:
- assume 100 models beats one model
- use random splits on temporal problems without justification
- leak future data
- train stacking models on in-sample predictions
- claim causal effects from correlations
- optimize only accuracy for imbalanced problems
- optimize only revenue while ignoring margin/cost
- hide model disagreement
- make predictions without confidence/quality metadata
- build a huge microservice system before proving one vertical slice
- build dashboards before the underlying state/provenance is correct
- call an ensemble “independent” if all models use nearly identical data and features

## 26. Definition of done for MVP

A customer dataset can be connected.

A business user/engineer can define a target and horizon.

Decision.ai can automatically:
- profile the dataset
- create valid feature candidates
- generate hundreds of candidate configurations
- train/evaluate them
- reject leakage/invalid candidates
- retain strong/diverse models
- create a calibrated ensemble
- create an intelligence state
- expose prediction + evidence through API
- store full lineage
- reproduce the result

Then a second layer can consume that state.

## 27. Success metrics for the product

Technical:
- time-to-first-valid-model
- time-to-layer-state
- predictive metrics
- calibration
- robustness
- model diversity
- reproducibility
- compute efficiency

Business:
- time saved vs conventional workflow
- incremental revenue
- incremental margin
- conversion uplift
- churn reduction
- cost reduction
- decision latency
- percentage of recommendations accepted
- recommendation lift vs baseline
- realized vs predicted value

The strongest proof of disruption is not “we trained more models.” It is:

**same or better decision quality + dramatically shorter time + measurable business outcome.**

## 28. Long-term vision

Decision.ai should eventually become a business decision operating layer:

Observe → Understand → Predict → Recommend → Simulate → Decide → Act → Measure → Learn.

It can expand from marketing/customer use cases into sales, pricing, operations and other business functions, while keeping the same underlying intelligence/decision graph architecture.
