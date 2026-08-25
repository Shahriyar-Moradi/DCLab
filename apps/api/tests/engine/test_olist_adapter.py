import pandas as pd

from app.engine.datasets.olist import build_analytical, marketing_frame


def _write_raw(root):
    pd.DataFrame(
        {
            "customer_id": ["c1", "c1b", "c2"],
            "customer_unique_id": ["u1", "u1", "u2"],
            "customer_zip_code_prefix": [1000, 1000, 2000],
            "customer_city": ["sao paulo", "sao paulo", "rio"],
            "customer_state": ["SP", "SP", "RJ"],
        }
    ).to_csv(root / "olist_customers_dataset.csv", index=False)
    pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "customer_id": ["c1", "c1b", "c2"],
            "order_status": ["delivered", "delivered", "delivered"],
            "order_purchase_timestamp": ["2018-01-10", "2018-07-01", "2018-03-01"],
        }
    ).to_csv(root / "olist_orders_dataset.csv", index=False)
    pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "order_item_id": [1, 1, 1],
            "product_id": ["p1", "p2", "p1"],
            "seller_id": ["s1", "s1", "s2"],
            "price": [10.0, 20.0, 15.0],
        }
    ).to_csv(root / "olist_order_items_dataset.csv", index=False)
    pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "payment_value": [10.0, 20.0, 15.0],
        }
    ).to_csv(root / "olist_order_payments_dataset.csv", index=False)


def test_analytical_snapshot_is_point_in_time(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_raw(raw)
    frame = build_analytical(as_of="2018-06-01", raw_root=raw)
    assert set(frame["entity_id"]) == {"u1", "u2"}
    c1 = frame.loc[frame["entity_id"] == "u1"].iloc[0]
    c2 = frame.loc[frame["entity_id"] == "u2"].iloc[0]
    assert int(c1["order_count"]) == 1
    assert int(c1["purchase_within_60d"]) == 1
    assert float(c1["revenue_60d"]) == 20.0
    assert int(c2["purchase_within_60d"]) == 0
    assert float(c2["revenue_60d"]) == 0.0


def test_marketing_frame_absent_without_mql(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_raw(raw)
    assert marketing_frame(raw_root=raw) is None


def test_engine_core_does_not_branch_on_olist():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "engine"
    for path in root.rglob("*.py"):
        if "datasets" in path.parts:
            continue
        text = path.read_text()
        assert 'dataset == "olist"' not in text
        assert "dataset == 'olist'" not in text
