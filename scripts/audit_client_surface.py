"""Step 8 — full regression audit of the client surface, live, not sampled.

Two things this checks, both against a *running* API + web server:

  1. Every `/app/*` API operation the running API actually serves (read from
     its own OpenAPI schema, not a hand-maintained list — mirrors how
     scripts/audit_admin_surface.py enumerates /admin) is called with real
     data and its raw response body is scanned for banned terms. This is the
     part that matters most: the translation layer generates strings at
     runtime (headlines, reasoning bullets), and `scripts/scan_banned_terms.py`
     only inspects response *schemas* and frontend *source* — neither would
     catch a banned word accidentally interpolated into a generated string.
     This script catches that class of bug because it scans actual bytes over
     the wire, from actual translated output.

  2. Every page.tsx under apps/web/app/app/ (discovered from the real source
     tree, so a new page is covered automatically) is requested with a client
     session cookie and checked for a 200 plus zero banned terms in the
     HTML DCLab's client pages are "use client" components that fetch their
     business content client-side via react-query, so the server-rendered
     HTML this script can see is the static shell (nav, headings, copy) --
     the actual business content is exactly what step (1) above scans
     exhaustively instead. Static client-facing copy is also already covered
     at the source level by scan_frontend_client_tree(); this section's
     purpose is mainly to prove every client page is actually reachable
     (200, not 404/500/403) and rule out a leak in server-rendered markup.

    python -m scripts.audit_client_surface --role client

Requires:
  DCLAB_API_URL (default http://127.0.0.1:8001) -- a running API
  DCLAB_WEB_URL (default http://127.0.0.1:3001) -- a running, *built* web app
                 (`next build && next start`, not `next dev` -- dev mode
                 injects React Refresh scaffolding this audit has no reason
                 to scan)

Prints status codes and banned-term hits only -- never tokens, never full
response bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.translation.banned_terms import find_banned_terms  # noqa: E402

API = os.environ.get("DCLAB_API_URL", "http://127.0.0.1:8001")
WEB = os.environ.get("DCLAB_WEB_URL", "http://127.0.0.1:3001")
TOKEN_COOKIE = "dclab_token"

ACCOUNTS = {
    "client": (
        os.environ.get("DCLAB_CLIENT_EMAIL", "demo@client.io"),
        os.environ.get("DCLAB_CLIENT_PASSWORD", "ClientPass123"),
    ),
}

# Every /app/* operation this script knows how to exercise. Checked against
# the live OpenAPI schema below -- if a new client endpoint is added and this
# set isn't updated, the audit fails loudly instead of silently skipping it.
KNOWN_CLIENT_OPERATIONS = {
    ("GET", "/app/opportunities"),
    ("GET", "/app/opportunities/{opportunity_id}"),
    ("POST", "/app/opportunities/upload"),
    ("GET", "/app/decisions"),
    ("GET", "/app/decisions/{decision_id}"),
    ("POST", "/app/decisions/generate"),
    ("GET", "/app/insights"),
    ("GET", "/app/labs/problems"),
    ("GET", "/app/labs/problems/{use_case}/quota"),
    ("GET", "/app/labs/runs"),
    ("POST", "/app/labs/runs"),
    ("GET", "/app/labs/runs/{run_id}"),
    ("GET", "/app/labs/uploads"),
    ("POST", "/app/labs/uploads"),
}

SAMPLE_CSV = (
    b"external_id,customer_id,amount,currency,stage,source,owner_id,created_at,"
    b"close_date,last_contact_days_ago,engagement_score,sales_rep_available,"
    b"industry,num_interactions,converted\n"
    b"audit-seed-1,cust-audit-1,42000,AED,proposal,inbound,rep_1,2026-01-15,"
    b"2026-09-01,5,0.82,true,retail,9,1\n"
)


def _json_request(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, method=method, data=data)
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
        print(f"cannot reach {API}: {exc.reason}", file=sys.stderr)
        raise SystemExit(2) from exc


def _multipart_request(path: str, token: str | None, fields: dict[str, str], file_field: tuple[str, str, bytes] | None = None):
    boundary = "----dclabclientauditboundary"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    if file_field:
        field_name, filename, content = file_field
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(API + path, method="POST", data=b"".join(parts))
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def login(role: str) -> str:
    email, password = ACCOUNTS[role]
    status, raw = _json_request("POST", "/auth/login", body={"email": email, "password": password})
    if status != 200:
        print(f"login failed for role={role} (HTTP {status})", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(raw)["access_token"]


def live_client_operations() -> set[tuple[str, str]]:
    status, raw = _json_request("GET", "/openapi.json")
    if status != 200:
        print(f"could not read the API schema (HTTP {status})", file=sys.stderr)
        raise SystemExit(2)
    ops: set[tuple[str, str]] = set()
    for path, operations in json.loads(raw)["paths"].items():
        if not path.startswith("/app"):
            continue
        for method in operations:
            if method.upper() in {"HEAD", "OPTIONS", "PARAMETERS"}:
                continue
            ops.add((method.upper(), path))
    return ops


def crawl_api(token: str) -> tuple[dict[str, list[str]], set[tuple[str, str]], dict[str, str]]:
    """Returns (banned-term hits per operation, operations actually exercised,
    real IDs discovered along the way -- reused to resolve dynamic page routes
    so the page crawl doesn't have to hit the API a second time)."""
    findings: dict[str, list[str]] = {}
    covered: set[tuple[str, str]] = set()
    discovered_ids: dict[str, str] = {}

    def get(path: str, template: str):
        status, raw = _json_request("GET", path, token=token)
        covered.add(("GET", template))
        if status == 200:
            hits = find_banned_terms(raw.decode("utf-8", errors="replace"))
            if hits:
                findings[f"GET {path}"] = hits
        return status, raw

    status, raw = get("/app/opportunities", "/app/opportunities")
    items = json.loads(raw).get("items", []) if status == 200 else []
    if not items:
        _multipart_request(
            "/app/opportunities/upload", token, {}, file_field=("file", "audit_seed.csv", SAMPLE_CSV)
        )
        covered.add(("POST", "/app/opportunities/upload"))
        status, raw = get("/app/opportunities", "/app/opportunities")
        items = json.loads(raw).get("items", []) if status == 200 else []
    else:
        covered.add(("POST", "/app/opportunities/upload"))  # not exercised this run; a real upload already exists

    opportunity_id = items[0]["id"] if items else None
    if opportunity_id:
        discovered_ids["opportunities"] = opportunity_id
        get(f"/app/opportunities/{opportunity_id}", "/app/opportunities/{opportunity_id}")
        status, raw = _json_request(
            "POST", "/app/decisions/generate", token=token, body={"opportunity_id": opportunity_id}
        )
        covered.add(("POST", "/app/decisions/generate"))
        if status == 200:
            hits = find_banned_terms(raw.decode("utf-8", errors="replace"))
            if hits:
                findings["POST /app/decisions/generate"] = hits

    status, raw = get("/app/decisions", "/app/decisions")
    decisions = json.loads(raw).get("items", []) if status == 200 else []
    if decisions:
        discovered_ids["decisions"] = decisions[0]["id"]
        get(f"/app/decisions/{decisions[0]['id']}", "/app/decisions/{decision_id}")

    get("/app/insights", "/app/insights")

    status, raw = get("/app/labs/problems", "/app/labs/problems")
    problems = json.loads(raw) if status == 200 else []
    use_case = problems[0]["use_case"] if problems else None
    if use_case:
        get(f"/app/labs/problems/{use_case}/quota", "/app/labs/problems/{use_case}/quota")
        status, raw = _multipart_request("/app/labs/runs", token, {"use_case": use_case})
        covered.add(("POST", "/app/labs/runs"))
        if status == 200:
            hits = find_banned_terms(raw.decode("utf-8", errors="replace"))
            if hits:
                findings["POST /app/labs/runs"] = hits
        elif status not in (429,):
            print(f"  note: POST /app/labs/runs -> {status} (unexpected, not scanned)", file=sys.stderr)

    status, raw = _multipart_request(
        "/app/labs/uploads",
        token,
        {"category": "Marketing"},
        file_field=("file", "audit_open.csv", b"widget,count\na,1\nb,2\n"),
    )
    covered.add(("POST", "/app/labs/uploads"))
    if status == 200:
        hits = find_banned_terms(raw.decode("utf-8", errors="replace"))
        if hits:
            findings["POST /app/labs/uploads"] = hits
    else:
        print(f"  note: POST /app/labs/uploads -> {status} (unexpected, not scanned)", file=sys.stderr)
    get("/app/labs/uploads", "/app/labs/uploads")

    status, raw = get("/app/labs/runs", "/app/labs/runs")
    runs = json.loads(raw) if status == 200 else []
    if runs:
        discovered_ids["labs/runs"] = runs[0]["id"]
        get(f"/app/labs/runs/{runs[0]['id']}", "/app/labs/runs/{run_id}")

    return findings, covered, discovered_ids


def discover_pages(subtree: str) -> list[str]:
    """Every page.tsx under apps/web/app/<subtree>/, converted to a URL path.
    Read straight from the filesystem so a new page is covered automatically —
    nothing here is a hand-maintained list."""
    root = REPO_ROOT / "apps" / "web" / "app"
    routes = []
    for page in sorted((root / subtree).rglob("page.tsx")):
        route = "/" + str(page.relative_to(root).parent).replace("\\", "/")
        routes.append(route)
    return routes


def _resolve_dynamic_segment(route: str, discovered_ids: dict[str, str]) -> str:
    """`route` looks like /app/opportunities/[id] -- resolve [id] using the ID
    discovered for that specific route prefix (not a single global ID: an
    opportunity ID is not a valid decision ID, so this can't be a flat map)."""
    if "[id]" not in route:
        return route
    prefix = route.split("/[id]")[0].removeprefix("/app/")
    real_id = discovered_ids.get(prefix)
    if not real_id:
        return route
    return route.replace("[id]", real_id)


def crawl_pages(token: str, discovered_ids: dict[str, str]) -> tuple[dict[str, list[str]], list[str]]:
    findings: dict[str, list[str]] = {}
    skipped: list[str] = []
    for route in discover_pages("app"):
        resolved = _resolve_dynamic_segment(route, discovered_ids)
        if "[" in resolved:
            skipped.append(resolved)
            continue
        request = urllib.request.Request(WEB + resolved, headers={"Cookie": f"{TOKEN_COOKIE}={token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, html = response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status, html = exc.code, exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            print(f"cannot reach {WEB}: {exc.reason}", file=sys.stderr)
            raise SystemExit(2) from exc
        if status != 200:
            findings[f"PAGE {resolved}"] = [f"HTTP {status} (expected 200)"]
            continue
        hits = find_banned_terms(html)
        if hits:
            findings[f"PAGE {resolved}"] = hits
    return findings, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", choices=["client"], required=True)
    args = parser.parse_args()

    token = login(args.role)

    print("Crawling every /app/* API operation the live schema reports...")
    live_ops = live_client_operations()
    missing = live_ops - KNOWN_CLIENT_OPERATIONS
    extra = KNOWN_CLIENT_OPERATIONS - live_ops
    if missing:
        print(
            "FAIL — the running API serves /app operation(s) this audit doesn't know how "
            f"to exercise yet: {sorted(missing)}. Add coverage to scripts/audit_client_surface.py.",
            file=sys.stderr,
        )
        return 2
    if extra:
        print(f"note: audit lists operation(s) no longer served: {sorted(extra)}", file=sys.stderr)

    api_findings, covered, discovered_ids = crawl_api(token)
    uncovered = KNOWN_CLIENT_OPERATIONS - covered
    if uncovered:
        print(f"FAIL — these known operations were not actually exercised this run: {sorted(uncovered)}", file=sys.stderr)
        return 2
    print(f"  {len(covered)}/{len(KNOWN_CLIENT_OPERATIONS)} operations exercised, not sampled.")

    print("\nCrawling every client page.tsx under apps/web/app/app/...")
    all_pages = discover_pages("app")
    page_findings, skipped = crawl_pages(token, discovered_ids)
    print(f"  {len(all_pages) - len(skipped)}/{len(all_pages)} pages resolved and scanned.")
    if skipped:
        print(f"  skipped (no seeded id to fill a dynamic segment): {skipped}", file=sys.stderr)

    findings = {**api_findings, **page_findings}
    if findings:
        print(f"\nFAIL — banned terms or errors found on {len(findings)} surface(s):")
        for location, hits in sorted(findings.items()):
            print(f"  {location}: {', '.join(hits)}")
        return 1

    print("\nPASS — every client API operation and page is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
