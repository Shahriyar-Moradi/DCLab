#!/usr/bin/env python3
"""Non-CI generator for representative canonical-query EXPLAIN ANALYZE.

Profiles:
  smoke    tiny graph for a local check
  default  many workspaces/projects/runs/candidates/folds
  large    100k users (optional)
  xl       1M users (optional; not for CI)

Requires a PostgreSQL database already at Alembic head. Refuses to run against
the pytest database name `decisionai_test` unless `--allow-test-db` is set.

  python scripts/benchmark_canonical_queries.py --profile smoke --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from sqlalchemy import create_engine, text

SMOKE_PASSWORD_HASH = (
    "$2b$12$C6UzMDM.H6dfI/f/IKcEe.O4r0q5u5m0k0k0k0k0k0k0k0k0k0k0"
)

QUERIES = (
    (
        "workspace_project_list",
        """
        SELECT id, name, slug, status, created_at
        FROM projects
        WHERE workspace_id = :workspace_id
        ORDER BY created_at DESC, id
        """,
    ),
    (
        "recent_pipeline_runs",
        """
        SELECT id, status, pipeline_name, created_at
        FROM experiments
        WHERE workspace_id = :workspace_id
        ORDER BY created_at DESC
        LIMIT 50
        """,
    ),
    (
        "pipeline_detail",
        """
        SELECT e.id, e.status, e.project_id, e.workflow_run_id, e.pipeline_version_id
        FROM experiments AS e
        WHERE e.id = :pipeline_run_id AND e.workspace_id = :workspace_id
        """,
    ),
    (
        "candidate_comparison",
        """
        SELECT c.id, c.candidate_key, c.status, c.model_family, c.algorithm
        FROM experiment_candidates AS c
        WHERE c.experiment_id = :pipeline_run_id AND c.workspace_id = :workspace_id
        ORDER BY c.created_at, c.id
        """,
    ),
    (
        "model_registry",
        """
        SELECT id, version, pipeline_run_id, selected_candidate_id, created_at
        FROM model_versions
        WHERE workspace_id = :workspace_id
        ORDER BY created_at DESC
        LIMIT 50
        """,
    ),
    (
        "admin_cross_tenant_recent_failures",
        """
        SELECT id, workspace_id, status, failure_reason, created_at
        FROM experiments
        WHERE status = 'FAILED'
        ORDER BY created_at DESC
        LIMIT 50
        """,
    ),
)


@dataclass(frozen=True)
class Profile:
    name: str
    workspaces: int
    users_per_workspace: int
    projects_per_workspace: int
    runs_per_project: int
    candidates_per_run: int
    folds_per_candidate: int


PROFILES = {
    "smoke": Profile("smoke", 2, 4, 2, 2, 3, 3),
    "default": Profile("default", 24, 8, 3, 4, 6, 5),
    "large": Profile("large", 1000, 100, 2, 2, 4, 5),
    "xl": Profile("xl", 10000, 100, 1, 1, 2, 5),
}


def _url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/decisionai"
    )


def _load(connection, profile: Profile) -> dict:
    connection.execute(
        text(
            """
            INSERT INTO business_domains (id, slug, name, description, default_config)
            SELECT gen_random_uuid(), 'labs', 'Labs', 'Labs workflows', '{}'::jsonb
            WHERE NOT EXISTS (SELECT 1 FROM business_domains WHERE slug = 'labs')
            """
        )
    )
    labs_id = connection.execute(
        text("SELECT id FROM business_domains WHERE slug = 'labs'")
    ).scalar_one()
    env_id = connection.execute(
        text(
            """
            INSERT INTO environments (id, org_id, name)
            VALUES (gen_random_uuid(), 'benchmark', 'Benchmark env')
            RETURNING id
            """
        )
    ).scalar_one()
    task_id = connection.execute(
        text(
            """
            INSERT INTO prediction_tasks (id, environment_id, slug, name, spec)
            VALUES (gen_random_uuid(), :env_id, 'benchmark-task', 'Benchmark task', '{}'::jsonb)
            RETURNING id
            """
        ),
        {"env_id": env_id},
    ).scalar_one()

    connection.execute(
        text(
            """
            INSERT INTO workspaces (id, slug, name, kind)
            SELECT gen_random_uuid(),
                   'bench-ws-' || i,
                   'Benchmark workspace ' || i,
                   'business'
            FROM generate_series(1, :n) AS i
            """
        ),
        {"n": profile.workspaces},
    )
    connection.execute(
        text(
            """
            INSERT INTO workspace_domains (id, workspace_id, business_domain_id, config)
            SELECT gen_random_uuid(), w.id, :labs_id, '{}'::jsonb
            FROM workspaces AS w
            WHERE w.slug LIKE 'bench-ws-%'
            """
        ),
        {"labs_id": labs_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash, role, full_name, workspace_id)
            SELECT gen_random_uuid(),
                   'bench-' || w.slug || '-' || u.i || '@bench.invalid',
                   :password_hash,
                   CASE WHEN u.i = 1 THEN 'workspace_owner' ELSE 'ml_engineer' END,
                   'Benchmark user',
                   w.id
            FROM workspaces AS w
            CROSS JOIN generate_series(1, :users_per) AS u(i)
            WHERE w.slug LIKE 'bench-ws-%'
            """
        ),
        {"users_per": profile.users_per_workspace, "password_hash": SMOKE_PASSWORD_HASH},
    )
    connection.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role)
            SELECT gen_random_uuid(), u.workspace_id, u.id,
                   CASE WHEN u.role = 'workspace_owner' THEN 'workspace_owner' ELSE 'ml_engineer' END
            FROM users AS u
            WHERE u.email LIKE 'bench-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO projects (id, workspace_id, name, slug, description, created_by)
            SELECT gen_random_uuid(),
                   w.id,
                   'Project ' || p.i,
                   'bench-proj-' || p.i,
                   'Benchmark project',
                   (
                       SELECT u.id FROM users AS u
                       WHERE u.workspace_id = w.id
                       ORDER BY u.email
                       LIMIT 1
                   )
            FROM workspaces AS w
            CROSS JOIN generate_series(1, :projects) AS p(i)
            WHERE w.slug LIKE 'bench-ws-%'
            """
        ),
        {"projects": profile.projects_per_workspace},
    )
    connection.execute(
        text(
            """
            INSERT INTO dataset_assets (id, workspace_id, project_id, name, slug, description)
            SELECT gen_random_uuid(), p.workspace_id, p.id, p.name, p.slug || '-asset', ''
            FROM projects AS p
            WHERE p.slug LIKE 'bench-proj-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO datasets (
                id, environment_id, name, source_type, location, version,
                workspace_id, dataset_asset_id, project_id, schema_json
            )
            SELECT gen_random_uuid(), :env_id, a.name, 'file', '/bench.csv', 'v1',
                   a.workspace_id, a.id, a.project_id, '{}'::jsonb
            FROM dataset_assets AS a
            WHERE a.slug LIKE 'bench-proj-%-asset'
            """
        ),
        {"env_id": env_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO ml_workflows (
                id, workspace_id, project_id, workspace_domain_id, name, slug,
                description, business_objective, status, config
            )
            SELECT gen_random_uuid(),
                   p.workspace_id,
                   p.id,
                   (
                       SELECT d.id FROM workspace_domains AS d
                       WHERE d.workspace_id = p.workspace_id
                       LIMIT 1
                   ),
                   'Benchmark workflow',
                   'bench-wf-' || p.slug,
                   '',
                   'benchmark',
                   'active',
                   '{}'::jsonb
            FROM projects AS p
            WHERE p.slug LIKE 'bench-proj-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO workflow_runs (
                id, workspace_id, project_id, workflow_id, trigger_type, source_type,
                initiated_by_type, status
            )
            SELECT gen_random_uuid(),
                   wf.workspace_id,
                   wf.project_id,
                   wf.id,
                   'api',
                   'dataset',
                   'system',
                   'completed'
            FROM ml_workflows AS wf
            CROSS JOIN generate_series(1, :runs) AS r(i)
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        ),
        {"runs": profile.runs_per_project},
    )
    connection.execute(
        text(
            """
            INSERT INTO experiments (
                id, environment_id, task_id, dataset_id, config, workspace_id, project_id,
                workflow_run_id, pipeline_name, pipeline_index, status, failure_reason
            )
            SELECT gen_random_uuid(),
                   :env_id,
                   :task_id,
                   (
                       SELECT d.id FROM datasets AS d
                       WHERE d.project_id = wr.project_id
                       LIMIT 1
                   ),
                   '{}'::jsonb,
                   wr.workspace_id,
                   wr.project_id,
                   wr.id,
                   'deterministic_ml',
                   0,
                   CASE WHEN (row_number() OVER (ORDER BY wr.id)) % 11 = 0
                        THEN 'FAILED' ELSE 'COMPLETED' END,
                   CASE WHEN (row_number() OVER (ORDER BY wr.id)) % 11 = 0
                        THEN 'benchmark injected failure' ELSE NULL END
            FROM workflow_runs AS wr
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        ),
        {"env_id": env_id, "task_id": task_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO experiment_candidates (
                id, experiment_id, candidate_key, fingerprint, payload, workspace_id,
                project_id, status, model_family, algorithm
            )
            SELECT gen_random_uuid(),
                   e.id,
                   'cand-' || c.i,
                   md5(e.id::text || '-' || c.i::text),
                   '{}'::jsonb,
                   e.workspace_id,
                   e.project_id,
                   'trained',
                   'sklearn',
                   'logistic'
            FROM experiments AS e
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            CROSS JOIN generate_series(1, :candidates) AS c(i)
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        ),
        {"candidates": profile.candidates_per_run},
    )
    connection.execute(
        text(
            """
            INSERT INTO cv_fold_runs (
                id, workspace_id, project_id, candidate_id, fold_number,
                train_row_count, validation_row_count, status
            )
            SELECT gen_random_uuid(),
                   c.workspace_id,
                   c.project_id,
                   c.id,
                   f.i,
                   80,
                   20,
                   'completed'
            FROM experiment_candidates AS c
            JOIN experiments AS e ON e.id = c.experiment_id
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            CROSS JOIN generate_series(1, :folds) AS f(i)
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        ),
        {"folds": profile.folds_per_candidate},
    )
    connection.execute(
        text(
            """
            INSERT INTO model_evaluations (
                id, workspace_id, project_id, candidate_id, evaluation_type,
                evaluation_scope, dataset_id, status, summary
            )
            SELECT gen_random_uuid(),
                   c.workspace_id,
                   c.project_id,
                   c.id,
                   'cross_validation',
                   'cv_aggregate',
                   e.dataset_id,
                   'completed',
                   '{}'::jsonb
            FROM experiment_candidates AS c
            JOIN experiments AS e ON e.id = c.experiment_id
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO evaluation_metrics (id, model_evaluation_id, metric_name, metric_value)
            SELECT gen_random_uuid(), ev.id, 'roc_auc', 0.7
            FROM model_evaluations AS ev
            JOIN experiment_candidates AS c ON c.id = ev.candidate_id
            JOIN experiments AS e ON e.id = c.experiment_id
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO model_assets (
                id, workspace_id, workflow_id, name, slug, description, status
            )
            SELECT gen_random_uuid(), wf.workspace_id, wf.id, wf.name, wf.slug || '-model', '', 'active'
            FROM ml_workflows AS wf
            WHERE wf.slug LIKE 'bench-wf-%'
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO model_versions (
                id, model_asset_id, version, workspace_id, project_id, workflow_id,
                workflow_run_id, pipeline_run_id, selected_candidate_id, dataset_id,
                content_digest, metrics
            )
            SELECT gen_random_uuid(),
                   a.id,
                   'v-' || replace(e.id::text, '-', ''),
                   e.workspace_id,
                   e.project_id,
                   a.workflow_id,
                   e.workflow_run_id,
                   e.id,
                   (
                       SELECT c.id FROM experiment_candidates AS c
                       WHERE c.experiment_id = e.id
                       ORDER BY c.candidate_key
                       LIMIT 1
                   ),
                   e.dataset_id,
                   md5(e.id::text),
                   '{}'::jsonb
            FROM experiments AS e
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            JOIN model_assets AS a ON a.workflow_id = wf.id
            WHERE wf.slug LIKE 'bench-wf-%'
              AND e.status = 'COMPLETED'
            """
        )
    )
    sample = connection.execute(
        text(
            """
            SELECT e.workspace_id, e.id
            FROM experiments AS e
            JOIN workflow_runs AS wr ON wr.id = e.workflow_run_id
            JOIN ml_workflows AS wf ON wf.id = wr.workflow_id
            WHERE wf.slug LIKE 'bench-wf-%' AND e.status = 'COMPLETED'
            ORDER BY e.created_at DESC
            LIMIT 1
            """
        )
    ).one()
    counts = connection.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM users WHERE email LIKE 'bench-%') AS users,
              (SELECT count(*) FROM workspaces WHERE slug LIKE 'bench-ws-%') AS workspaces,
              (SELECT count(*) FROM projects WHERE slug LIKE 'bench-proj-%') AS projects,
              (SELECT count(*) FROM experiments e
                 JOIN workflow_runs wr ON wr.id = e.workflow_run_id
                 JOIN ml_workflows wf ON wf.id = wr.workflow_id
                 WHERE wf.slug LIKE 'bench-wf-%') AS runs,
              (SELECT count(*) FROM experiment_candidates c
                 JOIN experiments e ON e.id = c.experiment_id
                 JOIN workflow_runs wr ON wr.id = e.workflow_run_id
                 JOIN ml_workflows wf ON wf.id = wr.workflow_id
                 WHERE wf.slug LIKE 'bench-wf-%') AS candidates,
              (SELECT count(*) FROM cv_fold_runs f
                 JOIN experiment_candidates c ON c.id = f.candidate_id
                 JOIN experiments e ON e.id = c.experiment_id
                 JOIN workflow_runs wr ON wr.id = e.workflow_run_id
                 JOIN ml_workflows wf ON wf.id = wr.workflow_id
                 WHERE wf.slug LIKE 'bench-wf-%') AS folds
            """
        )
    ).one()
    return {
        "workspace_id": sample[0],
        "pipeline_run_id": sample[1],
        "counts": counts._asdict() if hasattr(counts, "_asdict") else dict(counts._mapping),
    }


def _explain(connection, sql: str, params: dict) -> str:
    rows = connection.execute(text("EXPLAIN ANALYZE " + sql), params).all()
    return "\n".join(row[0] for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--yes", action="store_true", help="insert benchmark rows")
    parser.add_argument(
        "--allow-test-db",
        action="store_true",
        help="allow running against decisionai_test",
    )
    args = parser.parse_args()
    url = _url()
    if "decisionai_test" in url and not args.allow_test_db:
        print("refusing to load benchmark data into decisionai_test", file=sys.stderr)
        return 2
    if not args.yes:
        print("pass --yes to insert rows and run EXPLAIN ANALYZE", file=sys.stderr)
        return 2
    profile = PROFILES[args.profile]
    if args.profile in {"large", "xl"}:
        print(
            f"profile {args.profile} is optional and not part of CI "
            f"({profile.workspaces * profile.users_per_workspace} users)",
            file=sys.stderr,
        )
    engine = create_engine(url)
    with engine.begin() as connection:
        loaded = _load(connection, profile)
        params = {
            "workspace_id": loaded["workspace_id"],
            "pipeline_run_id": loaded["pipeline_run_id"],
        }
        print(f"profile={profile.name} counts={loaded['counts']}")
        print(f"sample workspace_id={params['workspace_id']} pipeline_run_id={params['pipeline_run_id']}")
        connection.execute(text("ANALYZE"))
        for name, sql in QUERIES:
            print(f"\n## {name}")
            print(_explain(connection, sql, params))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
