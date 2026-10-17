"""Monitoring windows and outcomes (P7.4-A): vocabulary, DB CHECK literals, reads, plain words.

For the model in use, each calendar week (UTC, Monday 00:00) of completed scorings is one
window: its scored inputs compared with the training partition (PSI per original feature), its
scores compared with the winner's out-of-fold CV scores, and — once a person links a file of
real outcomes — how the model actually did on the joined rows. A window is recomputed by
inserting a new ``revision`` (never an UPDATE); reads use the highest one.

Drift means "the inputs look different from training", not "the model is wrong". Outcomes are
real production labels, never the final test set; no holdout figure appears here. Stored
aggregates are of the workspace's own data under the same tenant boundary (category labels at
most 60 characters, top 10 per feature, each seen at least 5 times and in 1% of training rows).
Summaries are built in code from numbers; the column names and category values in them come from
the user's files (untrusted text). With AI off this deterministic result is the whole response:
monitoring never retrains, switches a model or acts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.data_plane import sql_in_clause
from app.domain.decision_records import RATIONALE_MAX_CHARS

WINDOW_STATUSES = ("ok", "watch", "alert", "not_enough_data")
OUTCOME_STATUSES = ("not_linked", "no_key_in_scores", "key_not_unique", "not_enough_labels", "measured")
WindowStatus = Literal["ok", "watch", "alert", "not_enough_data"]
OverviewStatus = Literal["ok", "watch", "alert", "not_enough_data", "no_windows", "no_model_in_use"]

COLUMN_NAME_MAX = 256
SUMMARY_MAX_CHARS = 1000
JSON_MAX_BYTES = 32768  # per jsonb column of a window
WINDOWS_LISTED = 12
FEATURE_SERIES_MAX = 8  # features in the overview's PSI-by-week series
OUTCOME_SETS_LISTED = 50
OUTCOME_SETS_TRIED = 5  # newest linked sets a window tries, newest first
WINDOW_SCORINGS_MAX = 20  # newest completed scorings (distinct input files) per window
WINDOW_ROWS_MAX = 50_000  # sampled input rows per window (fixed seed)
SCORED_ROWS_MAX = 200_000  # scored rows kept per window (sampled by key, fixed order)
SCORED_FILE_MAX_BYTES = 256 * 1024 * 1024
WINDOW_BYTES_MAX = 512 * 1024 * 1024  # scored files read per window, newest first
OUTCOME_ROWS_MAX = 2_000_000  # rows kept per outcome file (sampled by key, fixed order)
LINKS_PER_HOUR = 10  # outcome links per project per hour

# Stable codes.
OUTCOMES_FROM_TRAINING_DATA = "outcomes_from_training_data"
OUTCOME_COLUMN_UNKNOWN = "outcome_column_unknown"
ALREADY_LINKED = "already_linked"
MONITORING_DISABLED = "monitoring_disabled"
INPUTS_UNREADABLE = "monitoring_inputs_unreadable"
SCORES_UNREADABLE = "monitoring_scores_unreadable"
MANIFEST_UNREADABLE = "monitoring_manifest_unreadable"
RESULT_TOO_LARGE = "monitoring_result_too_large"
MONITORING_FAILED = "monitoring_failed"

# DB backstops (identical literals in Alembic 0080).
CK_MONITORING_WINDOWS_STATUS = sql_in_clause("status", WINDOW_STATUSES)
CK_MONITORING_WINDOWS_SUMMARY_CODE = "summary_code ~ '^[a-z][a-z0-9_]{0,63}$'"
CK_MONITORING_WINDOWS_SUMMARY = f"char_length(btrim(summary)) > 0 AND char_length(summary) <= {SUMMARY_MAX_CHARS}"
CK_MONITORING_WINDOWS_COUNTS = "revision >= 1 AND n_rows >= 0 AND n_batch_predictions >= 1"
# One calendar week starting Monday 00:00 UTC (hours, not days: timestamptz + days follows the session TimeZone).
CK_MONITORING_WINDOWS_WEEK = (
    "window_end = window_start + interval '168 hours' "
    "AND EXTRACT(ISODOW FROM window_start AT TIME ZONE 'UTC') = 1 "
    "AND date_trunc('day', window_start AT TIME ZONE 'UTC') = window_start AT TIME ZONE 'UTC'"
)
CK_MONITORING_WINDOWS_JSON = " AND ".join(
    f"jsonb_typeof({column}) = 'object' AND octet_length(CAST({column} AS TEXT)) <= {JSON_MAX_BYTES}"
    for column in ("input_drift", "prediction_drift", "outcomes")
)
# D5b (ADR 0012 D4): per-project thresholds, versioned append-only rows; no row = the defaults
# (the engine's PSI_WATCH / PSI_ALERT, no floors). Fixed bounds, identical literals in Alembic 0089.
PSI_WATCH_MIN, PSI_WATCH_MAX = 0.05, 0.5
PSI_ALERT_MAX = 1.0
FLOOR_MIN, FLOOR_MAX = 0.01, 0.99
THRESHOLDS_UNCHANGED = "thresholds_unchanged"
BELOW_FLOOR = "below_floor"  # summary code of an alert raised by a floor, not by drift
THRESHOLDS_USED_MAX_BYTES = 1024
CK_MONITORING_THRESHOLDS_BOUNDS = (
    f"psi_watch >= {PSI_WATCH_MIN} AND psi_watch <= {PSI_WATCH_MAX} "
    f"AND psi_alert > psi_watch AND psi_alert <= {PSI_ALERT_MAX} "
    f"AND (precision_floor IS NULL OR (precision_floor >= {FLOOR_MIN} AND precision_floor <= {FLOOR_MAX})) "
    f"AND (recall_floor IS NULL OR (recall_floor >= {FLOOR_MIN} AND recall_floor <= {FLOOR_MAX}))"
)
CK_MONITORING_THRESHOLDS_VERSION = "version >= 1"
CK_MONITORING_WINDOWS_THRESHOLDS_USED = (
    "thresholds_used IS NULL OR (jsonb_typeof(thresholds_used) = 'object' "
    f"AND octet_length(CAST(thresholds_used AS TEXT)) <= {THRESHOLDS_USED_MAX_BYTES})"
)
CK_MONITORING_OUTCOME_SETS_COLUMNS = (
    f"char_length(key_column) BETWEEN 1 AND {COLUMN_NAME_MAX} "
    f"AND char_length(outcome_column) BETWEEN 1 AND {COLUMN_NAME_MAX} AND key_column <> outcome_column"
)

NOTES = (
    "Drift means the inputs look different from training; it does not mean the model is wrong.",
    "Real performance appears only after outcomes are linked; outcomes usually arrive weeks late, "
    "so recent weeks have fewer.",
    "Outcomes are real results of scored rows, not the final test set; nothing here is used to "
    "choose or retrain a model.",
)
DATE_SENTENCE = ("Dates in new files are later than in training, as expected over time; the model uses this date "
                 "as an input, so it is working outside the dates it learned from.")
_UNTRUSTED = "Untrusted user data: column names and values from the user's files; display as plain text."


# --- requests and reads -------------------------------------------------------------------------


class OutcomeSetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID = Field(description="A dataset of this project uploaded with POST /v1/datasets.")
    key_column: str = Field(min_length=1, max_length=COLUMN_NAME_MAX,
                            description="Column holding the same key as the scored rows' entity column.")
    outcome_column: str = Field(min_length=1, max_length=COLUMN_NAME_MAX, description="The real outcome.")


class OutcomeSetRead(BaseModel):
    id: UUID
    project_id: UUID
    dataset_id: UUID
    dataset_name: str | None = Field(default=None, description="Untrusted text (the uploaded file's name).")
    key_column: str = Field(description="Untrusted text (a column name of the user's file).")
    outcome_column: str = Field(description="Untrusted text (a column name of the user's file).")
    linked_at: datetime
    linked_by_user_id: UUID | None = Field(default=None, description="Null for service tokens.")


class OutcomeSetList(BaseModel):
    project_id: UUID
    items: list[OutcomeSetRead]
    notes: list[str]


class MonitoringThresholds(BaseModel):
    """The values in force: PSI watch / alert and the precision / recall floors are the project's
    (D5b; ``version`` 0 = the defaults, never changed); the rest is code-owned."""

    psi_watch: float
    psi_alert: float
    precision_floor: float | None = Field(default=None, description="Null: off. Binary models, real outcomes.")
    recall_floor: float | None = Field(default=None, description="Null: off. Binary models, real outcomes.")
    version: int = Field(default=0, description="0: the defaults (never changed for this project).")
    changed_at: datetime | None = None
    changed_by_user_id: UUID | None = Field(default=None, description="Never shown to service tokens.")
    significance_level: float = Field(description="A feature counts only when a G-test on its bucket counts has p "
                                                  "below this level divided by the features compared.")
    flagged_share_relative_watch: float
    flagged_share_absolute_watch: float
    min_rows: int
    min_labels: int
    min_per_class: int


class MonitoringThresholdsUsed(BaseModel):
    """The values one window was computed with (stored on it; later changes never alter it)."""

    version: int
    psi_watch: float
    psi_alert: float
    precision_floor: float | None = None
    recall_floor: float | None = None


class MonitoringThresholdValues(BaseModel):
    psi_watch: float
    psi_alert: float
    precision_floor: float | None
    recall_floor: float | None


class MonitoringThresholdBounds(BaseModel):
    psi_watch_min: float = PSI_WATCH_MIN
    psi_watch_max: float = PSI_WATCH_MAX
    psi_alert_max: float = Field(default=PSI_ALERT_MAX, description="And above psi_watch.")
    floor_min: float = FLOOR_MIN
    floor_max: float = FLOOR_MAX


class MonitoringThresholdsWrite(BaseModel):
    """All four values are required (a floor is ``null`` to switch it off)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    psi_watch: float = Field(ge=PSI_WATCH_MIN, le=PSI_WATCH_MAX)
    psi_alert: float = Field(gt=PSI_WATCH_MIN, le=PSI_ALERT_MAX, description="Above psi_watch.")
    precision_floor: float | None = Field(ge=FLOOR_MIN, le=FLOOR_MAX)
    recall_floor: float | None = Field(ge=FLOOR_MIN, le=FLOOR_MAX)
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX_CHARS, description="Your reason (History).")

    @model_validator(mode="after")
    def _alert_above_watch(self) -> "MonitoringThresholdsWrite":
        if self.psi_alert <= self.psi_watch:
            raise ValueError("psi_alert must be above psi_watch")
        return self


class MonitoringThresholdsRead(MonitoringThresholdValues):
    project_id: UUID
    version: int = Field(description="0 when never changed (the defaults); send `If-Match: \"<version>\"`.")
    etag: str
    defaults: MonitoringThresholdValues
    bounds: MonitoringThresholdBounds
    changed_at: datetime | None = None
    changed_by_user_id: UUID | None = Field(default=None, description="Never shown to service tokens.")
    can_edit: bool = Field(description="You are a workspace owner or administrator (never platform staff or a token).")
    notes: list[str]


class MonitoringFinding(BaseModel):
    code: Literal["inputs_look_different", "scores_look_different", "performance_below_floor"]
    severity: Literal["alert"] = "alert"
    title: str = Field(description=_UNTRUSTED)
    body: str = Field(description=_UNTRUSTED)


class MonitoringWindowSummary(BaseModel):
    window_start: datetime
    window_end: datetime
    revision: int
    model_version_id: UUID
    model_release_id: UUID | None
    status: WindowStatus
    summary_code: str
    summary: str = Field(description=_UNTRUSTED)
    n_rows: int
    n_batch_predictions: int
    max_psi: float | None
    prediction_psi: float | None
    outcomes_status: str
    n_labelled: int | None
    coverage: float | None = Field(description="Labelled rows / scored customers with a key in this week.")
    outcome_metric: str | None
    outcome_metric_value: float | None
    outcome_precision: float | None = Field(
        default=None, description="Binary, outcomes measured: precision at the locked threshold on the joined rows "
                                  "(real outcomes, never the final test). Null otherwise.")
    outcome_recall: float | None = Field(
        default=None, description="Binary, outcomes measured: recall at the locked threshold on the joined rows.")
    thresholds_used: MonitoringThresholdsUsed | None = Field(
        default=None, description="The values this week was computed with; null: computed before they were "
                                  "recorded (the defaults, no floors).")
    floor_status: Literal["below", "not_judged", "ok"] | None = Field(
        default=None, description="Precision / recall floors on real outcomes: `below` (an alert), `not_judged` "
                                  "(outcomes not linked or measured, or too few rows to say), `ok`; null: no floor.")
    computed_at: datetime


class MonitoringPsiPoint(BaseModel):
    window_start: datetime
    psi: float | None = Field(description="Null when the feature was not among the week's shown features.")
    status: str | None = None


class MonitoringFeatureSeries(BaseModel):
    name: str = Field(description=_UNTRUSTED)
    kind: str | None = None
    points: list[MonitoringPsiPoint] = Field(description="One per listed window, oldest first.")


class MonitoringWindowRead(MonitoringWindowSummary):
    input_drift: dict[str, Any] = Field(description="Per feature (top 20 by PSI, then dates): psi, G-test p, status, "
                                                    "kind, missing shares, mean change, the category bucket that moved "
                                                    "most or date ranges. Names and category values: " + _UNTRUSTED)
    feature_sentences: list[str] = Field(description=_UNTRUSTED)
    prediction_drift: dict[str, Any]
    outcomes: dict[str, Any]
    outcome_sentences: list[str] = Field(default_factory=list, description="Outcomes in plain words, from numbers.")
    finding: MonitoringFinding | None
    thresholds: MonitoringThresholds
    notes: list[str]


class MonitoringOverviewRead(BaseModel):
    project_id: UUID
    enabled: bool
    model_version_id: UUID | None
    model_version: str | None = Field(default=None, description="The model version's label (e.g. v3).")
    model_release_id: UUID | None
    status: OverviewStatus
    latest: MonitoringWindowSummary | None
    windows: list[MonitoringWindowSummary] = Field(description="Last 12 windows, newest first.")
    feature_psi_series: list[MonitoringFeatureSeries] = Field(
        default_factory=list,
        description=f"PSI by week for the {FEATURE_SERIES_MAX} counted features with the highest PSI in any listed "
                    "week (dates are never counted), over the same windows, oldest first.")
    finding: MonitoringFinding | None
    thresholds: MonitoringThresholds
    notes: list[str]


# --- plain words (numbers only; names are the user's column names, already sanitized) ----------


def _counted(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in features if row.get("kind") != "date" and row.get("counted", True)]


def _moved(features: list[dict[str, Any]], limit: int = 3, statuses: tuple[str, ...] = ("alert", "watch")) -> list[dict]:
    return [row for row in _counted(features) if row.get("status") in statuses][:limit]


def _scores_moved(prediction: dict[str, Any]) -> str | None:
    if not prediction.get("available"):
        return None
    parts = []
    if prediction.get("status") in ("watch", "alert"):
        parts.append(f"the scores moved (PSI {float(prediction['psi']):.2f})")
    if prediction.get("flagged_status") == "watch":
        parts.append(f"the share flagged moved from {float(prediction['flagged_share_reference']):.0%} in "
                     f"cross-validation to {float(prediction['flagged_share']):.0%}")
    return " and ".join(parts) or None


def summary_text(code: str, *, n_rows: int, min_rows: int, input_drift: dict[str, Any],
                 prediction_drift: dict[str, Any], sampled_rows: int | None, scorings_total: int = 0) -> str:
    compared = int(input_drift.get("n_rows_compared") or 0)
    if code == "no_reference":
        text = ("This model was saved before monitoring existed, so there is nothing from training to compare "
                "with; train it again to monitor it.")
    elif code == "inputs_unavailable":
        text = "The files scored this week are no longer available, so their inputs cannot be compared with training."
    elif code == "too_few_rows":
        text = (f"Only {compared:,} rows could be compared this week; at least {min_rows:,} are needed before drift "
                "is judged.")
    else:
        features = _counted(list(input_drift.get("features") or []))
        moved = _moved(features)
        scores = _scores_moved(prediction_drift)
        if not moved and not scores:
            top = features[0] if features else None
            text = f"The {compared:,} compared rows look like training"
            text += f" (largest PSI {float(top['psi']):.2f}, {top['name']})." if top else "."
        else:
            parts = []
            if moved:
                names = ", ".join(f"{row['name']} (PSI {float(row['psi']):.2f})" for row in moved)
                parts.append(f"Inputs look different from training: {names}.")
            if scores:
                parts.append(scores[0].upper() + scores[1:] + ".")
            parts.append("Drift means the inputs look different from training; it does not mean the model is wrong.")
            text = " ".join(parts)
    if sampled_rows:
        text += f" Based on {sampled_rows:,} sampled rows."
    if scorings_total > WINDOW_SCORINGS_MAX:
        text += f" Based on the newest {WINDOW_SCORINGS_MAX} scorings of {scorings_total} this week."
    return text[:SUMMARY_MAX_CHARS]


def feature_sentence(row: dict[str, Any], watch: float | None = None) -> str:
    """One feature in plain words, from its numbers; never a cause. ``watch``: the window's PSI watch
    line (default: the engine's ``PSI_WATCH``)."""

    if watch is None:
        from app.engine.serving.monitoring import PSI_WATCH

        watch = PSI_WATCH

    name = row.get("name")
    if row.get("kind") == "date":
        span, ref = row.get("range") or [None, None], row.get("range_reference") or [None, None]
        text = f"{name}: dates from {span[0]} to {span[1]} vs {ref[0]} to {ref[1]} in training."
        return text + (f" {DATE_SENTENCE}" if row.get("later_than_training") else "")
    status = row.get("status")
    amount = "a lot" if status == "alert" else "somewhat" if status == "watch" else "little"
    text = f"{name} moved {amount} (PSI {float(row.get('psi') or 0.0):.2f})"
    if status == "ok" and float(row.get("psi") or 0.0) >= watch:
        text += ", within what chance allows for this many rows and features"
    change = row.get("top_change") or {}
    if row.get("kind") == "numeric" and row.get("mean") is not None and row.get("mean_reference") is not None:
        text += f": the average is {float(row['mean']):.4g} vs {float(row['mean_reference']):.4g} in training"
    elif change.get("share") is not None and change.get("share_reference") is not None:
        what = {"category": f"'{change.get('value')}'", "other": "values that were rare or unseen in training",
                "missing": "missing values"}[change.get("bucket", "other")]
        text += (f": {what} {'are' if change.get('bucket') != 'category' else 'is'} {float(change['share']):.0%} "
                 f"of rows vs {float(change['share_reference']):.0%} in training")
    missing, reference = row.get("missing_share"), row.get("missing_share_reference")
    if missing is not None and reference is not None and abs(float(missing) - float(reference)) >= 0.05 \
            and change.get("bucket") != "missing":
        text += f"; {float(missing):.0%} of values are missing vs {float(reference):.0%} in training"
    return text + "."


def outcome_sentences(outcomes: dict[str, Any], thresholds: MonitoringThresholds) -> list[str]:
    """Outcomes in plain words, from the stored numbers (never a cause, never the final test)."""

    status = outcomes.get("status")
    out: list[str] = []
    if status == "not_linked":
        out.append("No real outcomes are linked yet.")
    elif status == "no_key_in_scores":
        out.append("The scored files have no key column to join real outcomes on.")
    elif status == "key_not_unique":
        side = "a scored file" if outcomes.get("side") == "scores" else "the outcome file"
        out.append(f"A key appears more than once in {side}, so outcomes cannot be joined unambiguously.")
    elif status == "not_enough_labels":
        out.append(f"Only {int(outcomes.get('n_labelled') or 0):,} scored customers have a recognised real outcome; "
                   f"at least {thresholds.min_labels:,} are needed (and {thresholds.min_per_class} of each class).")
    elif status == "measured":
        n, keyed = int(outcomes.get("n_labelled") or 0), int(outcomes.get("n_keyed") or 0)
        coverage = outcomes.get("coverage")
        out.append(f"{n:,} of {keyed:,} scored customers with a key have a real outcome"
                   + (f" ({float(coverage):.0%})." if coverage is not None else "."))
        if coverage is not None and float(coverage) < 1.0:
            out.append("Customers with outcomes may differ from those without, so these numbers may not describe "
                       "everyone scored.")
        rate, cv_rate = outcomes.get("label_positive_rate"), outcomes.get("positive_rate_reference")
        if rate is not None:
            out.append(f"{float(rate):.0%} of them had the outcome" + (
                f", vs {float(cv_rate):.0%} of the training rows in cross-validation." if cv_rate is not None else "."))
        if outcomes.get("metric") and outcomes.get("metric_value") is not None:
            text = f"{outcomes['metric']} on them: {float(outcomes['metric_value']):.3f}"
            cv = outcomes.get("cv_reference") or {}
            if cv.get("value") is not None:
                text += f" (cross-validation: {float(cv['value']):.3f})"
            out.append(text + ".")
    out.extend(floor_sentences(outcomes.get("floors")))
    unrecognised = int(outcomes.get("unrecognised_outcome_values") or 0)
    if unrecognised:
        out.append(f"{unrecognised:,} outcome values did not match the training outcome labels and were left out.")
    if outcomes.get("refused_sets"):
        out.append("A newer outcome file was skipped because a key appears in it more than once.")
    return out


_FLOOR_NOT_JUDGED = {
    "outcomes_not_measured": "the {name} floor ({floor:.0%}) is not judged until enough real outcomes are linked "
                             "and measured.",
    "not_binary": "the {name} floor ({floor:.0%}) applies to yes/no models only; not judged.",
    "no_flagged_rows": "no labelled row was flagged, so the precision floor ({floor:.0%}) is not judged.",
    "no_positive_outcomes": "no labelled row had the outcome, so the recall floor ({floor:.0%}) is not judged.",
    "confusion_unavailable": "the {name} floor ({floor:.0%}) is not judged: this week's counts of right and wrong "
                             "flags are not available.",
    "too_few_flagged_rows": "too few labelled rows were flagged to judge the precision floor ({floor:.0%}).",
    "too_few_positive_outcomes": "too few labelled rows had the outcome to judge the recall floor ({floor:.0%}).",
}


def floor_sentences(checks: dict[str, Any] | None) -> list[str]:
    """The precision / recall floors in plain words, from the stored checks (real outcomes)."""

    out: list[str] = []
    for name in ("precision", "recall"):
        check = (checks or {}).get(name)
        if not isinstance(check, dict) or check.get("floor") is None:
            continue
        floor, value, status = float(check["floor"]), check.get("value"), check.get("status")
        shown = (f"{name.capitalize()} on real outcomes is {float(value):.0%} ({int(check.get('successes') or 0):,} "
                 f"of {int(check.get('trials') or 0):,})" if value is not None else "")
        if status == "below":
            out.append(f"{shown}, below the floor of {floor:.0%} set for this project, and clearly so for this many "
                       "rows.")
        elif status == "ok":
            out.append(f"{shown}, at or above the floor of {floor:.0%} set for this project.")
        elif check.get("reason_code") == "within_chance":
            out.append(f"{shown}, under the floor of {floor:.0%}, but within what chance allows for this many rows "
                       "(not judged).")
        else:
            text = _FLOOR_NOT_JUDGED.get(str(check.get("reason_code")), _FLOOR_NOT_JUDGED["outcomes_not_measured"])
            text = text.format(name=name, floor=floor)
            out.append(text[0].upper() + text[1:])
    return out


def floor_summary(checks: dict[str, Any] | None) -> str:
    """The sentence a window's summary gains when a floor is judged below (empty otherwise)."""

    below = [name for name in ("precision", "recall") if ((checks or {}).get(name) or {}).get("status") == "below"]
    if not below:
        return ""
    parts = [f"{name} {float(checks[name]['value']):.0%} (floor {float(checks[name]['floor']):.0%})" for name in below]
    return "Real outcomes are below the floor set for this project: " + ", ".join(parts) + "."


def floor_status(outcomes: dict[str, Any]) -> str | None:
    """``below`` if any floor is judged below, else ``not_judged`` if any is not judged, else ``ok``;
    None when the window stored no floor."""

    checks = outcomes.get("floors")
    statuses = [(checks.get(name) or {}).get("status") for name in ("precision", "recall")
                if isinstance(checks, dict) and isinstance(checks.get(name), dict)]
    if not statuses:
        return None
    return "below" if "below" in statuses else "not_judged" if "not_judged" in statuses else "ok"


def outcome_rates(outcomes: dict[str, Any]) -> tuple[float | None, float | None]:
    """(precision, recall) of a measured binary window, from its stored outcome metrics."""

    metrics = outcomes.get("metrics") if outcomes.get("status") == "measured" else None
    if not isinstance(metrics, dict):
        return None, None
    values = [metrics.get(key) for key in ("precision", "recall")]
    rates = [float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
             for value in values]
    return rates[0], rates[1]


def feature_psi_series(windows: list[tuple[datetime, dict[str, Any]]],
                       limit: int = FEATURE_SERIES_MAX) -> list[MonitoringFeatureSeries]:
    """PSI by week per counted feature (dates never), from each window's stored input drift: the
    ``limit`` features with the highest PSI in any week, ties by name; points oldest first, null
    where the feature was not among that week's shown features."""

    ordered = sorted(windows, key=lambda item: item[0])
    weeks: list[dict[str, dict[str, Any]]] = []
    peak: dict[str, float] = {}
    kinds: dict[str, Any] = {}
    for _start, drift in ordered:
        rows = {str(row["name"]): row for row in _counted(list((drift or {}).get("features") or []))
                if row.get("name")}
        weeks.append(rows)
        for name, row in rows.items():
            peak[name] = max(peak.get(name, 0.0), float(row.get("psi") or 0.0))
            kinds.setdefault(name, row.get("kind"))
    chosen = sorted(peak, key=lambda name: (-peak[name], name))[:limit]
    return [
        MonitoringFeatureSeries(name=name, kind=kinds.get(name), points=[
            MonitoringPsiPoint(window_start=start, psi=week[name].get("psi") if name in week else None,
                               status=week[name].get("status") if name in week else None)
            for (start, _drift), week in zip(ordered, weeks)
        ])
        for name in chosen
    ]


def finding_for(status: str, summary: str, input_drift: dict[str, Any], prediction_drift: dict[str, Any],
                outcomes: dict[str, Any] | None = None) -> MonitoringFinding | None:
    """The one deterministic finding of an ``alert`` window (no proposal, no agent, no retrain).
    Date inputs never raise it; a floor judged below raises it when no drift alert did."""

    if status != "alert":
        return None
    alerted = _moved(list(input_drift.get("features") or []), statuses=("alert",))
    body = (summary + " Nothing was retrained or switched; check whether the data source or the population "
            "scored has changed before acting.")
    if (not alerted and (prediction_drift or {}).get("status") != "alert"
            and floor_status(outcomes or {}) == "below"):
        return MonitoringFinding(code="performance_below_floor", title="Real outcomes are below the floor set for "
                                 "this project", body=body[:SUMMARY_MAX_CHARS])
    if alerted:
        names = ", ".join(str(row["name"]) for row in alerted)
        return MonitoringFinding(code="inputs_look_different", title=f"Inputs look different from training: {names}",
                                 body=body[:SUMMARY_MAX_CHARS])
    return MonitoringFinding(code="scores_look_different", title="Scores look different from training",
                             body=body[:SUMMARY_MAX_CHARS])
