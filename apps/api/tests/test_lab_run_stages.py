"""Client-safe run-stage mapping for Labs open-ingest jobs."""

from app.domain.lab_run_stages import (
    ANALYZING,
    CLEANING,
    COMPLETED,
    CROSS_VALIDATION,
    EVALUATING,
    FAILED,
    FEATURE_ENGINEERING,
    INGESTING,
    PREPROCESSING,
    QUEUED,
    SPLITTING,
    TRAINING,
    client_error_message,
    client_stage,
    headline,
    lifecycle_status,
    milestone_for,
    public_pipeline_status,
    stage_after,
    steps_for,
)
from app.translation.banned_terms import find_banned_terms

CLIENT_STATES = {"queued", "processing", "completed", "failed"}
INTERNAL_LEAKS = (
    "ingesting",
    "feature_engineering",
    "preprocessing",
    "splitting",
    "cross_validation",
    "training",
    "evaluating",
    "predicting",
)


def test_lifecycle_status_views_fine_grained_stages_as_processing():
    assert lifecycle_status(QUEUED) == "queued"
    assert lifecycle_status(ANALYZING) == "processing"
    assert lifecycle_status(TRAINING) == "processing"
    assert lifecycle_status(COMPLETED) == "completed"
    assert lifecycle_status(FAILED) == "failed"


def test_client_view_never_emits_internal_stages_or_banned_vocabulary():
    for stored in (QUEUED, ANALYZING, CLEANING, FEATURE_ENGINEERING, CROSS_VALIDATION, TRAINING, COMPLETED, FAILED):
        token = client_stage(stored)
        public = public_pipeline_status(stored)
        assert token in CLIENT_STATES
        assert public in CLIENT_STATES
        assert token == lifecycle_status(stored)
        assert public == lifecycle_status(stored)
        assert find_banned_terms(token) == []
        assert find_banned_terms(headline(stored)) == []
        assert find_banned_terms(public) == []
        assert find_banned_terms(milestone_for(stored)) == []
        for step in steps_for(stored):
            blob = f"{step['id']} {step['label']} {step['state']}"
            assert find_banned_terms(blob) == []
            for leak in INTERNAL_LEAKS:
                assert leak not in blob


def test_training_is_rewritten_before_it_reaches_the_client():
    assert client_stage(TRAINING) == "processing"
    assert public_pipeline_status(TRAINING) == "processing"
    assert public_pipeline_status(CROSS_VALIDATION) == "processing"
    assert public_pipeline_status(ANALYZING) == "processing"
    assert "training" not in public_pipeline_status(TRAINING)
    assert "training" not in milestone_for(TRAINING).lower()
    assert "cross_validation" not in milestone_for(CROSS_VALIDATION)


def test_client_checklist_has_five_milestones_driven_by_real_stages():
    queued = steps_for(QUEUED)
    assert [row["label"] for row in queued] == [
        "Uploading",
        "Analyzing your data",
        "Preparing your data",
        "Building your model",
        "Finishing up",
    ]
    assert [row["state"] for row in queued] == ["current", "upcoming", "upcoming", "upcoming", "upcoming"]
    assert milestone_for(QUEUED) == "Uploading"

    analyzing = steps_for(INGESTING)
    assert analyzing[0]["state"] == "done"
    assert analyzing[1]["state"] == "current"
    assert analyzing[1]["label"] == "Analyzing your data"
    assert milestone_for(ANALYZING) == "Analyzing your data"

    preparing = steps_for(PREPROCESSING)
    assert preparing[2]["state"] == "current"
    assert milestone_for(CLEANING) == "Preparing your data"

    building = steps_for(TRAINING)
    assert building[3]["state"] == "current"
    assert all(row["state"] == "done" for row in building[:3])
    assert milestone_for(SPLITTING) == "Building your model"
    assert milestone_for(CROSS_VALIDATION) == "Building your model"

    finishing = steps_for(EVALUATING)
    assert finishing[4]["state"] == "current"
    assert milestone_for(EVALUATING) == "Finishing up"

    done = steps_for(COMPLETED)
    assert all(row["state"] == "done" for row in done)
    assert milestone_for(COMPLETED) == ""
    assert headline(FAILED) == ""


def test_in_progress_headline_follows_the_mapped_milestone():
    assert headline(QUEUED) == "Uploading"
    assert headline(ANALYZING) == "Analyzing your data"
    assert headline(TRAINING) == "Building your model"
    assert headline(COMPLETED) == ""
    assert headline(FAILED) == ""
    assert find_banned_terms(headline(TRAINING)) == []


def test_client_error_message_is_plain_language_and_drops_column_dumps():
    assert client_error_message("no label column found") == "We could not find an outcome column to analyze."
    assert "tenure" not in client_error_message("no label column found: dropped columns tenure, notes")
    assert client_error_message("unexpected error: disk full") == "disk full"
    assert find_banned_terms(client_error_message("no label column found")) == []


def test_stage_after_marks_later_pipeline_work():
    assert stage_after(PREPROCESSING, CLEANING) is True
    assert stage_after(CLEANING, CLEANING) is False
    assert stage_after(COMPLETED, TRAINING) is True
    assert stage_after(FAILED, CLEANING) is False
