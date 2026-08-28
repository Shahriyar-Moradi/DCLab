"""Admin Lab use-case catalog.

Five prediction problems the lab trains from an uploaded CSV. Matching is by
column name (aliases), not by a fixed schema — a file only trains the use cases
it actually has labels for. Other label columns are held out of features so they
cannot leak into a different problem.
"""

from __future__ import annotations

from dataclasses import dataclass

MODELS_PER_USE_CASE = 5

BINARY_FAMILIES = (
    "majority",
    "logistic_regression",
    "random_forest",
    "gradient_boosting",
    "extra_trees",
)
REGRESSION_FAMILIES = (
    "mean",
    "linear_regression",
    "random_forest_regressor",
    "gradient_boosting_regressor",
    "extra_trees_regressor",
)


@dataclass(frozen=True)
class UseCaseDefinition:
    slug: str
    name: str
    description: str
    task_type: str
    evaluation_metric: str
    target_aliases: tuple[str, ...]
    preferred_groups: tuple[str, ...]


LAB_USE_CASES: tuple[UseCaseDefinition, ...] = (
    UseCaseDefinition(
        slug="churn",
        name="Churn",
        description="Will this customer leave? Recency, engagement, and support features.",
        task_type="binary",
        evaluation_metric="roc_auc",
        target_aliases=(
            "churned",
            "churn",
            "is_churn",
            "churn_flag",
            "cancelled",
            "canceled",
            "attrited",
            "attrition",
        ),
        preferred_groups=("recency", "engagement", "support", "product", "temporal"),
    ),
    UseCaseDefinition(
        slug="conversion",
        name="Conversion",
        description="Will this opportunity close? Deal size, stage, and engagement.",
        task_type="binary",
        evaluation_metric="pr_auc",
        target_aliases=(
            "converted",
            "conversion",
            "is_converted",
            "won",
            "is_won",
            "closed_won",
        ),
        preferred_groups=("monetary", "pipeline", "engagement", "acquisition", "temporal"),
    ),
    UseCaseDefinition(
        slug="lead_conversion",
        name="Lead conversion",
        description="Will this lead become a customer? Source, segment, and early engagement.",
        task_type="binary",
        evaluation_metric="pr_auc",
        target_aliases=(
            "lead_converted",
            "is_lead_converted",
            "qualified",
            "is_qualified",
            "mql",
            "sql",
            "converted",
        ),
        preferred_groups=("acquisition", "firmographic", "engagement", "pipeline"),
    ),
    UseCaseDefinition(
        slug="purchase",
        name="Purchase probability",
        description="Will they buy again? Recency, frequency, monetary, and product mix.",
        task_type="binary",
        evaluation_metric="pr_auc",
        target_aliases=(
            "purchase_within_60d",
            "purchased",
            "will_purchase",
            "repeat_purchase",
            "bought",
            "purchase",
        ),
        preferred_groups=("recency", "frequency", "monetary", "product", "engagement"),
    ),
    UseCaseDefinition(
        slug="customer_value",
        name="Customer value",
        description="Expected value of the customer or deal. Historical spend is a feature, not the label, when a dedicated value column exists.",
        task_type="regression",
        evaluation_metric="mae",
        target_aliases=(
            "customer_value_90d",
            "revenue_60d",
            "remaining_arr",
            "expected_value",
            "lifetime_value",
            "ltv",
        ),
        preferred_groups=("frequency", "product", "firmographic", "temporal", "engagement"),
    ),
)

ENTITY_ALIASES = ("entity_id", "customer_id", "account_id", "lead_id", "user_id", "external_id")
TIME_ALIASES = (
    "as_of_date",
    "created_at",
    "event_time",
    "timestamp",
    "close_date",
    "order_purchase_timestamp",
)

# Columns that are identifiers or constants, never model features.
SKIP_EXACT = {
    "currency",
    "email",
    "name",
    "full_name",
    "password",
    "token",
    "owner",
    "owner_id",
    "rep_id",
}

# First matching group wins. More specific tokens first.
GROUP_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("engagement", ("engagement", "interaction", "login", "emails_opened", "emails_clicked", "session", "activity", "marketing")),
    ("recency", ("days_since", "last_contact", "last_login", "recency", "inactive", "days_until")),
    ("frequency", ("_count", "num_", "number_of", "frequency")),
    ("monetary", ("amount", "spend", "revenue", "price", "arr", "payment", "discount", "order_value")),
    ("pipeline", ("stage", "funnel")),
    ("acquisition", ("source", "channel", "campaign", "medium")),
    ("product", ("product", "category", "feature_us", "item_count", "sku", "seller")),
    ("support", ("support", "ticket", "nps", "review", "complaint")),
    ("temporal", ("date", "created", "close", "age", "month", "tenure", "lifetime_days")),
    ("firmographic", ("industry", "segment", "employees", "number_of_users", "company_size", "account_age")),
)


def use_case_by_slug(slug: str) -> UseCaseDefinition | None:
    return next((item for item in LAB_USE_CASES if item.slug == slug), None)


def families_for(task_type: str) -> tuple[str, ...]:
    return BINARY_FAMILIES if task_type == "binary" else REGRESSION_FAMILIES
