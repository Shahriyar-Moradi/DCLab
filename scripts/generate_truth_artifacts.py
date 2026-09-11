"""Generate and verify the canonical DCLab repository-truth artifacts.

    python -m scripts.generate_truth_artifacts
    python -m scripts.generate_truth_artifacts --check
    python -m scripts.generate_truth_artifacts --verify-idempotent
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.truth_drift import (
    REPO_ROOT,
    check_generated_artifacts,
    generated_file_contents,
    write_snapshots,
)


def verify_idempotent(*, output_root: Path = REPO_ROOT) -> list[str]:
    first = write_snapshots(output_root=output_root)
    first_bytes = {
        rel: (output_root / rel).read_bytes() for rel in sorted(first)
    }
    second = write_snapshots(output_root=output_root)
    changed = [
        rel
        for rel in sorted(second)
        if (output_root / rel).read_bytes() != first_bytes[rel]
    ]
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Read only: fail if any checked artifact differs from regeneration.",
    )
    mode.add_argument(
        "--verify-idempotent",
        action="store_true",
        help="Write twice and fail if the second generation changes any byte.",
    )
    args = parser.parse_args()

    if args.check:
        report = check_generated_artifacts()
        if report.ok:
            print("[clean] generated truth artifacts are current")
            return 0
        for problem in report.problems:
            print(f"[FAIL] {problem}")
        return 1

    if args.verify_idempotent:
        changed = verify_idempotent()
        if changed:
            print("[FAIL] second generation changed: " + ", ".join(changed))
            return 1
        print("[clean] two generations are byte-identical")
        return 0

    files = write_snapshots(files=generated_file_contents())
    for rel in sorted(files):
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
