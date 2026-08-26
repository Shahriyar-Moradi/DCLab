"""Load, validate, and list the fixed case-study registry.

Each case study is one YAML file under ``configs/case_studies/``. This module
is the single place that knows the full battery and how to turn a YAML file
into a validated ``CaseStudyConfig`` — every later benchmark step (baseline
runner, DCLab runner, decision impact, calibration, segments, scorecard)
loads case studies through here, never by reading YAML directly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from pydantic import ValidationError

from benchmarks.case_studies.schema import CaseStudyConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "case_studies"

# The fixed battery from the case-study registry table in the build doc.
# Order matters for --list output and for Step 6's scorecard row order.
CASE_STUDY_IDS: tuple[str, ...] = (
    "purchase_prediction",
    "reactivation",
    "customer_value",
    "lead_conversion",
    "upsell_crosssell",
    "campaign_response",
)


class CaseStudyRegistryError(RuntimeError):
    """Raised when a case study config file is missing or fails validation."""


def _config_path(case_study_id: str) -> Path:
    return CONFIG_DIR / f"{case_study_id}.yaml"


def load_case_study(case_study_id: str) -> CaseStudyConfig:
    if case_study_id not in CASE_STUDY_IDS:
        raise CaseStudyRegistryError(
            f"unknown case study id {case_study_id!r}; known ids: {', '.join(CASE_STUDY_IDS)}"
        )
    path = _config_path(case_study_id)
    if not path.exists():
        raise CaseStudyRegistryError(f"missing config file for case study {case_study_id!r}: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    try:
        config = CaseStudyConfig.model_validate(raw)
    except ValidationError as exc:
        raise CaseStudyRegistryError(f"{path} failed schema validation:\n{exc}") from exc
    if config.id != case_study_id:
        raise CaseStudyRegistryError(
            f"{path} declares id={config.id!r}, expected {case_study_id!r} (filename must match id)"
        )
    return config


def list_case_studies() -> list[CaseStudyConfig]:
    return [load_case_study(case_study_id) for case_study_id in CASE_STUDY_IDS]


def _print_table(configs: list[CaseStudyConfig]) -> None:
    rows = [
        (
            c.id,
            c.data_source.kind,
            c.target.task_type,
            c.target.target_column,
            str(c.target.horizon_days),
            "yes" if c.honesty_note else "",
        )
        for c in configs
    ]
    headers = ("id", "data_source", "task_type", "target_column", "horizon_days", "honesty_note")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]

    def _fmt(values: tuple[str, ...] | list[str]) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(values, widths))

    print(_fmt(headers))
    print(_fmt(["-" * w for w in widths]))
    for row in rows:
        print(_fmt(row))
    print(f"\n{len(configs)} case studies, all valid.")
    for c in configs:
        if c.honesty_note:
            print(f"\n[{c.id}] honesty_note: {c.honesty_note}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.case_studies.registry")
    parser.add_argument("--list", action="store_true", help="load, validate, and print every case study")
    parser.add_argument("--id", help="load and print one case study's full validated config as JSON")
    args = parser.parse_args()

    if args.id:
        config = load_case_study(args.id)
        print(config.model_dump_json(indent=2))
        return

    # Default (and --list): validate and print the whole registry.
    configs = list_case_studies()
    _print_table(configs)


if __name__ == "__main__":
    main()
