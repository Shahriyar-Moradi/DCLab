"""PostgreSQL freeze for a PipelineRun's canonical scientific evidence.

``experiments.scientific_evidence_locked_at`` is stamped by
``app.services.evidence_lock_service`` only once every child row exists. From
that moment PostgreSQL rejects INSERT/UPDATE/DELETE on the run's evidence, so a
psql session cannot rewrite CV scores, hyperparameters, the winner, or the final
holdout metrics after the fact.

Deliberately still writable, because they are post-run evidence in their own
right: ``ml_run_events`` (append-only), ``ml_run_verifications``,
``llm_invocations``, ``artifacts``, and ``experiments.status``/``result``.
"""

from __future__ import annotations

LOCK_COLUMN = "scientific_evidence_locked_at"

# A stage run is "finalized" in one of these states. Anything still queued or
# running may be closed out by the terminal event, which fires after the lock.
FINALIZED_STAGE_STATUSES = ("completed", "failed", "skipped")
POST_RUN_STAGE_KEYS = ("deterministic_verification", "report", "openai_audit")

_LOCKED_MESSAGE = (
    "% is frozen: scientific evidence for this pipeline run is locked"
)

# PostgreSQL validates the NULL -> timestamp transition too. Besides protecting
# against a premature direct-SQL lock, this predicate lets the migration safely
# backfill only historical runs whose complete evidence already exists.
EVIDENCE_COMPLETE_SQL = """
CREATE OR REPLACE FUNCTION pipeline_run_scientific_evidence_complete(wanted_run uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM experiments AS run
        JOIN model_selection_decisions AS selection
          ON selection.pipeline_run_id = run.id
        JOIN experiment_candidates AS winner
          ON winner.id = selection.selected_candidate_id
         AND winner.experiment_id = run.id
        LEFT JOIN feature_set_versions AS feature_version
          ON feature_version.id = winner.feature_set_version_id
        WHERE run.id = wanted_run
          AND upper(run.status) = 'COMPLETED'
          AND lower(winner.status) = 'trained'
          AND (
              (
                  run.project_id IS NULL
                  AND winner.feature_set_version_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM experiment_candidates AS feature_candidate
                      WHERE feature_candidate.experiment_id = run.id
                        AND feature_candidate.feature_set_version_id IS NOT NULL
                  )
              )
              OR (
                  winner.feature_set_version_id IS NOT NULL
                  AND feature_version.locked_at IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM features AS feature_row
                      WHERE feature_row.feature_set_version_id = winner.feature_set_version_id
                  )
              )
          )
          AND (
              (
                  run.workflow_run_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM model_versions AS unexpected_version
                      WHERE unexpected_version.pipeline_run_id = run.id
                  )
              )
              OR EXISTS (
                  SELECT 1
                  FROM model_versions AS version_row
                  WHERE version_row.pipeline_run_id = run.id
                    AND version_row.selected_candidate_id = winner.id
                    AND version_row.dataset_id = run.dataset_id
                    AND version_row.feature_set_version_id = winner.feature_set_version_id
                    AND version_row.runtime_environment_id IS NOT NULL
                    AND version_row.model_artifact_id IS NOT NULL
                    AND version_row.feature_manifest_artifact_id IS NOT NULL
              )
          )
          AND EXISTS (
              SELECT 1
              FROM preprocessing_steps AS step
              WHERE step.pipeline_run_id = run.id
          )
          AND EXISTS (
              SELECT 1
              FROM model_evaluations AS holdout
              WHERE holdout.candidate_id = winner.id
                AND holdout.evaluation_scope = 'final_holdout'
                AND holdout.status = 'completed'
                AND EXISTS (
                    SELECT 1
                    FROM evaluation_metrics AS metric
                    WHERE metric.model_evaluation_id = holdout.id
                )
                AND (
                    (
                        run.workflow_run_id IS NULL
                        AND holdout.model_version_id IS NULL
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM model_versions AS version_row
                        WHERE version_row.pipeline_run_id = run.id
                          AND version_row.selected_candidate_id = winner.id
                          AND holdout.model_version_id = version_row.id
                    )
                )
          )
          AND EXISTS (
              SELECT 1
              FROM experiment_test_predictions AS prediction
              WHERE prediction.experiment_id = run.id
          )
          AND EXISTS (
              SELECT 1
              FROM artifacts AS artifact
              WHERE artifact.pipeline_run_id = run.id
                AND artifact.metadata->>'role' = 'dependency_lock'
          )
          AND EXISTS (
              SELECT 1
              FROM artifacts AS artifact
              WHERE artifact.pipeline_run_id = run.id
                AND artifact.metadata->>'role' = 'model'
          )
          AND EXISTS (
              SELECT 1
              FROM artifacts AS artifact
              WHERE artifact.pipeline_run_id = run.id
                AND artifact.metadata->>'role' = 'feature_manifest'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM experiment_candidates AS candidate_row
              WHERE candidate_row.experiment_id = run.id
                AND NOT EXISTS (
                    SELECT 1
                    FROM model_hyperparameters AS parameter_row
                    WHERE parameter_row.candidate_id = candidate_row.id
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM experiment_candidates AS candidate_row
              WHERE candidate_row.experiment_id = run.id
                AND lower(candidate_row.status) = 'trained'
                AND (
                    CASE
                        WHEN jsonb_typeof(candidate_row.payload->'folds') = 'array'
                        THEN jsonb_array_length(candidate_row.payload->'folds')
                        ELSE 0
                    END = 0
                    OR (
                        SELECT count(*)
                        FROM cv_fold_runs AS fold
                        WHERE fold.candidate_id = candidate_row.id
                          AND fold.status = 'completed'
                    ) <> CASE
                        WHEN jsonb_typeof(candidate_row.payload->'folds') = 'array'
                        THEN jsonb_array_length(candidate_row.payload->'folds')
                        ELSE 0
                    END
                    OR EXISTS (
                        SELECT 1
                        FROM cv_fold_runs AS fold
                        WHERE fold.candidate_id = candidate_row.id
                          AND NOT EXISTS (
                              SELECT 1
                              FROM model_evaluations AS evaluation_row
                              WHERE evaluation_row.candidate_id = candidate_row.id
                                AND evaluation_row.evaluation_scope = 'cv_fold'
                                AND evaluation_row.status = 'completed'
                                AND evaluation_row.summary->>'fold_number'
                                    = fold.fold_number::text
                                AND EXISTS (
                                    SELECT 1
                                    FROM evaluation_metrics AS metric
                                    WHERE metric.model_evaluation_id = evaluation_row.id
                                )
                          )
                    )
                    OR NOT EXISTS (
                        SELECT 1
                        FROM model_evaluations AS evaluation_row
                        WHERE evaluation_row.candidate_id = candidate_row.id
                          AND evaluation_row.evaluation_scope = 'cv_aggregate'
                          AND evaluation_row.status = 'completed'
                          AND EXISTS (
                              SELECT 1
                              FROM evaluation_metrics AS metric
                              WHERE metric.model_evaluation_id = evaluation_row.id
                          )
                    )
                )
          )
    );
$$ LANGUAGE sql STABLE
"""

# --- ownership resolvers -----------------------------------------------------
# Each answers "does this row belong to a run whose evidence is locked?". They
# are STABLE so PostgreSQL can cache them within a statement.

# Parameters carry a `wanted_` prefix: an unqualified name that also exists as a
# column of a queried table would be resolved to the column, not the parameter.
RESOLVER_SQL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION pipeline_run_evidence_locked(wanted_run uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM experiments AS run
        WHERE run.id = wanted_run
          AND run.scientific_evidence_locked_at IS NOT NULL
    );
$$ LANGUAGE sql STABLE
""",
    """
CREATE OR REPLACE FUNCTION candidate_evidence_locked(wanted_candidate uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM experiment_candidates AS candidate_row
        JOIN experiments AS run ON run.id = candidate_row.experiment_id
        WHERE candidate_row.id = wanted_candidate
          AND run.scientific_evidence_locked_at IS NOT NULL
    );
$$ LANGUAGE sql STABLE
""",
    """
CREATE OR REPLACE FUNCTION evaluation_target_evidence_locked(
    wanted_candidate uuid, wanted_version uuid
)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM experiment_candidates AS candidate_row
        JOIN experiments AS run ON run.id = candidate_row.experiment_id
        WHERE candidate_row.id = wanted_candidate
          AND run.scientific_evidence_locked_at IS NOT NULL
        UNION ALL
        SELECT 1
        FROM model_versions AS version_row
        JOIN experiments AS run ON run.id = version_row.pipeline_run_id
        WHERE version_row.id = wanted_version
          AND run.scientific_evidence_locked_at IS NOT NULL
    );
$$ LANGUAGE sql STABLE
""",
    """
CREATE OR REPLACE FUNCTION model_evaluation_evidence_locked(wanted_evaluation uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM model_evaluations AS evaluation_row
        WHERE evaluation_row.id = wanted_evaluation
          AND evaluation_target_evidence_locked(
              evaluation_row.candidate_id, evaluation_row.model_version_id
          )
    );
$$ LANGUAGE sql STABLE
""",
    # A pipeline-run FeatureSetVersion is reachable only through the candidates
    # that were trained on it, or through the published ModelVersion.
    """
CREATE OR REPLACE FUNCTION feature_set_version_evidence_locked(wanted_version uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM experiment_candidates AS candidate_row
        JOIN experiments AS run ON run.id = candidate_row.experiment_id
        WHERE candidate_row.feature_set_version_id = wanted_version
          AND run.scientific_evidence_locked_at IS NOT NULL
        UNION ALL
        SELECT 1
        FROM model_versions AS version_row
        JOIN experiments AS run ON run.id = version_row.pipeline_run_id
        WHERE version_row.feature_set_version_id = wanted_version
          AND run.scientific_evidence_locked_at IS NOT NULL
    );
$$ LANGUAGE sql STABLE
""",
    """
CREATE OR REPLACE FUNCTION feature_evidence_locked(wanted_feature uuid)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
        FROM features AS feature_row
        WHERE feature_row.id = wanted_feature
          AND feature_set_version_evidence_locked(feature_row.feature_set_version_id)
    );
$$ LANGUAGE sql STABLE
""",
)


def _single_key_guard(function: str, resolver: str) -> str:
    """A guard whose owning run is reachable from one column, named by TG_ARGV[0].

    Both OLD and NEW are checked so a row cannot be moved into or out of a
    locked run either.
    """

    return f"""
CREATE OR REPLACE FUNCTION {function}()
RETURNS trigger AS $$
DECLARE
    owner uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        owner := (to_jsonb(OLD) ->> TG_ARGV[0])::uuid;
        IF {resolver}(owner) THEN
            RAISE EXCEPTION '{_LOCKED_MESSAGE}', TG_TABLE_NAME;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    owner := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
    IF {resolver}(owner) THEN
        RAISE EXCEPTION '{_LOCKED_MESSAGE}', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


# --- guard functions --------------------------------------------------------

SINGLE_KEY_GUARDS: dict[str, str] = {
    "prevent_locked_run_evidence_mutation": "pipeline_run_evidence_locked",
    "prevent_locked_candidate_evidence_mutation": "candidate_evidence_locked",
    "prevent_locked_metric_evidence_mutation": "model_evaluation_evidence_locked",
    "prevent_locked_feature_evidence_mutation": "feature_set_version_evidence_locked",
    "prevent_locked_feature_child_mutation": "feature_evidence_locked",
}

# ModelEvaluation owns two alternative parents, and on INSERT its own row is not
# visible yet, so it reads its own columns instead of a single key.
MODEL_EVALUATION_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION prevent_locked_evaluation_mutation()
RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'INSERT'
        AND evaluation_target_evidence_locked(OLD.candidate_id, OLD.model_version_id)
    THEN
        RAISE EXCEPTION '{_LOCKED_MESSAGE}', TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF evaluation_target_evidence_locked(NEW.candidate_id, NEW.model_version_id) THEN
        RAISE EXCEPTION '{_LOCKED_MESSAGE}', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# Canonical execution stages cannot be added after the lock. A stage that was
# already running may receive its one terminal update, after which it is frozen.
# Explicit verification stages and the advisory OpenAI audit are post-run
# evidence and stay outside this canonical freeze.
STAGE_RUN_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION prevent_locked_stage_run_mutation()
RETURNS trigger AS $$
DECLARE
    old_locked_at timestamptz;
    new_locked_at timestamptz;
    old_post_run_evidence boolean;
    new_post_run_evidence boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT run.{LOCK_COLUMN} INTO new_locked_at
        FROM experiments AS run
        WHERE run.id = NEW.pipeline_run_id;
        new_post_run_evidence := NEW.stage_type = 'verification'
            OR NEW.stage_key IN ({", ".join(f"'{key}'" for key in POST_RUN_STAGE_KEYS)});
        IF new_locked_at IS NOT NULL AND NOT new_post_run_evidence THEN
            RAISE EXCEPTION
                'pipeline_stage_runs is frozen: scientific evidence for this pipeline run is locked';
        END IF;
        RETURN NEW;
    END IF;

    SELECT run.{LOCK_COLUMN} INTO old_locked_at
    FROM experiments AS run
    WHERE run.id = OLD.pipeline_run_id;
    old_post_run_evidence := OLD.stage_type = 'verification'
            OR OLD.stage_key IN ({", ".join(f"'{key}'" for key in POST_RUN_STAGE_KEYS)});

    IF TG_OP = 'DELETE' THEN
        IF old_locked_at IS NOT NULL
            AND NOT old_post_run_evidence
            AND OLD.status IN ({", ".join(f"'{status}'" for status in FINALIZED_STAGE_STATUSES)})
        THEN
            RAISE EXCEPTION
                'pipeline_stage_runs is frozen: scientific evidence for this pipeline run is locked';
        END IF;
        RETURN OLD;
    END IF;

    SELECT run.{LOCK_COLUMN} INTO new_locked_at
    FROM experiments AS run
    WHERE run.id = NEW.pipeline_run_id;
    new_post_run_evidence := NEW.stage_type = 'verification'
        OR NEW.stage_key IN ({", ".join(f"'{key}'" for key in POST_RUN_STAGE_KEYS)});

    IF old_locked_at IS NOT NULL AND NOT old_post_run_evidence THEN
        IF OLD.status IN ({", ".join(f"'{status}'" for status in FINALIZED_STAGE_STATUSES)})
            OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
            OR NEW.stage_key IS DISTINCT FROM OLD.stage_key
            OR NEW.stage_type IS DISTINCT FROM OLD.stage_type
            OR NEW.status NOT IN ({", ".join(f"'{status}'" for status in FINALIZED_STAGE_STATUSES)})
        THEN
            RAISE EXCEPTION
                'pipeline_stage_runs is frozen: scientific evidence for this pipeline run is locked';
        END IF;
    END IF;

    IF new_locked_at IS NOT NULL AND NOT new_post_run_evidence
        AND (
            NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id
            OR old_post_run_evidence
        )
    THEN
        RAISE EXCEPTION
            'pipeline_stage_runs is frozen: scientific evidence for this pipeline run is locked';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# The stamp itself must be one-way, or tampering would just start by clearing it.
LOCK_STAMP_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION prevent_evidence_lock_reset()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.{LOCK_COLUMN} IS NOT NULL THEN
            RAISE EXCEPTION
                'experiments.{LOCK_COLUMN} cannot be set before scientific evidence is complete';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.{LOCK_COLUMN} IS NULL AND NEW.{LOCK_COLUMN} IS NOT NULL THEN
        IF NOT pipeline_run_scientific_evidence_complete(OLD.id) THEN
            RAISE EXCEPTION
                'experiments.{LOCK_COLUMN} cannot be set before scientific evidence is complete';
        END IF;
        NEW.{LOCK_COLUMN} := statement_timestamp();
    ELSIF OLD.{LOCK_COLUMN} IS NOT NULL
        AND NEW.{LOCK_COLUMN} IS DISTINCT FROM OLD.{LOCK_COLUMN} THEN
        RAISE EXCEPTION
            'experiments.{LOCK_COLUMN} cannot be changed once the run is locked';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# --- trigger placement ------------------------------------------------------

# table -> (guard function, key column)
SINGLE_KEY_EVIDENCE_TABLES: tuple[tuple[str, str, str], ...] = (
    (
        "data_quality_findings",
        "prevent_locked_run_evidence_mutation",
        "pipeline_run_id",
    ),
    (
        "data_preparation_decisions",
        "prevent_locked_run_evidence_mutation",
        "pipeline_run_id",
    ),
    (
        "preprocessing_steps",
        "prevent_locked_run_evidence_mutation",
        "pipeline_run_id",
    ),
    (
        "model_selection_decisions",
        "prevent_locked_run_evidence_mutation",
        "pipeline_run_id",
    ),
    (
        "experiment_test_predictions",
        "prevent_locked_run_evidence_mutation",
        "experiment_id",
    ),
    (
        "experiment_candidates",
        "prevent_locked_run_evidence_mutation",
        "experiment_id",
    ),
    (
        "model_hyperparameters",
        "prevent_locked_candidate_evidence_mutation",
        "candidate_id",
    ),
    ("cv_fold_runs", "prevent_locked_candidate_evidence_mutation", "candidate_id"),
    (
        "evaluation_metrics",
        "prevent_locked_metric_evidence_mutation",
        "model_evaluation_id",
    ),
    (
        "features",
        "prevent_locked_feature_evidence_mutation",
        "feature_set_version_id",
    ),
    ("feature_lineage", "prevent_locked_feature_child_mutation", "feature_id"),
    (
        "feature_transformations",
        "prevent_locked_feature_child_mutation",
        "feature_id",
    ),
)


def _trigger_name(table: str) -> str:
    return f"{table}_evidence_locked"


def evidence_lock_upgrade_statements() -> list[str]:
    """Trigger DDL Alembic 0043 installs. Requires the lock column to exist."""

    statements = [EVIDENCE_COMPLETE_SQL, *RESOLVER_SQL]
    for function, resolver in SINGLE_KEY_GUARDS.items():
        statements.append(_single_key_guard(function, resolver))
    statements.append(MODEL_EVALUATION_GUARD_SQL)
    statements.append(STAGE_RUN_GUARD_SQL)
    statements.append(LOCK_STAMP_GUARD_SQL)

    for table, function, key_column in SINGLE_KEY_EVIDENCE_TABLES:
        trigger = _trigger_name(table)
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        statements.append(
            f"""
CREATE TRIGGER {trigger}
BEFORE INSERT OR UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION {function}('{key_column}')
"""
        )

    evaluation_trigger = _trigger_name("model_evaluations")
    statements.append(f"DROP TRIGGER IF EXISTS {evaluation_trigger} ON model_evaluations")
    statements.append(
        f"""
CREATE TRIGGER {evaluation_trigger}
BEFORE INSERT OR UPDATE OR DELETE ON model_evaluations
FOR EACH ROW EXECUTE FUNCTION prevent_locked_evaluation_mutation()
"""
    )

    stage_trigger = _trigger_name("pipeline_stage_runs")
    statements.append(f"DROP TRIGGER IF EXISTS {stage_trigger} ON pipeline_stage_runs")
    statements.append(
        f"""
CREATE TRIGGER {stage_trigger}
BEFORE INSERT OR UPDATE OR DELETE ON pipeline_stage_runs
FOR EACH ROW EXECUTE FUNCTION prevent_locked_stage_run_mutation()
"""
    )

    statements.append("DROP TRIGGER IF EXISTS experiments_evidence_lock_stamp ON experiments")
    statements.append(
        """
CREATE TRIGGER experiments_evidence_lock_stamp
BEFORE INSERT OR UPDATE ON experiments
FOR EACH ROW EXECUTE FUNCTION prevent_evidence_lock_reset()
"""
    )
    return statements


def evidence_lock_downgrade_statements() -> list[str]:
    statements = [
        f"DROP TRIGGER IF EXISTS {_trigger_name(table)} ON {table}"
        for table, _function, _key in SINGLE_KEY_EVIDENCE_TABLES
    ]
    statements.append(
        f"DROP TRIGGER IF EXISTS {_trigger_name('model_evaluations')} ON model_evaluations"
    )
    statements.append(
        f"DROP TRIGGER IF EXISTS {_trigger_name('pipeline_stage_runs')} ON pipeline_stage_runs"
    )
    statements.append("DROP TRIGGER IF EXISTS experiments_evidence_lock_stamp ON experiments")
    for guard in (
        *SINGLE_KEY_GUARDS,
        "prevent_locked_evaluation_mutation",
        "prevent_locked_stage_run_mutation",
        "prevent_evidence_lock_reset",
    ):
        statements.append(f"DROP FUNCTION IF EXISTS {guard}()")
    # Dependents first: feature_evidence_locked calls feature_set_version_evidence_locked,
    # and model_evaluation_evidence_locked calls evaluation_target_evidence_locked.
    for resolver in (
        "feature_evidence_locked(uuid)",
        "feature_set_version_evidence_locked(uuid)",
        "model_evaluation_evidence_locked(uuid)",
        "evaluation_target_evidence_locked(uuid, uuid)",
        "candidate_evidence_locked(uuid)",
        "pipeline_run_evidence_locked(uuid)",
        "pipeline_run_scientific_evidence_complete(uuid)",
    ):
        statements.append(f"DROP FUNCTION IF EXISTS {resolver}")
    return statements
