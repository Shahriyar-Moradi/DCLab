"""CI guardrail: fail the build if ML vocabulary reaches a client-facing surface.

    python -m scripts.scan_banned_terms            # scan everything
    python -m scripts.scan_banned_terms --api-only
    python -m scripts.scan_banned_terms --web-only

Exit code is 0 iff both scans are clean. Add apps/api to PYTHONPATH before running
(the repo's Makefile / CI job does this via `cd apps/api`).
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "apps/api")

from app.translation.scanner import (  # noqa: E402
    scan_client_api_response_models,
    scan_frontend_client_tree,
)


def _report(title: str, violations: dict[str, list[str]]) -> bool:
    if not violations:
        print(f"[clean] {title}")
        return True
    print(f"[FAIL] {title} — banned terms found:")
    for location, terms in sorted(violations.items()):
        print(f"  {location}: {', '.join(terms)}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--web-only", action="store_true")
    args = parser.parse_args()

    clean = True
    if not args.web_only:
        clean &= _report("client API response schemas", scan_client_api_response_models())
    if not args.api_only:
        clean &= _report("client frontend source", scan_frontend_client_tree())

    if not clean:
        print("\nBanned-terms scan failed. Route the offending value through app.translation before it reaches a client surface.")
        return 1
    print("\nBanned-terms scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
