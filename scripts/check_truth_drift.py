"""CI entrypoint for S0-P01B repository-truth drift.

    python -m scripts.check_truth_drift
    python -m scripts.check_truth_drift --write-snapshots
    python -m scripts.check_truth_drift --alembic-check
"""

from __future__ import annotations

import argparse
import sys

from scripts.truth_drift import collect_reports, format_reports, write_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-snapshots",
        action="store_true",
        help="Rewrite contracts/*.json from the current tree, then re-check.",
    )
    parser.add_argument(
        "--alembic-check",
        action="store_true",
        help="Also compare SQLAlchemy metadata to DATABASE_URL (after migrate).",
    )
    args = parser.parse_args()
    if args.write_snapshots:
        write_snapshots()
        print("wrote contracts/v1_openapi.json")
        print("wrote contracts/openapi_operations.json")
        print("wrote contracts/sqlalchemy_tables.json")
        print("wrote contracts/truth_baseline.json")
    reports = collect_reports(include_alembic_metadata=args.alembic_check)
    text, failed = format_reports(reports)
    sys.stdout.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
