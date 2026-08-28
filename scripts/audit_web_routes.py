"""Check the Next.js route guard: what a browser gets when a URL is typed directly.

Exercises /admin and /app pages as anonymous, client, and admin visitors, using the
same signed cookie the browser would carry. Prints status codes only, never tokens.

    python -m scripts.audit_web_routes
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = os.environ.get("DCLAB_API_URL", "http://127.0.0.1:8001")
WEB = os.environ.get("DCLAB_WEB_URL", "http://127.0.0.1:3001")
TOKEN_COOKIE = "dclab_token"

ADMIN_PAGES = ["/admin/lab", "/admin/lab/datasets", "/admin/lab/experiments", "/admin/lab/tasks"]
CLIENT_PAGES = ["/app/dashboards", "/app/opportunities", "/app/decisions"]

ACCOUNTS = {
    "admin": ("admin@dclab.io", "AdminPass123!"),
    "client": ("demo@client.io", "ClientPass123!"),
}


def login(role: str) -> str:
    email, password = ACCOUNTS[role]
    request = urllib.request.Request(
        f"{API}/auth/login",
        method="POST",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["access_token"]


def visit(path: str, token: str | None) -> tuple[int, str]:
    """Returns (status, location) without following redirects, so a redirect is
    reported as a redirect rather than silently resolving to a 200."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(WEB + path)
    if token:
        request.add_header("Cookie", f"{TOKEN_COOKIE}={token}")
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def main() -> int:
    tokens = {"anonymous": None, "client": login("client"), "admin": login("admin")}
    failures: list[str] = []

    print("ADMIN pages (typed directly into the browser)")
    for role, token in tokens.items():
        for path in ADMIN_PAGES:
            status, location = visit(path, token)
            note = f" -> {location}" if location else ""
            print(f"  {role:<9} {path:<26} HTTP {status}{note}")
            if role == "client" and status != 403:
                failures.append(f"client got {status} on {path}, expected 403")
            if role == "anonymous" and status not in {302, 307}:
                failures.append(f"anonymous got {status} on {path}, expected a login redirect")
            if role == "admin" and status != 200:
                failures.append(f"admin got {status} on {path}, expected 200")

    print("\nCLIENT pages")
    for role, token in tokens.items():
        for path in CLIENT_PAGES:
            status, location = visit(path, token)
            note = f" -> {location}" if location else ""
            print(f"  {role:<9} {path:<26} HTTP {status}{note}")
            if role == "client" and status != 200:
                failures.append(f"client got {status} on {path}, expected 200")
            if role == "anonymous" and status not in {302, 307}:
                failures.append(f"anonymous got {status} on {path}, expected a login redirect")

    if failures:
        print(f"\nFAIL — {len(failures)} problem(s):")
        for line in failures:
            print(f"  {line}")
        return 1
    print("\nPASS — the route guard behaved correctly for every role.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
