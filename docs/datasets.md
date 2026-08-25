# Datasets

Supported sources now: CSV and Parquet. Future: warehouses and object storage behind the same `load_table` interface.

Each dataset row stores `source_type`, `location`, `version`, inferred schema, row/column counts. Profiles are JSON and can be regenerated.

Olist:

```bash
python scripts/fetch_olist.py
# writes data/olist/raw/*.csv (gitignored)
dclab experiment run --dataset olist --task purchase_prediction
```

The adapter builds `data/olist/analytical/customer_snapshot.csv` as **customer × as-of date** snapshots. Olist `customer_id` is per order; the entity is `customer_unique_id`. Features use orders at or before the cutoff; targets use later orders for that person. Default as-of dates are 2017-09-01, 2017-12-01, 2018-03-01, and 2018-06-01 so a time split is possible.

Marketing files (`olist_marketing_qualified_leads_dataset.csv` + closed deals) are optional. If timestamps cannot support a point-in-time MQL→won label, the marketing task is skipped rather than trained on a leaked target.

