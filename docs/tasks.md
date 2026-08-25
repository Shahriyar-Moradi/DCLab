# Tasks

A task YAML defines entity, prediction time, horizon, target column, feature groups, metric, and validation strategy.

| File | Type | Target |
|------|------|--------|
| `configs/tasks/purchase.yaml` | binary | `purchase_within_60d` |
| `configs/tasks/revenue.yaml` | regression | `revenue_60d` |
| `configs/tasks/customer_value.yaml` | regression | `customer_value_90d` (future 90d spend) |
| `configs/tasks/next_purchase.yaml` | regression | `days_to_next_purchase` |
| `configs/tasks/marketing_response.yaml` | binary | MQL conversion when timestamps allow |

`time_to_event` is implemented as capped-days regression. Survival models are a later extension.

Marketing response is **only** trained when MQL `first_contact_date` and a later won/closed timestamp exist. Otherwise `dclab experiment run --dataset olist --task marketing_response` prints a limitation and exits 0 — it does not invent a leaked label.

