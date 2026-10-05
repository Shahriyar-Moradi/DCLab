# dclab-client

Typed synchronous Python SDK for the stable DCLab `/v1` API (`DCLabClient`) and
the customer CLI. Talks only to `/v1` over HTTP; no server imports.

```python
from dclab_client import DCLabClient

with DCLabClient("https://dclab.example/api/backend", token="dclab_st_...") as api:
    project = api.projects.create(name="Churn")
    upload = api.datasets.upload(project.id, "rows.csv")
    run = api.experiments.create(project_id=project.id, dataset_id=upload.id, target_column="label")
    # once completed: five plain-language trust checks (leakage, overfit, duplicates, ...)
    for check in api.experiments.findings(run.id).checks:
        print(check.check, check.status, check.message)
    # one-page model card: metric in plain words, baseline, drivers, risks (Markdown too)
    print(api.model_versions.card("<model version id>").markdown)
    # later: score new rows with a model version (the worker scores; poll until terminal)
    rows = api.datasets.upload(project.id, "new_rows.csv", purpose="scoring")
    pred = api.predictions.create(model_version_id="<model version id>", dataset_id=rows.id)
    if api.predictions.get(pred.id).status == "completed":
        api.predictions.download(pred.id, to="predictions.csv")
```

Every `/v1` operation a bearer token can call has an SDK method (the
`/v1/service-tokens` management routes are session-only and intentionally not
in the SDK; `scripts/truth_drift.py` keeps this in sync).

## CLI

Installed as `dclab-cli` and runnable as `python -m dclab_client`. It is
deliberately **not** called `dclab`: the server repo's own console script
`dclab` (`worker run`, `user seed`) lives in the same environment.

```
dclab-cli login --api-url https://... --workspace <id>   # token from stdin or prompt (--token warns)
dclab-cli whoami | logout
dclab-cli projects list | create --name N | get ID
dclab-cli data upload FILE --project ID [--purpose training|scoring]
dclab-cli experiments list | get ID | run --project P --dataset D [--target C] [--intent T]
dclab-cli experiments branch ID --intent T (--changes JSON | --changes-file F)
dclab-cli experiments compare ID ID... | code ID [--notebook] [-o FILE] | findings ID | cancel ID
dclab-cli models card MV [--json [--markdown]]                 # Markdown by default
dclab-cli predict create --model-version MV --dataset D [--format csv|parquet] [--wait [--timeout S]]
dclab-cli predict get ID | download ID -o FILE
dclab-cli decisions list --project P | propose --project P --type T --subject-kind K --rationale R | get ID
```

Add `--json` to any command for machine output on stdout (`code` prints the
script source by default). Commands that create things take
`--idempotency-key` so a retry is safe. `predict create --wait` exits 1 when the
prediction failed and 7 when it is still running at `--timeout`.

Credentials: `login` verifies the token with `GET /v1/me`, then stores
`{token, api_url, workspace}` in `$XDG_CONFIG_HOME/dclab/config.json`
(`~/.config` fallback) with mode 0600. `DCLAB_TOKEN`, `DCLAB_API_URL` and
`DCLAB_WORKSPACE` override the file, with one exception: a stored token is only
sent to the URL it was stored with (a different `DCLAB_API_URL` is refused unless
`DCLAB_TOKEN` is set too). The API URL must be https; plain http is allowed only
for loopback, `*.localhost` and the compose host `api`, or with
`DCLAB_ALLOW_INSECURE_HTTP=1`. The token is never printed; prefer stdin or the
prompt over `--token`, which shows up in shell history. Service
tokens (`dclab_st_...`) need no workspace; user access tokens do.

Errors go to stderr as `error: <code>: <message> (request_id=<id>)` (with
`--json`, the envelope as JSON).

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | generic failure (local file, bad JSON argument, unexpected API status) |
| 2 | usage error (unknown command, missing argument) |
| 3 | authentication / permission (401, 403, not logged in) |
| 4 | not found (404; also another workspace's resource) |
| 5 | conflict or precondition (409, 412, 428, idempotency key reuse) |
| 6 | validation (400, 422) |
| 7 | rate limited, server error or network failure; safe to retry with the same idempotency key |
