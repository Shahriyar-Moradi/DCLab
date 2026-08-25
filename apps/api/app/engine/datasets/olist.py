"""Olist adapter: raw CSVs → cleaned tables → customer-level analytical snapshot.

The engine never imports this module. Task YAML names columns, not 'olist'.

Olist's ``customer_id`` is unique per *order*. Repeat buyers are joined on
``customer_unique_id``. Snapshots are customer × as-of date: features use orders
at or before the cutoff; labels use later orders only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from app.config import REPO_ROOT

RAW = REPO_ROOT / "data" / "olist" / "raw"
CLEANED = REPO_ROOT / "data" / "olist" / "cleaned"
ANALYTICAL = REPO_ROOT / "data" / "olist" / "analytical"

FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "geo": "olist_geolocation_dataset.csv",
    "mql": "olist_marketing_qualified_leads_dataset.csv",
    "closed_deals": "olist_closed_deals_dataset.csv",
}

# Several as-of dates so a time split has train < val < test.
DEFAULT_AS_OF_DATES = ("2017-09-01", "2017-12-01", "2018-03-01", "2018-06-01")


def raw_available(root: Path | None = None) -> bool:
    base = Path(root or RAW)
    return (base / FILES["customers"]).exists() and (base / FILES["orders"]).exists()


def load_raw(name: str, root: Path | None = None) -> pd.DataFrame:
    path = Path(root or RAW) / FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Olist file missing: {path}")
    return pd.read_csv(path)


def _tables(raw_root: Path | None) -> dict[str, pd.DataFrame]:
    customers = load_raw("customers", raw_root)
    orders = load_raw("orders", raw_root)
    items = load_raw("items", raw_root)
    payments = load_raw("payments", raw_root)
    orders["order_purchase_timestamp"] = pd.to_datetime(orders["order_purchase_timestamp"], errors="coerce")
    orders = orders.merge(
        customers[["customer_id", "customer_unique_id"]],
        on="customer_id",
        how="left",
    )
    pay = payments.groupby("order_id")["payment_value"].sum()
    orders = orders.merge(pay.rename("payment_value"), left_on="order_id", right_index=True, how="left")
    try:
        reviews = load_raw("reviews", raw_root)
        reviews["review_creation_date"] = pd.to_datetime(reviews["review_creation_date"], errors="coerce")
    except FileNotFoundError:
        reviews = pd.DataFrame()
    dim = customers.drop_duplicates("customer_unique_id")
    return {
        "customers": dim,
        "orders": orders,
        "items": items,
        "reviews": reviews,
    }


def _snapshot_at(tables: dict[str, pd.DataFrame], cutoff: pd.Timestamp) -> pd.DataFrame:
    orders = tables["orders"]
    items = tables["items"]
    reviews = tables["reviews"]
    customers = tables["customers"]
    past = orders[orders["order_purchase_timestamp"] <= cutoff]
    future = orders[orders["order_purchase_timestamp"] > cutoff]
    future_60 = future[future["order_purchase_timestamp"] <= cutoff + pd.Timedelta(days=60)]
    future_90 = future[future["order_purchase_timestamp"] <= cutoff + pd.Timedelta(days=90)]

    agg = past.groupby("customer_unique_id").agg(
        order_count=("order_id", "nunique"),
        total_spend=("payment_value", "sum"),
        last_order=("order_purchase_timestamp", "max"),
        first_order=("order_purchase_timestamp", "min"),
    )
    if agg.empty:
        return pd.DataFrame()
    agg["avg_order_value"] = agg["total_spend"] / agg["order_count"].clip(lower=1)
    agg["days_since_last_order"] = (cutoff - agg["last_order"]).dt.days
    agg["lifetime_days"] = (agg["last_order"] - agg["first_order"]).dt.days.clip(lower=0)

    items_past = items.merge(past[["order_id", "customer_unique_id"]], on="order_id", how="inner")
    product_stats = items_past.groupby("customer_unique_id").agg(
        item_count=("order_item_id", "count"),
        unique_products=("product_id", "nunique"),
        unique_sellers=("seller_id", "nunique"),
        avg_price=("price", "mean"),
    )

    if len(reviews):
        reviews_past = reviews[reviews["review_creation_date"] <= cutoff]
        reviews_past = reviews_past.merge(past[["order_id", "customer_unique_id"]], on="order_id", how="inner")
        review_stats = reviews_past.groupby("customer_unique_id").agg(
            review_count=("review_id", "count"),
            avg_review_score=("review_score", "mean"),
            latest_review_score=("review_score", "last"),
        )
    else:
        review_stats = pd.DataFrame()

    snap = customers.merge(agg, on="customer_unique_id", how="inner")
    snap = snap.merge(product_stats, on="customer_unique_id", how="left")
    if len(review_stats):
        snap = snap.merge(review_stats, on="customer_unique_id", how="left")
    else:
        snap["review_count"] = 0
        snap["avg_review_score"] = 0.0
        snap["latest_review_score"] = 0.0

    bought_60 = set(future_60["customer_unique_id"].dropna())
    snap["purchase_within_60d"] = snap["customer_unique_id"].isin(bought_60).astype(int)
    rev60 = future_60.groupby("customer_unique_id")["payment_value"].sum()
    rev90 = future_90.groupby("customer_unique_id")["payment_value"].sum()
    snap["revenue_60d"] = snap["customer_unique_id"].map(rev60).fillna(0.0)
    snap["customer_value_90d"] = snap["customer_unique_id"].map(rev90).fillna(0.0)
    next_order = future.groupby("customer_unique_id")["order_purchase_timestamp"].min()
    days = (next_order - cutoff).dt.days
    snap["days_to_next_purchase"] = snap["customer_unique_id"].map(days).fillna(365).clip(0, 365)
    snap["as_of_date"] = cutoff
    snap["entity_id"] = snap["customer_unique_id"]
    if "customer_state" in snap.columns:
        snap["customer_state_code"] = snap["customer_state"].astype("category").cat.codes
    if "customer_zip_code_prefix" in snap.columns:
        snap["customer_zip_prefix"] = pd.to_numeric(snap["customer_zip_code_prefix"], errors="coerce").fillna(0)
    if "unique_products" in snap.columns:
        snap["category_count"] = snap["unique_products"]
    if "avg_review_score" in snap.columns:
        snap["review_score"] = snap["avg_review_score"]
    numeric = [
        "order_count",
        "total_spend",
        "avg_order_value",
        "days_since_last_order",
        "lifetime_days",
        "item_count",
        "unique_products",
        "unique_sellers",
        "avg_price",
        "review_count",
        "avg_review_score",
        "latest_review_score",
        "category_count",
        "review_score",
        "customer_state_code",
        "customer_zip_prefix",
    ]
    for col in numeric:
        if col in snap.columns:
            snap[col] = pd.to_numeric(snap[col], errors="coerce").fillna(0.0)
    return snap


def build_analytical(
    as_of: str | None = "2018-06-01",
    raw_root: Path | None = None,
    as_of_dates: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Customer × as-of snapshots. Labels use only later orders for that person."""
    dates = [pd.Timestamp(d) for d in (as_of_dates or ([as_of] if as_of else DEFAULT_AS_OF_DATES))]
    tables = _tables(raw_root)
    frames = [_snapshot_at(tables, cutoff) for cutoff in dates]
    frames = [frame for frame in frames if len(frame)]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def write_analytical(
    as_of: str | None = None,
    raw_root: Path | None = None,
    as_of_dates: Sequence[str] | None = None,
) -> Path:
    ANALYTICAL.mkdir(parents=True, exist_ok=True)
    CLEANED.mkdir(parents=True, exist_ok=True)
    dates = as_of_dates or (None if as_of else DEFAULT_AS_OF_DATES)
    frame = build_analytical(as_of=as_of, raw_root=raw_root, as_of_dates=dates)
    out = ANALYTICAL / "customer_snapshot.csv"
    frame.to_csv(out, index=False)
    return out


def marketing_frame(raw_root: Path | None = None) -> pd.DataFrame | None:
    """MQL → closed deal, only if both files exist and timestamps are usable."""
    try:
        mql = load_raw("mql", raw_root)
        closed = load_raw("closed_deals", raw_root)
    except FileNotFoundError:
        return None
    if "first_contact_date" not in mql.columns or "mql_id" not in mql.columns:
        return None
    if "won_date" not in closed.columns:
        return None
    mql["first_contact_date"] = pd.to_datetime(mql["first_contact_date"], errors="coerce")
    closed["won_date"] = pd.to_datetime(closed["won_date"], errors="coerce")
    if mql["first_contact_date"].isna().all() or closed["won_date"].notna().sum() == 0:
        return None
    merged = mql.merge(closed, on="mql_id", how="left", suffixes=("", "_deal"))
    # Won after first contact only — otherwise the label is not point-in-time.
    won_later = merged["won_date"].notna() & (merged["won_date"] > merged["first_contact_date"])
    merged["target"] = won_later.astype(int)
    merged["entity_id"] = merged["mql_id"]
    merged["as_of_date"] = merged["first_contact_date"]
    keep = [col for col in ("origin", "landing_page_id", "entity_id", "as_of_date", "target") if col in merged.columns]
    frame = merged[keep].copy()
    if "origin" in frame.columns:
        frame["origin_code"] = frame["origin"].astype("category").cat.codes
    else:
        frame["origin_code"] = 0
    frame["has_landing_page"] = (
        frame["landing_page_id"].notna().astype(int) if "landing_page_id" in frame.columns else 0
    )
    return frame.dropna(subset=["as_of_date"])
