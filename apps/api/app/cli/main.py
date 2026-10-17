"""Internal DCLab CLI: dclab dataset|task|experiment|graph ..."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.config import REPO_ROOT
from app.db.session import get_session_factory
from app.engine.datasets.olist import marketing_frame, raw_available, write_analytical
from app.services.lab_service import (
    ingest_dataset,
    ingest_synthetic,
    profile_dataset,
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
        UserRole.WORKSPACE_OWNER,
        UserRole.WORKSPACE_ADMIN,
        UserRole.ML_ENGINEER,
        UserRole.VIEWER,
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
    from app.services.auth_service import demo_logins, ensure_demo_users

    from app.agents.governance.seed import seed_platform_governance
    from app.agents.prompt_releases import sync_prompt_releases
    from app.agents.semantic.releases import sync_jev_releases

    db = _session()
    users = ensure_demo_users(db)
    seed_platform_governance(db)
    sync_prompt_releases(db)  # idempotent: the code-owned prompts become releases (ADR 0009 §2.8)
    sync_jev_releases(db)  # and the pinned Jev purpose releases (jev:<purpose>)
    db.commit()
    print(
        json.dumps(
            {
                "users": [
                    {"id": str(row.id), "email": row.email, "role": row.role, "full_name": row.full_name}
                    for row in users
                ],
                "logins": demo_logins(),
            }
        )
    )
    db.close()
    return 0


def cmd_governance_seed(_args: argparse.Namespace) -> int:
    from app.agents.governance.seed import seed_platform_governance

    db = _session()
    try:
        result = seed_platform_governance(db)
        db.commit()
    finally:
        db.close()
    print(json.dumps(result))
    return 0


def cmd_governance_switch(args: argparse.Namespace) -> int:
    """Audited switch row by a named platform admin (ADR 0009 §2.11); no HTTP route."""

    from uuid import UUID

    from sqlalchemy import func, select

    from app.agents.governance.switches import flip_off, re_enable
    from app.db.models import User

    db = _session()
    try:
        admin = db.scalar(select(User).where(func.lower(User.email) == args.admin.strip().lower()))
        if admin is None:
            print(json.dumps({"error": "unknown admin"}))
            return 2
        workspace_id = UUID(args.workspace) if args.workspace else None
        common = {"workspace_id": workspace_id, "switch_key": args.key, "reason": args.reason, "actor": admin}
        row = flip_off(db, **common) if args.state == "off" else re_enable(db, **common)
        db.commit()
        print(json.dumps({"id": str(row.id), "switch_key": row.switch_key, "state": row.state}))
        return 0
    finally:
        db.close()


def _limit_arg(raw: str | None):
    """``--runs-per-day 100`` / ``unlimited`` / ``default`` (back to the default plan's value)."""

    from app.services.usage_service import UNSET

    if raw is None:
        return None
    word = raw.strip().lower()
    if word == "unlimited":
        return ("set", None)
    if word == "default":
        return ("set", UNSET)
    if not word.isdigit():
        raise SystemExit(f"limit must be a whole number, 'unlimited' or 'default': {raw!r}")
    return ("set", int(word))


def cmd_usage_set_plan(args: argparse.Namespace) -> int:
    """P8.4-A: a named platform admin sets a workspace's plan and limits; no HTTP route (tenants
    cannot change their own limits). Entitlement rows record ``platform_staff:<admin id>``.
    ``--admin`` only records who did it (no password is asked): the control is who holds the
    database credentials this command runs with."""

    from uuid import UUID

    from sqlalchemy import func, select

    from app.db.models import User
    from app.domain.errors import IdentityError
    from app.domain.usage import QUOTA_COMPUTE_SECONDS_MONTH, QUOTA_RUNS_PER_DAY, QUOTA_STORAGE_BYTES
    from app.services.usage_service import set_plan

    limits = {}
    for key, raw in ((QUOTA_COMPUTE_SECONDS_MONTH, args.compute_seconds_month),
                     (QUOTA_STORAGE_BYTES, args.storage_bytes), (QUOTA_RUNS_PER_DAY, args.runs_per_day)):
        parsed = _limit_arg(raw)
        if parsed is not None:
            limits[key] = parsed[1]
    db = _session()
    try:
        admin = db.scalar(select(User).where(func.lower(User.email) == args.admin.strip().lower()))
        if admin is None:
            print(json.dumps({"error": "unknown admin"}))
            return 2
        try:
            result = set_plan(db, actor=admin, workspace_id=UUID(args.workspace), plan=args.plan, limits=limits)
        except IdentityError as exc:
            db.rollback()
            print(json.dumps({"error": str(exc)}))
            return 2
        db.commit()
        print(json.dumps(result))
        return 0
    finally:
        db.close()


def cmd_improve_start(args: argparse.Namespace) -> int:
    """P5.4-A2 (local runs): start an improve loop as a named person (their own ML-write authority is
    checked); the worker steps it. Acceptance stays a person's act through the API (P5.4-A3)."""

    from uuid import UUID

    from sqlalchemy import func, select

    from app.db.models import User
    from app.domain.errors import ExperimentNotFoundError, ImproveRunError
    from app.services.improve.service import start_improve_run

    db = _session()
    try:
        user = db.scalar(select(User).where(func.lower(User.email) == args.user.strip().lower()))
        if user is None:
            print(json.dumps({"error": "unknown user"}))
            return 2
        try:
            run = start_improve_run(db, actor=user, workspace_id=UUID(args.workspace),
                                    start_experiment_id=UUID(args.experiment), goal=json.loads(args.goal),
                                    budget=json.loads(args.budget) if args.budget else None)
        except (ImproveRunError, ExperimentNotFoundError) as exc:
            db.rollback()
            print(json.dumps({"error": getattr(exc, "code", "experiment_not_found"), "message": str(exc)}))
            return 2
        print(json.dumps({"improve_run_id": str(run.id), "status": run.status, "plan": run.plan,
                          "planned_comparisons": run.planned_comparisons}))
        return 0
    finally:
        db.close()


def cmd_agents_sync_prompts(args: argparse.Namespace) -> int:
    """Create prompt_releases rows from app/agents/prompts (ADR 0009 §2.8); --check only verifies."""

    from app.agents.prompt_releases import sync_prompt_releases, verify_prompt_releases

    from pathlib import Path

    from app.agents.prompt_releases import PROMPTS_ROOT

    root = Path(args.root) if args.root else PROMPTS_ROOT
    db = _session()
    try:
        result = {} if args.check else sync_prompt_releases(db, root).as_dict()
        problems = verify_prompt_releases(db, root)
        if args.check:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()
    print(json.dumps({**result, "problems": problems}))
    return 1 if problems or result.get("mismatched") else 0


def _load_report(path: str) -> dict:
    from pathlib import Path

    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_r3_run(args: argparse.Namespace) -> int:
    """R3 evaluation run (P6.8-A). Offline by default: the R1 ``quick`` corpus and the scripted
    fake answerer; ``--live`` asks the real Jev provider through the gateway (AI_ENABLED and
    the platform switches must allow it; refused when CI is set)."""

    import tempfile
    from pathlib import Path
    from uuid import UUID

    from app.services import r3_evaluation_service as r3

    ci = os.environ.get("CI", "").strip().lower() not in ("", "0", "false") or bool(os.environ.get("GITHUB_ACTIONS"))
    workspaces = [UUID(w) for w in args.workspace]
    if args.live and ci:
        print(json.dumps({"error": "a live R3 run never runs in CI"}))
        return 2
    if (args.live or workspaces) and not args.admin:
        print(json.dumps({"error": "--live and --workspace need --admin (an active dclab_admin; operator-trust "
                                   "attribution recorded in the run)"}))
        return 2
    corpus = r3.benchmark_corpus(args.suite, tasks=args.task or None, seed=args.seed)
    answerer, candidate, db, eval_ws, admin = r3.ScriptedAnswerer(seed=args.seed), f"fake:{args.seed}", None, None, None
    operator = None
    if args.live or workspaces or args.store:
        db = _session()
    try:
        if args.admin and db is not None:
            import getpass
            import socket

            from sqlalchemy import func, select

            from app.agents.governance.policy import is_platform_admin
            from app.db.models import User

            admin = db.scalar(select(User).where(func.lower(User.email) == args.admin.strip().lower()))
            if admin is None or not is_platform_admin(db, admin):
                print(json.dumps({"error": "--admin must name an active dclab_admin"}))
                return 2
            # --admin is operator trust (no login): the admin id and a hash of user@host (never the raw
            # names) are part of the run's evidence. Never commit a live report file.
            import hashlib

            operator = {"admin_id": str(admin.id),
                        "operator_digest": hashlib.sha256(f"{getpass.getuser()}@{socket.gethostname()}".encode()).hexdigest()[:16]}
        if args.live:
            from app.agents.gateway.service import GatewayService
            from app.agents.semantic.port import semantic_port
            from app.config import get_settings

            settings = get_settings()
            port = semantic_port(GatewayService(), settings=settings)
            if not r3.platform_evaluation_workspace_ok(db, settings.r3_eval_workspace_id, settings) or type(
                    port).__name__ != "JevSemanticPort":
                print(json.dumps({"error": "live runs need AI and Jev enabled, the Jev provider and R3_EVAL_WORKSPACE_ID "
                                           f"naming a workspace with the {r3.EVALUATION_CAPABILITY} capability"}))
                return 2
            eval_ws = settings.r3_eval_workspace_id
            answerer = r3.port_answerer(db, port, eval_ws, settings=settings,
                                        dataset_id=UUID(args.dataset) if args.dataset else None,
                                        project_id=UUID(args.project) if args.project else None)
            candidate = f"live:jev:admin:{admin.id}"
        previous = _load_report(args.previous) if args.previous else None
        report = r3.run_r3(corpus, answerer, candidate=candidate, db=db, workspace_ids=workspaces, actor=admin,
                           evaluation_workspace_id=eval_ws, previous=previous, apply_demotions=bool(args.live),
                           partition=args.partition, operator=operator,
                           settings=settings if args.live else None)
        if db is not None:
            db.commit()  # the stored run (+ eval_failure incidents of a live run)
    finally:
        if db is not None:
            db.close()
    out = Path(args.out or Path(tempfile.gettempdir()) / "dclab-runs" / "r3-latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch(mode=0o600, exist_ok=True)
    out.chmod(0o600)  # aggregates and operator attribution: owner-readable only
    out.write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(r3.render_markdown(report))
    print(json.dumps({"run_id": report["run_id"], "digest": report["digest"], "out": str(out)}))
    return 0 if all(v["equal"] for v in report["ablation"]["points"].values()) else 1


def cmd_r3_report(args: argparse.Namespace) -> int:
    from app.services.r3_evaluation_service import render_markdown

    print(render_markdown(_load_report(args.report)))
    return 0


def cmd_r3_promote_check(args: argparse.Namespace) -> int:
    """ADVISORY: would the rule engine let ``--key`` be proposed at ``--level`` on this report file?
    Prints every gate; writes nothing. The binding decision is ``propose_promotion`` on a STORED
    live run plus a second dclab_admin's ``accept_level`` — neither has, nor may get, a CLI path."""

    from dataclasses import asdict

    from app.services.r3_evaluation_service import promotion_check

    report = _load_report(args.report)
    previous = _load_report(args.previous) if args.previous else {}
    entry = dict(report["points"][args.key], key=args.key)
    verdict = promotion_check(entry, args.level, previous=previous.get("points", {}).get(args.key),
                              holm=report.get("holm_adjusted_p_values") or {})
    print(json.dumps(asdict(verdict), indent=1))
    return 0 if verdict.allowed else 1


def cmd_r3_record_levels(args: argparse.Namespace) -> int:
    """Platform L0 rows for every registry key, by a named platform admin, citing the run."""

    from sqlalchemy import func, select

    from app.db.models import User
    from app.services.r3_evaluation_service import record_first_levels

    report = _load_report(args.report)
    db = _session()
    try:
        admin = db.scalar(select(User).where(func.lower(User.email) == args.admin.strip().lower()))
        if admin is None:
            print(json.dumps({"error": "unknown admin"}))
            return 2
        outcome = record_first_levels(db, report, admin=admin)
        db.commit()
    finally:
        db.close()
    print(json.dumps({"run_id": report["run_id"], "levels": outcome}))
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
    from app.db.models import User
    from app.services.lab_training_service import train_dataset_target

    actor = db.query(User).filter(User.email == args.actor).first()
    if actor is None:
        print(f"actor not found: {args.actor} (pass --actor <admin email>)", file=sys.stderr)
        return 1
    target = str((task.spec or {}).get("target") or "").strip()
    if not target:
        print(f"task {task.slug} has no target column", file=sys.stderr)
        return 1
    # One training path: queue an open-ingest build; the worker trains it.
    experiment = train_dataset_target(
        db, dataset, actor=actor, target=target,
        origin={"admin_lab_dataset_id": str(dataset.id), "task_slug": task.slug},
    )
    print(
        json.dumps(
            {
                "id": str(experiment.id),
                "status": experiment.status,
                "next": "run `dclab worker run --once` (or a running worker) to train it",
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

    from app.services.reproducibility_service import read_run_file

    row = db.get(Experiment, args.id)
    if row is None:
        print("not found", file=sys.stderr)
        return 1
    report = read_run_file(db, row, "report.md")
    print(report.decode("utf-8") if report else json.dumps(row.result, default=str, indent=2))
    db.close()
    return 0


def cmd_verify_ai_smoke(args: argparse.Namespace) -> int:
    """Run exactly one synthetic-only live request through a provider adapter (default Anthropic;
    needs --live and AI switched on). Prints a safe summary only, never key material."""
    from app.config import get_settings
    import app.services.openai_smoke as smoke

    provider = getattr(args, "provider", None) or "anthropic"
    db = _session()
    try:
        settings = get_settings()
        refusal = smoke.live_smoke_refusal(db, live=bool(getattr(args, "live", False)),
                                           ai_enabled=bool(settings.ai_enabled), provider=provider,
                                           legacy_enabled=settings.ai_legacy_providers_enabled is True)
    finally:
        db.close()
    if refusal is not None:
        print(f"live smoke not executed: {refusal}", file=sys.stderr)
        return 1
    run = smoke.run_openai_verification_smoke if provider == "openai" else smoke.run_anthropic_verification_smoke
    try:
        print(json.dumps(run()))
    except smoke.OpenAISmokeError as exc:
        print(f"live smoke not executed: {exc.code}", file=sys.stderr)
        return 1
    return 0


def cmd_verify_openai_smoke(args: argparse.Namespace) -> int:
    """LEGACY (switched to Anthropic 2026-10-16; kept for now): the smoke against the OpenAI adapter."""
    return cmd_verify_ai_smoke(argparse.Namespace(**{**vars(args), "provider": "openai"}))


def cmd_worker_run(args: argparse.Namespace) -> int:
    """Claim queued ml_jobs and run them. Production companion to the API process."""
    import os
    import socket
    import time

    from app import observability
    from app.config import get_settings, mark_worker_process
    from app.observability.setup import setup as setup_observability
    from app.services.ml_job_service import process_next_job
    from app.services.notification_service import run_notification_tick
    from app.services.retrain_service import run_retrain_tick
    from app.services.scoring_schedule_service import run_scoring_schedule_tick
    from app.services.system_health_service import mark_worker_connections
    from app.services.upload_attempt_service import PURGE_TICK_SECONDS, purge_expired_upload_attempts
    from app.services.usage_service import run_usage_metering_tick

    mark_worker_process()  # P7.1-A: this process may load stored models
    # P8.5-A: name this worker's DB connections (liveness for /health/ready and the operator
    # console; no new table), then logs / optional OTel for the worker process.
    mark_worker_connections()
    setup_observability("worker")
    settings = get_settings()
    print(json.dumps({"model_probe_limits": {"memory_bytes": settings.model_probe_memory_bytes,
                                             "timeout_seconds": settings.model_probe_timeout_seconds}}), file=sys.stderr)
    poll = (
        args.poll_seconds
        if args.poll_seconds is not None
        else get_settings().ml_job_poll_seconds
    )
    claimed_by = f"{socket.gethostname()}:{os.getpid()}"[:128]
    next_tick = 0.0
    next_usage_tick = 0.0
    next_notification_tick = 0.0
    next_purge_tick = 0.0
    next_retrain_tick = 0.0
    while True:
        if time.monotonic() >= next_purge_tick:  # D3a: the 90-day upload log retention (bounded, idempotent)
            next_purge_tick = time.monotonic() + PURGE_TICK_SECONDS
            db = _session()
            try:
                purge_expired_upload_attempts(db)
            except Exception as exc:  # noqa: BLE001 - never stops job processing
                print(json.dumps({"error": f"upload log purge: {type(exc).__name__}"}), file=sys.stderr)
                db.rollback()
            finally:
                db.close()
        if time.monotonic() >= next_notification_tick:  # P8.8-A outbox: bounded batch, one transaction per row
            next_notification_tick = time.monotonic() + max(1.0, float(settings.notifications_tick_seconds))
            db = _session()
            try:
                run_notification_tick(db)
            except Exception as exc:  # noqa: BLE001 - never stops job processing
                print(json.dumps({"error": f"notification tick: {type(exc).__name__}"}), file=sys.stderr)
                db.rollback()
            finally:
                db.close()
        if time.monotonic() >= next_usage_tick:  # P8.4-A usage metering (bounded, per-workspace transactions)
            next_usage_tick = time.monotonic() + max(1.0, float(settings.usage_metering_tick_seconds))
            db = _session()
            try:
                run_usage_metering_tick(db)
            except Exception as exc:  # noqa: BLE001 - never stops job processing
                print(json.dumps({"error": f"usage metering tick: {exc}"}), file=sys.stderr)
                db.rollback()
            finally:
                db.close()
        if time.monotonic() >= next_tick:  # weekly scheduled scoring: queues jobs this loop then runs
            next_tick = time.monotonic() + max(1.0, float(settings.scoring_schedule_tick_seconds))
            db = _session()
            try:
                with observability.scheduled_tick() as tick:
                    runs = run_scoring_schedule_tick(db)
                    queued = sum(1 for run in runs if run.outcome == "queued")
                    tick.labels = {"runs_queued": queued, "runs_skipped": len(runs) - queued}
            except Exception as exc:  # noqa: BLE001 - never stops job processing
                print(json.dumps({"error": f"scoring schedule tick: {exc}"}), file=sys.stderr)
                db.rollback()
            finally:
                db.close()
        if time.monotonic() >= next_retrain_tick:  # D5a: compare finished retrains, apply retrain policies
            next_retrain_tick = time.monotonic() + max(1.0, float(settings.retrain_tick_seconds))
            db = _session()
            try:
                run_retrain_tick(db)
            except Exception as exc:  # noqa: BLE001 - never stops job processing
                print(json.dumps({"error": f"retrain tick: {type(exc).__name__}"}), file=sys.stderr)
                db.rollback()
            finally:
                db.close()
        db = _session()
        try:
            job = process_next_job(db, claimed_by=claimed_by)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            db.rollback()
            job = None
        finally:
            db.close()
        if args.once:
            if job is None:
                print(json.dumps({"claimed": False}))
            else:
                print(
                    json.dumps(
                        {
                            "claimed": True,
                            "id": str(job.id),
                            "status": job.status,
                            "attempts": job.attempts,
                        }
                    )
                )
            return 0
        if job is None:
            time.sleep(max(0.1, float(poll)))


def cmd_graph_backfill_winner_records(_args: argparse.Namespace) -> int:
    """Materialize winner_locked decision records from model_selection_decisions (ADR 0006 Q7)."""
    from app.services.winner_record_backfill import backfill_winner_records

    db = _session()
    try:
        result = backfill_winner_records(db)
    finally:
        db.close()
    print(json.dumps(result.as_dict()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    from app.db.models import UserRole

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

    governance = sub.add_parser("governance")
    governance_sub = governance.add_subparsers(dest="governance_cmd", required=True)
    governance_seed = governance_sub.add_parser("seed")
    governance_seed.set_defaults(func=cmd_governance_seed)
    governance_switch = governance_sub.add_parser("switch")
    governance_switch.add_argument("state", choices=["on", "off"])
    governance_switch.add_argument("key")
    governance_switch.add_argument("--admin", required=True, help="email of the platform admin")
    governance_switch.add_argument("--reason", required=True)
    governance_switch.add_argument("--workspace", default=None, help="workspace id (omit for platform keys)")
    governance_switch.set_defaults(func=cmd_governance_switch)

    usage = sub.add_parser("usage", help="P8.4-A plans and limits (platform staff)")
    usage_sub = usage.add_subparsers(dest="usage_cmd", required=True)
    set_plan = usage_sub.add_parser("set-plan", help="set a workspace's plan name and limits")
    set_plan.add_argument("workspace", help="workspace id")
    set_plan.add_argument("--admin", required=True,
                          help="email of the platform admin (recorded only, not authenticated: database "
                               "credentials are the control)")
    set_plan.add_argument("--plan", default=None, help="plan name (a label, e.g. beta, team)")
    for flag in ("--compute-seconds-month", "--storage-bytes", "--runs-per-day"):
        set_plan.add_argument(flag, default=None, help="whole number, 'unlimited' or 'default'")
    set_plan.set_defaults(func=cmd_usage_set_plan)

    improve = sub.add_parser("improve", help="P5.4-A2 improve loops (local runs)")
    improve_sub = improve.add_subparsers(dest="improve_cmd", required=True)
    improve_start = improve_sub.add_parser("start", help="start a loop on a completed scored run")
    improve_start.add_argument("--user", required=True, help="email of the person starting the loop")
    improve_start.add_argument("--workspace", required=True, help="workspace id")
    improve_start.add_argument("--experiment", required=True, help="the start experiment id")
    improve_start.add_argument("--goal", required=True, help='JSON, e.g. {"plain": {"metric": "pr_auc"}}')
    improve_start.add_argument("--budget", default=None, help='JSON, e.g. {"max_iterations": 3}')
    improve_start.set_defaults(func=cmd_improve_start)

    agents = sub.add_parser("agents")
    agents_sub = agents.add_subparsers(dest="agents_cmd", required=True)
    sync_prompts = agents_sub.add_parser("sync-prompts")
    sync_prompts.add_argument("--check", action="store_true", help="verify only; write nothing")
    sync_prompts.add_argument("--root", default=None, help="prompt directory (default app/agents/prompts)")
    sync_prompts.set_defaults(func=cmd_agents_sync_prompts)

    r3 = sub.add_parser("r3", help="R3 evaluation harness and trust levels (ADR 0008 §4)")
    r3_sub = r3.add_subparsers(dest="r3_cmd", required=True)
    r3_run = r3_sub.add_parser("run")
    r3_run.add_argument("--suite", choices=["quick", "full"], default="quick")
    r3_run.add_argument("--task", action="append", default=[], help="run only these R1 task ids")
    r3_run.add_argument("--seed", type=int, default=42)
    r3_run.add_argument("--out", default=None, help="report path (default <temp>/dclab-runs/r3-latest.json; "
                                                     "never commit a live report)")
    r3_run.add_argument("--previous", default=None, help="previous run report (stability over two runs)")
    r3_run.add_argument("--workspace", action="append", default=[],
                        help="workspace id whose aggregates (never rows) join the report if it opted in "
                             "(policy data.share_r3_aggregates)")
    r3_run.add_argument("--live", action="store_true",
                        help="ask the real Jev provider inside R3_EVAL_WORKSPACE_ID (never in CI)")
    r3_run.add_argument("--admin", default=None,
                        help="email of the active dclab_admin running a live run or joining tenant aggregates")
    r3_run.add_argument("--store", action="store_true", help="also store an offline run in r3_runs (needs the DB)")
    r3_run.add_argument("--partition", choices=["both", "development", "sealed"], default="both",
                        help="development = prompt-work mode (sealed cases never scored)")
    r3_run.add_argument("--dataset", default=None, help="live: the registered benchmark dataset id")
    r3_run.add_argument("--project", default=None, help="live: project the calls are attributed to")
    r3_run.set_defaults(func=cmd_r3_run)
    r3_report = r3_sub.add_parser("report")
    r3_report.add_argument("--report", required=True)
    r3_report.set_defaults(func=cmd_r3_report)
    r3_check = r3_sub.add_parser("promote-check")
    r3_check.add_argument("--report", required=True)
    r3_check.add_argument("--key", required=True)
    r3_check.add_argument("--level", type=int, required=True)
    r3_check.add_argument("--previous", default=None)
    r3_check.set_defaults(func=cmd_r3_promote_check)
    r3_levels = r3_sub.add_parser("record-levels")
    r3_levels.add_argument("--report", required=True)
    r3_levels.add_argument("--admin", required=True, help="email of the platform admin who records the levels")
    r3_levels.set_defaults(func=cmd_r3_record_levels)

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
    run.add_argument(
        "--actor",
        default=os.environ.get("DCLAB_ADMIN_EMAIL", "admin@dclab.io"),
        help="email of the platform admin the build is queued as",
    )
    run.set_defaults(func=cmd_experiment_run)
    status = exp_sub.add_parser("status")
    status.add_argument("--id", required=True)
    status.set_defaults(func=cmd_experiment_status)
    report = exp_sub.add_parser("report")
    report.add_argument("--id", required=True)
    report.set_defaults(func=cmd_experiment_report)

    ai_smoke = sub.add_parser(
        "verify-ai-smoke",
        help="call the production verifier once through a provider adapter with synthetic bounded evidence",
    )
    ai_smoke.add_argument("--live", action="store_true",
                          help="required: the call leaves the process (AI_ENABLED and global_ai must be on)")
    ai_smoke.add_argument("--provider", choices=("anthropic", "openai"), default="anthropic",
                          help="anthropic (default) or openai (legacy)")
    ai_smoke.set_defaults(func=cmd_verify_ai_smoke)
    smoke = sub.add_parser(
        "verify-openai-smoke",
        help="legacy: call the OpenAI verifier once with synthetic bounded evidence",
    )
    smoke.add_argument("--live", action="store_true",
                       help="required: the call leaves the process (AI_ENABLED and global_ai must be on)")
    smoke.set_defaults(func=cmd_verify_openai_smoke)

    worker = sub.add_parser(
        "worker",
        help="claim and run durable ml_jobs with PostgreSQL SKIP LOCKED",
    )
    worker_sub = worker.add_subparsers(dest="worker_cmd", required=True)
    worker_run = worker_sub.add_parser("run")
    worker_run.add_argument(
        "--once",
        action="store_true",
        help="recover, claim at most one job, run it, then exit",
    )
    worker_run.add_argument(
        "--poll-seconds",
        type=float,
        default=None,
        help="sleep between empty polls (default: settings.ml_job_poll_seconds)",
    )
    worker_run.set_defaults(func=cmd_worker_run)

    graph = sub.add_parser("graph", help="ML state graph operator commands (ADR 0006)")
    graph_sub = graph.add_subparsers(dest="graph_cmd", required=True)
    backfill = graph_sub.add_parser(
        "backfill-winner-records",
        help="idempotently write winner_locked decision records for existing winner locks",
    )
    backfill.set_defaults(func=cmd_graph_backfill_winner_records)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
