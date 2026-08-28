"""Audit the admin surface against a given role.

Walks every admin endpoint the running API actually serves (read from its OpenAPI
schema, so nothing is sampled or hand-listed) and asserts the caller is rejected.

    python -m scripts.audit_admin_surface --role client   # expects all rejections
    python -m scripts.audit_admin_surface --role admin    # expects no rejections

Prints status codes only — never tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
BODY_METHODS = {"POST", "PUT", "PATCH"}

ACCOUNTS = {
    "admin": (
        os.environ.get("DCLAB_ADMIN_EMAIL", "admin@dclab.io"),
        os.environ.get("DCLAB_ADMIN_PASSWORD", "AdminPass123!"),
    ),
    "client": (
        os.environ.get("DCLAB_CLIENT_EMAIL", "demo@client.io"),
        os.environ.get("DCLAB_CLIENT_PASSWORD", "ClientPass123!"),
    ),
}


def _request(base: str, method: str, path: str, token: str | None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, method=method, data=data)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        print(f"cannot reach {base}: {exc.reason}", file=sys.stderr)
        raise SystemExit(2) from exc


def login(base: str, role: str) -> str:
    email, password = ACCOUNTS[role]
    status, raw = _request(base, "POST", "/auth/login", None, {"email": email, "password": password})
    if status != 200:
        print(f"login failed for role={role} (HTTP {status})", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(raw)["access_token"]


def admin_endpoints(base: str) -> list[tuple[str, str]]:
    status, raw = _request(base, "GET", "/openapi.json", None)
    if status != 200:
        print(f"could not read the API schema (HTTP {status})", file=sys.stderr)
        raise SystemExit(2)
    pairs: list[tuple[str, str]] = []
    for path, operations in json.loads(raw)["paths"].items():
        if not path.startswith("/admin"):
            continue
        for method in operations:
            if method.upper() in {"HEAD", "OPTIONS", "PARAMETERS"}:
                continue
            pairs.append((method.upper(), re.sub(r"\{[^}]+\}", PLACEHOLDER, path)))
    return sorted(set(pairs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["client", "admin", "anonymous"], required=True)
    parser.add_argument("--base", default=os.environ.get("DCLAB_API_URL", "http://127.0.0.1:8001"))
    args = parser.parse_args()

    token = None if args.role == "anonymous" else login(args.base, args.role)
    endpoints = admin_endpoints(args.base)
    if not endpoints:
        print("no admin endpoints found — the audit would trivially pass", file=sys.stderr)
        return 2

    expect_rejection = args.role != "admin"
    failures: list[str] = []
    counts: dict[int, int] = {}

    for method, path in endpoints:
        body = {} if method in BODY_METHODS else None
        status, _ = _request(args.base, method, path, token, body)
        counts[status] = counts.get(status, 0) + 1
        rejected = status in {401, 403, 404}
        if expect_rejection and not rejected:
            failures.append(f"{method} {path} -> {status} (expected rejection)")
        if not expect_rejection and status in {401, 403}:
            failures.append(f"{method} {path} -> {status} (admin should be allowed)")

    print(f"role={args.role}  admin endpoints audited: {len(endpoints)}")
    for status in sorted(counts):
        print(f"  HTTP {status}: {counts[status]}")
    if failures:
        print(f"\nFAIL — {len(failures)} endpoint(s) behaved incorrectly:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("\nPASS — every admin endpoint behaved as expected for this role.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
