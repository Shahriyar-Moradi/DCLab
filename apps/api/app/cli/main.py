"""Internal DCLab CLI: dclab dataset|task|experiment ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import REPO_ROOT
from app.db.session import get_session_factory
from app.engine.datasets.olist import marketing_frame, raw_available, write_analytical
from app.engine.types import SearchConfig
from app.services.lab_service import (
    create_experiment,
    execute_experiment,
    ingest_dataset,
    ingest_synthetic,
    profile_dataset,
    search_from_yaml,
    seed_dogfood,
    task_from_yaml,
    upsert_task,
)


def _session():
    return get_session_factory()()


def cmd_user_create(args: argparse.Namespace) -> int:
    from app.db.models import DEFAULT_WORKSPACE_ID, User, UserRole
    from app.services.auth_service import create_user

    db = _session()
    role = UserRole(args.role)
    existing = db.query(User).filter(User.email == args.email.strip().lower()).one_or_none()
    if existing is not None:
        print(json.dumps({"error": "email already exists", "email": existing.email}))
        db.close()
        return 1
    workspace_roles = {
        UserRole.CLIENT_USER,
        UserRole.BUSINESS_ADMIN,
        UserRole.BUSINESS_DEVELOPER,
    }
    workspace_id = DEFAULT_WORKSPACE_ID if role in workspace_roles else None
    user = create_user(
        db,
        email=args.email,
        password=args.password,
        role=role,
        full_name=args.name or "",
        workspace_id=workspace_id,
    )
    db.commit()
    print(json.dumps({"id": str(user.id), "email": user.email, "role": user.role}))
    db.close()
    return 0


def cmd_user_seed(_args: argparse.Namespace) -> int:
    from app.services.auth_service import (
        DEMO_ADMIN_EMAIL,
        DEMO_ADMIN_NAME,
        DEMO_ADMIN_PASSWORD,
        DEMO_CLIENT_EMAIL,
        DEMO_CLIENT_NAME,
        DEMO_CLIENT_PASSWORD,
        ensure_demo_users,
    )

    db = _session()
    users = ensure_demo_users(db)
    db.commit()
    accounts = [
        {"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD, "role": "dclab_admin", "name": DEMO_ADMIN_NAME},
        {"email": DEMO_CLIENT_EMAIL, "password": DEMO_CLIENT_PASSWORD, "role": "client_user", "name": DEMO_CLIENT_NAME},
    ]
    print(
        json.dumps(
            {
                "users": [
                    {"id": str(row.id), "email": row.email, "role": row.role, "full_name": row.full_name}
                    for row in users
                ],
                "logins": accounts,
            }
        )
    )
    db.close()
    return 0


def cmd_env_seed(_args: argparse.Namespace) -> int:
    db = _session()
    env = seed_dogfood(db)
    print(json.dumps({"id": str(env.id), "name": env.name, "org_id": env.org_id}))
    db.close()
    return 0


def cmd_dataset_ingest(args: argparse.Namespace) -> int:
    db = _session()
    env = seed_dogfood(db)
    if args.path == "synthetic":
        row = ingest_synthetic(db, env, n=int(args.n or 2000))
    else:
        row = ingest_dataset(
            db,
            environment=env,
            name=args.name,
            location=args.path,
            source_type="parquet" if args.path.endswith(".parquet") else "csv",
        )
    print(json.dumps({"id": str(row.id), "name": row.name, "rows": row.row_count}))
    db.close()
    return 0


def cmd_dataset_profile(args: argparse.Namespace) -> int:
    db = _session()
    from app.db.models import Dataset

    dataset = db.query(Dataset).filter(Dataset.name == args.dataset).order_by(Dataset.created_at.desc()).first()
    if dataset is None:
        print("dataset not found", file=sys.stderr)
        return 1
    profile = profile_dataset(db, dataset)
    print(json.dumps({"id": str(profile.id), "row_count": profile.stats.get("row_count")}))
    db.close()
    return 0


def cmd_task_create(args: argparse.Namespace) -> int:
    db = _session()
    env = seed_dogfood(db)
    spec = task_from_yaml(Path(args.config))
    row = upsert_task(db, env, spec)
    print(json.dumps({"id": str(row.id), "slug": row.slug}))
    db.close()
    return 0


def cmd_experiment_run(args: argparse.Namespace) -> int:
    db = _session()
    env = seed_dogfood(db)
    from app.db.models import Dataset, PredictionTask

    if args.dataset == "olist":
        if not raw_available():
            print("Olist raw files missing. Run: python scripts/fetch_olist.py", file=sys.stderr)
            return 1
        path = write_analytical()
        dataset = ingest_dataset(db, environment=env, name="olist", location=str(path), source_type="csv")
    elif args.dataset == "synthetic":
        dataset = ingest_synthetic(db, env)
    else:
        dataset = db.query(Dataset).filter(Dataset.name == args.dataset).order_by(Dataset.created_at.desc()).first()
    if dataset is None:
        print("dataset not found", file=sys.stderr)
        return 1

    task_slug = args.task
    config_path = REPO_ROOT / "configs" / "tasks" / f"{task_slug.replace('_prediction', '')}.yaml"
    aliases = {
        "purchase_prediction": "purchase.yaml",
        "revenue_prediction": "revenue.yaml",
        "customer_value": "customer_value.yaml",
        "next_purchase": "next_purchase.yaml",
        "next_purchase_time": "next_purchase.yaml",
        "marketing_response": "marketing_response.yaml",
    }
    if task_slug in aliases:
        config_path = REPO_ROOT / "configs" / "tasks" / aliases[task_slug]
    if config_path.exists():
        spec = task_from_yaml(config_path)
        if args.dataset == "olist" and spec.id == "marketing_response":
            frame = marketing_frame()
            if frame is None:
                print("Olist marketing files do not support a PIT-valid target. Skipping.")
                return 0
            mpath = REPO_ROOT / "data" / "olist" / "analytical" / "marketing.csv"
            mpath.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(mpath, index=False)
            dataset = ingest_dataset(db, environment=env, name="olist_marketing", location=str(mpath))
        task = upsert_task(db, env, spec)
    else:
        task = db.query(PredictionTask).filter(PredictionTask.slug == task_slug).first()
    if task is None:
        print(f"task not found: {task_slug}", file=sys.stderr)
        return 1
    overrides = {"max_candidates": args.max_candidates, "seed": args.seed}
    cfg = search_from_yaml(config_path, overrides=overrides) if config_path.exists() else SearchConfig(
        max_candidates=int(args.max_candidates or 24), seed=int(args.seed or 42)
    )
    experiment = create_experiment(db, environment=env, dataset=dataset, task=task, config=cfg)
    experiment = execute_experiment(db, experiment)
    print(
        json.dumps(
            {
                "id": str(experiment.id),
                "status": experiment.status,
                "funnel": (experiment.result or {}).get("funnel"),
                "fusion": (experiment.result or {}).get("fusion"),
                "test_metrics": (experiment.result or {}).get("test_metrics"),
                "report": experiment.artifact_dir,
            },
            default=str,
        )
    )
    db.close()
    return 0


def cmd_experiment_status(args: argparse.Namespace) -> int:
    db = _session()
    from app.db.models import Experiment

    row = db.get(Experiment, args.id)
    if row is None:
        print("not found", file=sys.stderr)
        return 1
    print(json.dumps({"id": str(row.id), "status": row.status}))
    db.close()
    return 0


def cmd_experiment_report(args: argparse.Namespace) -> int:
    db = _session()
    from app.db.models import Experiment

    row = db.get(Experiment, args.id)
    if row is None or not row.artifact_dir:
        print("not found", file=sys.stderr)
        return 1
    path = Path(row.artifact_dir) / "report.md"
    print(path.read_text() if path.exists() else json.dumps(row.result, default=str, indent=2))
    db.close()
    return 0


def cmd_verify_openai_smoke(_args: argparse.Namespace) -> int:
    """Run exactly one synthetic-only live OpenAI verification request."""
    from app.services.openai_smoke import OpenAISmokeError, run_openai_verification_smoke

    try:
        print(json.dumps(run_openai_verification_smoke()))
    except OpenAISmokeError as exc:
        print(f"live smoke not executed: {exc.code}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dclab")
    sub = parser.add_subparsers(dest="cmd", required=True)

    user = sub.add_parser("user")
    user_sub = user.add_subparsers(dest="user_cmd", required=True)
    user_create = user_sub.add_parser("create")
    user_create.add_argument("--email", required=True)
    user_create.add_argument("--password", required=True)
    user_create.add_argument(
        "--role",
        required=True,
        choices=[role.value for role in UserRole],
    )
    user_create.add_argument("--name", default="")
    user_create.set_defaults(func=cmd_user_create)
    user_seed = user_sub.add_parser("seed")
    user_seed.set_defaults(func=cmd_user_seed)

    env = sub.add_parser("env")
    env_sub = env.add_subparsers(dest="env_cmd", required=True)
    seed = env_sub.add_parser("seed-dogfood")
    seed.set_defaults(func=cmd_env_seed)

    ds = sub.add_parser("dataset")
    ds_sub = ds.add_subparsers(dest="dataset_cmd", required=True)
    ingest = ds_sub.add_parser("ingest")
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--name", default="dataset")
    ingest.add_argument("--n", type=int, default=2000)
    ingest.set_defaults(func=cmd_dataset_ingest)
    profile = ds_sub.add_parser("profile")
    profile.add_argument("--dataset", required=True)
    profile.set_defaults(func=cmd_dataset_profile)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    create = task_sub.add_parser("create")
    create.add_argument("--config", required=True)
    create.set_defaults(func=cmd_task_create)

    exp = sub.add_parser("experiment")
    exp_sub = exp.add_subparsers(dest="experiment_cmd", required=True)
    run = exp_sub.add_parser("run")
    run.add_argument("--dataset", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--max-candidates", dest="max_candidates", type=int, default=24)
    run.add_argument("--seed", type=int, default=42)
    run.set_defaults(func=cmd_experiment_run)
    status = exp_sub.add_parser("status")
    status.add_argument("--id", required=True)
    status.set_defaults(func=cmd_experiment_status)
    report = exp_sub.add_parser("report")
    report.add_argument("--id", required=True)
    report.set_defaults(func=cmd_experiment_report)

    smoke = sub.add_parser(
        "verify-openai-smoke",
        help="call the production OpenAI verifier once with synthetic bounded evidence",
    )
    smoke.set_defaults(func=cmd_verify_openai_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
