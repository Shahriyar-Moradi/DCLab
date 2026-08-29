"""Client-safe run-stage mapping for Labs open-ingest jobs."""

from app.domain.lab_run_stages import (
    ANALYZING,
    CLEANING,
    COMPLETED,
    CROSS_VALIDATION,
    FAILED,
    FEATURE_ENGINEERING,
    PREPROCESSING,
    PROCESSING_HEADLINE,
    QUEUED,
    TRAINING,
    client_stage,
    headline,
    lifecycle_status,
    public_pipeline_status,
    stage_after,
    steps_for,
)
from app.translation.banned_terms import find_banned_terms

CLIENT_STATES = {"queued", "processing", "completed", "failed"}


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


def test_training_is_rewritten_before_it_reaches_the_client():
    assert client_stage(TRAINING) == "processing"
    assert public_pipeline_status(TRAINING) == "processing"
    assert public_pipeline_status(CROSS_VALIDATION) == "processing"
    assert public_pipeline_status(ANALYZING) == "processing"
    assert "training" not in public_pipeline_status(TRAINING)


def test_client_never_receives_a_stage_checklist():
    assert steps_for(QUEUED) == []
    assert steps_for(ANALYZING) == []
    assert steps_for(TRAINING) == []
    assert steps_for(COMPLETED) == []
    assert steps_for(FAILED) == []


def test_in_progress_headline_is_a_single_processing_line():
    assert headline(QUEUED) == PROCESSING_HEADLINE
    assert headline(ANALYZING) == PROCESSING_HEADLINE
    assert headline(TRAINING) == PROCESSING_HEADLINE
    assert headline(COMPLETED) == ""
    assert headline(FAILED) == ""
    assert find_banned_terms(PROCESSING_HEADLINE) == []


def test_stage_after_marks_later_pipeline_work():
    assert stage_after(PREPROCESSING, CLEANING) is True
    assert stage_after(CLEANING, CLEANING) is False
    assert stage_after(COMPLETED, TRAINING) is True
    assert stage_after(FAILED, CLEANING) is False
