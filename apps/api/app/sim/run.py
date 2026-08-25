"""CLI: python -m app.sim.run [use_case|all]"""

from __future__ import annotations

import json
import sys

from app.sim import USE_CASES
from app.sim.generate import write_all
from app.sim.runner import run_all, run_use_case


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg in {"generate", "data"}:
        write_all()
        return
    if arg == "all":
        results = run_all()
        for payload in results:
            _print_summary(payload)
        return
    if arg not in USE_CASES:
        raise SystemExit(f"Unknown use case {arg!r}. Choose from {USE_CASES} or 'all'")
    payload = run_use_case(arg)
    _print_summary(payload)
    print(json.dumps({k: payload[k] for k in ("use_case", "fusion", "comparison", "heroes")}, indent=2, default=str))


def _print_summary(payload: dict) -> None:
    fusion = payload["fusion"]
    metrics = payload["metrics"]
    comparison = payload["comparison"]
    print(
        f"{payload['use_case']}: {fusion} ROC-AUC={metrics['roc_auc']:.4f} "
        f"profit fusion={comparison['fusion']['profit_vs_do_nothing']} "
        f"single={comparison['best_single']['profit_vs_do_nothing']} "
        f"naive={comparison['naive']['profit_vs_do_nothing']} "
        f"oracle={comparison['oracle']['profit_vs_do_nothing']}"
    )


if __name__ == "__main__":
    main()
