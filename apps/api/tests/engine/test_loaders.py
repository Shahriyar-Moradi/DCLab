import pandas as pd

from app.engine.data.loaders import infer_schema, load_table
from app.engine.data.quality import quality_report


def test_csv_and_parquet_roundtrip(tmp_path):
    frame = pd.DataFrame({"a": [1, 2, None], "b": ["x", "y", "x"]})
    csv_path = tmp_path / "t.csv"
    parquet_path = tmp_path / "t.parquet"
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    loaded_csv = load_table(csv_path)
    loaded_parquet = load_table(parquet_path)
    assert list(loaded_csv.columns) == ["a", "b"]
    assert len(loaded_parquet) == 3
    schema = infer_schema(loaded_csv)
    assert schema["row_count"] == 3
    quality = quality_report(loaded_csv, target=None)
    assert quality["issue_count"] >= 1
    assert any(issue["code"] == "missing_values" for issue in quality["issues"])
    assert len(loaded_csv) == 3
