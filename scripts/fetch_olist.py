#!/usr/bin/env python3
"""Download Olist CSVs into data/olist/raw/. Run from the repository root.

The official host is Kaggle (requires an account). Public git mirrors are
tried first so a lab machine can fetch without Kaggle credentials.
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEST = REPO_ROOT / "data" / "olist" / "raw"
MIRRORS = (
    "https://github.com/andresionek91/brazilian-e-commerce/archive/refs/heads/master.zip",
    "https://github.com/Ganesh7699/Brazilian-E-Commerce-OList/archive/refs/heads/main.zip",
)
KAGGLE = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"


def fetch(dest: Path | None = None) -> Path:
    dest = Path(dest or DEST)
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / "olist_customers_dataset.csv"
    if marker.exists():
        print(f"Olist already present at {dest}")
        return dest
    last_error: Exception | None = None
    zip_path = dest / "olist.zip"
    for url in MIRRORS:
        print(f"Trying {url}")
        try:
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(dest)
            for csv in dest.rglob("*.csv"):
                target = dest / csv.name
                if csv.resolve() != target.resolve():
                    csv.replace(target)
            zip_path.unlink(missing_ok=True)
            if marker.exists():
                print("Olist raw files ready:", dest)
                return dest
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"Mirror failed: {exc}")
            zip_path.unlink(missing_ok=True)
    print(
        "Automatic download failed. Place the official Olist CSVs in "
        f"{dest} (olist_customers_dataset.csv, olist_orders_dataset.csv, ...).\n"
        f"Kaggle: {KAGGLE}\n"
        f"Last error: {last_error}"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    fetch()
    raise SystemExit(0)
