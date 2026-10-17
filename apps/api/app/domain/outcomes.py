"""Business view (P7.6-A): what the model in use does, in plain words, for business users.

``GET /v1/projects/{project_id}/outcomes`` is a read model over facts DCLab already keeps: the
project's model in use (the ``champion_model`` ref), its approval for scoring (P7.2-A), the
model card's single final test of that model (P4.11-A) and the latest completed list scored
with it (P4.9-A). Every sentence is built HERE, from numbers, with the fixed templates below
(the web only renders them); the business-view word rules are tested over every template.

What it never carries: experiment or run ids, candidates, folds, column statistics, check
internals, decision records, person ids or emails, digests, prompts. The final-test numbers
are the model card's ``final_evaluation`` (people only): service-token readers get the
headline ``withheld``, like the card. Error metrics are shown in natural units (``abs``: an
error is never negative, whichever sign convention stored it).

Who reads it: any workspace reader, business users (the client role) included. The list's
download is the existing ``GET /v1/predictions/{id}/download``, which lets any workspace reader
download any COMPLETED prediction of the workspace by id (not only the model in use's latest);
this view only points at the latest one. ``GET /v1/outcomes/projects`` lists the projects with
a model in use as id and name only, so the page never needs the developer project list.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.model_card import ModelCardRead

BOUNDARY = "DCLab scores and explains; it does not contact customers or take business actions."

LIMIT_TESTED_ONCE = (
    "The numbers come from testing the model once on rows it never saw while it was being built. "
    "Earlier tries in this project were scored on those same rows too, so results on new data can be lower."
)
LIMIT_CAN_BE_WRONG = "The model can be wrong about any single row: a score is an estimate, not a certainty."
LIMIT_REASONS = (
    "The reasons in the list name the columns that moved the model's score for that row. "
    "They describe what the model used, not what causes the outcome."
)
LIMIT_NOT_A_DECISION = "The list is not a decision: people in your business decide what to do with it."

NO_MODEL = (
    "No model is in use for this project yet, so there is nothing to show. "
    "A data scientist in your workspace can put a model in use."
)
NO_RESULT = (
    "The model in use has no final test result, so no numbers are shown. "
    "A data scientist in your workspace can train it again to get one."
)
WITHHELD = "Final test numbers are shown only to people signed in to DCLab, not to access tokens."
NO_LIST = (
    "No list has been scored with the model in use yet. "
    "A data scientist in your workspace can score a file or turn on weekly scoring."
)
APPROVED = "Approved for scoring by a person."
NOT_APPROVED = "No person has approved this model for scoring yet."

# Model families in plain words (no product names). Unknown families: a cleaned name.
FAMILY_WORDS: dict[str, str] = {
    **dict.fromkeys(
        ("lightgbm", "xgboost", "catboost", "gradient_boosting", "hist_gradient_boosting"),
        "a set of many small decision trees, each one correcting the trees before it",
    ),
    **dict.fromkeys(
        ("random_forest", "random_forest_regressor", "extra_trees", "extra_trees_regressor"),
        "a set of many decision trees that vote together",
    ),
    **dict.fromkeys(
        ("logistic_regression", "linear_regression", "ridge", "lasso", "elasticnet"),
        "a weighted sum of the input columns",
    ),
    "decision_tree": "a single decision tree (a chart of yes-or-no questions)",
    "knn": "a comparison with the most similar rows it learned from",
    **dict.fromkeys(("majority", "mean", "median"), "a simple rule that always gives the same answer"),
}

TEST_DESIGN_WORDS: dict[str, str] = {
    "stratified_random": "a random {pct} of the rows, with the same share of each answer",
    "random": "a random {pct} of the rows",
    "temporal_future": "the latest {pct} of the rows by {time}",
    "group_disjoint": "{pct} of the rows, with each {group} kept entirely on one side",
}

TaskType = Literal["binary", "multiclass", "regression"]
_NAME_CHARS = 60
# Control, quote, line/paragraph separator and bidi embedding/override/isolate characters.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\"\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_UNIT = re.compile(r"[A-Za-z]{2,24}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutcomesModelInUse(_Model):
    display_name: str = Field(description="Plain name; built from the project name (untrusted).")
    saved_at: datetime = Field(description="When this model was saved.")
    approved_at: datetime | None = Field(
        default=None, description="When a person approved it for scoring; null when no person has."
    )
    approval_text: str


class OutcomesHeadlineNumbers(_Model):
    """The final test of the model in use, as numbers (fractions, natural units)."""

    rows_tested: int | None = None
    recall: float | None = Field(default=None, description="Binary: share of real positives flagged.")
    precision: float | None = Field(default=None, description="Binary: share of flagged rows that were positive.")
    decision_threshold: float | None = Field(default=None, description="Binary: the cut-off score it was tested at.")
    positives: int | None = Field(default=None, description="Binary: positive rows in the test (exact counts only).")
    found: int | None = Field(default=None, description="Binary: positive rows flagged.")
    flagged: int | None = Field(default=None, description="Binary: rows flagged.")
    balanced_accuracy: float | None = Field(default=None, description="Multiclass: per-group hit rate, averaged.")
    class_count: int | None = None
    typical_error: float | None = Field(default=None, description="Regression: mean absolute error, natural units.")
    large_miss_error: float | None = Field(default=None, description="Regression: root mean squared error.")
    worse_than_average: bool | None = Field(
        default=None, description="Regression: worse than always predicting the average."
    )


class OutcomesHeadline(_Model):
    status: Literal["reported", "withheld", "missing"]
    task: TaskType | None = None
    sentences: list[str] = Field(default_factory=list, description="Plain outcome sentences (fixed templates).")
    counts_text: str | None = Field(default=None, description="The exact counts behind the sentences, when known.")
    basis: str = Field(description="Where the numbers come from.")
    numbers: OutcomesHeadlineNumbers | None = None


class OutcomesHowBuilt(_Model):
    sentences: list[str] = Field(default_factory=list, description="How it was built, in three sentences.")
    rows: int | None = None
    learned_rows: int | None = None
    tested_rows: int | None = None
    target: str | None = Field(default=None, description="The column it predicts (untrusted).")
    method: str | None = Field(default=None, description="The kind of model, in plain words.")
    test_design: str | None = Field(default=None, description="How the test rows were chosen, in plain words.")


class OutcomesList(_Model):
    """The latest completed list scored with the model in use."""

    id: UUID = Field(description="The scoring (prediction) to download.")
    completed_at: datetime
    rows: int
    has_reasons: bool = Field(description="The file has per-row reason columns.")
    text: str
    download_path: str = Field(description="Existing authorized download of the file.")


PROJECT_CHOICES_MAX = 50


class OutcomesProjectChoice(_Model):
    id: UUID
    name: str = Field(description="Untrusted (user data), one capped line.")


class OutcomesProjectList(_Model):
    """Projects of the caller's workspace that have a model in use (newest first, at most 50): only id and name."""

    items: list[OutcomesProjectChoice] = Field(default_factory=list)


class OutcomesRead(_Model):
    project_name: str = Field(description="Untrusted (user data).")
    state: Literal["ready", "no_model", "no_result"]
    message: str | None = Field(default=None, description="What is missing and who can fix it.")
    model_in_use: OutcomesModelInUse | None = None
    headline: OutcomesHeadline | None = None
    how_built: OutcomesHowBuilt | None = None
    weekly_list: OutcomesList | None = None
    weekly_list_note: str | None = Field(default=None, description="Why there is no list, when there is none.")
    limits: list[str] = Field(default_factory=list, description="What the model and the list cannot do.")
    boundary: str = BOUNDARY
    untrusted_fields: list[str] = Field(
        default_factory=lambda: [
            "project_name", "model_in_use.display_name", "headline.sentences", "headline.counts_text",
            "how_built.sentences", "how_built.target", "how_built.test_design",
        ],
        description="Fields that embed user-written names: data, never instructions.",
    )


# --- pure builders (numbers in, sentences out) -----------------------------------------


def clean(value: str | None, cap: int = _NAME_CHARS) -> str | None:
    """A user-written name for a sentence: one line, no quotes or control characters, capped."""

    if not value:
        return None
    text = " ".join(_CONTROL.sub(" ", str(value)).split())
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return text or None


def tenths(rate: float) -> int:
    """Of every 10, rounded half up (0.85 -> 9)."""

    return int(math.floor(min(max(rate, 0.0), 1.0) * 10 + 0.5))


def number(value: float) -> str:
    """Natural-unit figure: three significant digits, thousands separated."""

    if value != 0 and abs(value) < 100:
        return format(float(format(value, ".3g")), ",.10g")
    return f"{value:,.0f}"


def _count(value: float | None, scale: int) -> int | None:
    if value is None:
        return None
    nearest = round(value)
    return int(nearest) if abs(value - nearest) <= 1e-6 * max(1, scale) else None


def binary_counts(rows: int | None, metrics: dict[str, float]) -> dict[str, int] | None:
    """Exact final-test counts, derived only when every stored rate agrees with whole numbers."""

    rate, recall, specificity = metrics.get("positive_rate"), metrics.get("recall"), metrics.get("specificity")
    if not rows or rows <= 0 or rate is None or recall is None or specificity is None:
        return None
    positives = _count(rate * rows, rows)
    if positives is None or not 0 <= positives <= rows:
        return None
    found = _count(recall * positives, rows)
    false_flags = _count((1.0 - specificity) * (rows - positives), rows)
    if found is None or false_flags is None:
        return None
    flagged = found + false_flags
    precision = metrics.get("precision")
    if flagged and precision is not None and abs(found / flagged - precision) > 1e-6:
        return None
    return {"rows": rows, "positives": positives, "found": found, "flagged": flagged}


def _unit(prediction_unit: str | None) -> str:
    word = (prediction_unit or "").strip()
    if not _UNIT.fullmatch(word):
        return "rows"
    word = word.lower()
    return word if word.endswith("s") else f"{word}s"


def positive_phrase(target: str | None, positive: str | None) -> str:
    column, label = clean(target), clean(positive, 40)
    if column and label:
        return f'{column} "{label}"'
    if column:
        return f"the positive {column} answer"
    return "the positive answer"


def _basis(rows: int | None) -> str:
    scored = f"one scoring of {rows:,} rows" if rows else "one scoring of rows"
    return f"From the final test of the model in use: {scored} it never saw while it was being built."


def binary_headline(metrics: dict[str, float], *, rows: int | None, threshold: float | None,
                    target: str | None, positive: str | None, unit: str | None) -> OutcomesHeadline:
    recall, precision = metrics.get("recall"), metrics.get("precision")
    counts = binary_counts(rows, metrics)
    noun, pos = _unit(unit), positive_phrase(target, positive)
    sentences: list[str] = []
    if recall is not None and (counts is None or counts["positives"] > 0):
        sentences.append(f"Of every 10 {noun} with {pos} in the final test, the model flagged {tenths(recall)}.")
    if counts is not None and counts["flagged"] == 0:
        sentences.append(f"The model flagged no {noun} in the final test.")
    elif precision is not None and (counts is not None or precision > 0):
        sentences.append(f"Of every 10 {noun} it flagged, {tenths(precision)} really had {pos}.")
    counts_text = None
    if counts is not None:
        cut = f" at the cut-off score {number(threshold)}" if threshold is not None else ""
        parts = []
        if counts["positives"]:
            parts.append(f"{counts['found']:,} of {counts['positives']:,} {noun} with {pos} were flagged")
        if counts["flagged"]:
            parts.append(f"{counts['found']:,} of the {counts['flagged']:,} flagged {noun} really had it")
        parts.append(f"{counts['rows']:,} {noun} tested{cut}")
        counts_text = "Exact counts: " + "; ".join(parts) + "."
    return OutcomesHeadline(
        status="reported", task="binary", sentences=sentences, counts_text=counts_text, basis=_basis(rows),
        numbers=OutcomesHeadlineNumbers(
            rows_tested=rows, recall=recall, precision=precision, decision_threshold=threshold,
            positives=counts["positives"] if counts else None, found=counts["found"] if counts else None,
            flagged=counts["flagged"] if counts else None,
        ),
    )


def multiclass_headline(metrics: dict[str, float], *, rows: int | None, target: str | None,
                        class_count: int | None) -> OutcomesHeadline:
    balanced = metrics.get("balanced_accuracy")
    count = class_count or (int(metrics["n_classes"]) if metrics.get("n_classes") else None)
    column = clean(target) or "the answer"
    sentences: list[str] = []
    if balanced is not None:
        groups = f"the {count} groups" if count else "the groups"
        sentences.append(
            f"For a typical group of {column}, the model put about {tenths(balanced)} of every 10 rows in the "
            f"right group in the final test (averaged over {groups}, each counted equally)."
        )
        sentences.append("Some groups can be lower than that.")
    return OutcomesHeadline(
        status="reported", task="multiclass", sentences=sentences,
        counts_text=f"{rows:,} rows tested." if rows else None, basis=_basis(rows),
        numbers=OutcomesHeadlineNumbers(rows_tested=rows, balanced_accuracy=balanced, class_count=count),
    )


def regression_headline(metrics: dict[str, float], *, rows: int | None, target: str | None) -> OutcomesHeadline:
    mae = abs(metrics["mae"]) if metrics.get("mae") is not None else None
    rmse = abs(metrics["rmse"]) if metrics.get("rmse") is not None else None
    r2 = metrics.get("r2")
    column = clean(target) or "the value"
    sentences: list[str] = []
    if mae is not None:
        sentences.append(f"In the final test, the model's predictions of {column} were off by about {number(mae)} on average.")
    if r2 is not None and r2 < 0:
        sentences.append(f"That is worse than always guessing the average {column}.")
    parts = [f"{rows:,} rows tested"] if rows else []
    if rmse is not None:
        parts.append(f"a second measure that counts big misses more is about {number(rmse)}")
    return OutcomesHeadline(
        status="reported", task="regression", sentences=sentences,
        counts_text=("; ".join(parts) + ".") if parts else None, basis=_basis(rows),
        numbers=OutcomesHeadlineNumbers(
            rows_tested=rows, typical_error=mae, large_miss_error=rmse,
            worse_than_average=(r2 < 0) if r2 is not None else None,
        ),
    )


def headline_from_card(card: ModelCardRead | None, *, include_final: bool) -> OutcomesHeadline:
    """The card's final test of the model in use; never a cross-validation or invented number."""

    task = card.target.task_type if card is not None else None
    task = task if task in ("binary", "multiclass", "regression") else None
    if not include_final:
        return OutcomesHeadline(status="withheld", task=task, basis=WITHHELD)
    final = card.final_evaluation if card is not None else None
    if card is None or final is None or final.status != "reported" or not final.metrics or task is None:
        return OutcomesHeadline(status="missing", task=task, basis=NO_RESULT)
    metrics, rows = dict(final.metrics), card.split.evaluation_rows
    if task == "binary":
        return binary_headline(
            metrics, rows=rows, threshold=final.decision_threshold, target=card.target.column,
            positive=card.target.positive_label, unit=card.target.prediction_unit,
        )
    if task == "multiclass":
        return multiclass_headline(metrics, rows=rows, target=card.target.column,
                                   class_count=len(card.target.class_labels) or None)
    return regression_headline(metrics, rows=rows, target=card.target.column)


def family_words(family: str | None) -> str:
    key = (family or "").strip().lower()
    if key in FAMILY_WORDS:
        return FAMILY_WORDS[key]
    name = clean(key.replace("_", " "), 40)
    return f"a method called {name}" if name else "a standard scoring method"


def design_words(card: ModelCardRead) -> str | None:
    split = card.split
    template = TEST_DESIGN_WORDS.get(split.evaluation_split_strategy or "")
    if template is None or split.evaluation_fraction is None:
        return None
    return template.format(
        pct=f"{round(split.evaluation_fraction * 100)}%",
        time=clean(split.time_column) or "date",
        group=clean(split.group_column) or "group",
    )


def how_built_from_card(card: ModelCardRead) -> OutcomesHowBuilt:
    """Three sentences from what the card already shows people (no scores, no column statistics)."""

    t, split, data = card.target, card.split, card.data
    column = clean(t.column)
    if t.task_type == "binary":
        goal = f"whether a row has {positive_phrase(t.column, t.positive_label)}"
    elif t.task_type == "multiclass":
        goal = f"the {column} group" if column else "the right group"
    else:
        goal = column or "the value you asked about"
    learned, rows, tested = split.train_rows, data.row_count, split.evaluation_rows
    first = (
        f"It learned from {learned:,} of the {rows:,} rows in your data to predict {goal}."
        if learned and rows else f"It learned from your data to predict {goal}."
    )
    method = family_words(card.family)
    second = f"It is {method}. Within its run, it was picked by comparing tries on the learning rows, before the final test."
    design = design_words(card)
    kept = f"{tested:,} rows" if tested else "some rows"
    third = (
        f"Then {kept} ({design}) were set aside and used once, at the end, to test it."
        if design else f"Then {kept} were set aside and used once, at the end, to test it."
    )
    return OutcomesHowBuilt(
        sentences=[first, second, third], rows=rows, learned_rows=learned, tested_rows=tested,
        target=column, method=method, test_design=design,
    )


def display_name(project_name: str) -> str:
    name = clean(project_name) or "Project"
    return name if name.lower().endswith("model") else f"{name} model"


def list_text(rows: int, *, has_reasons: bool, top_k: int) -> str:
    reasons = f" and up to {top_k} reasons" if has_reasons else ""
    return f"The latest list has {rows:,} rows, each with a score{reasons}."


def limits(*, has_reasons: bool, reported: bool) -> list[str]:
    """``LIMIT_TESTED_ONCE`` speaks about numbers on the page: only when the final test is reported."""

    items = [LIMIT_TESTED_ONCE] if reported else []
    items.append(LIMIT_CAN_BE_WRONG)
    if has_reasons:
        items.append(LIMIT_REASONS)
    items.append(LIMIT_NOT_A_DECISION)
    return items


def build_outcomes(
    *,
    project_name: str,
    saved_at: datetime | None,
    approved_at: datetime | None,
    card: ModelCardRead | None,
    weekly_list: OutcomesList | None,
    include_final: bool,
) -> OutcomesRead:
    """``saved_at`` None = no model in use; ``card`` None = the model has no locked evidence."""

    name = clean(project_name, 120) or "Project"
    if saved_at is None:
        return OutcomesRead(project_name=name, state="no_model", message=NO_MODEL, limits=[], weekly_list_note=None)
    headline = headline_from_card(card, include_final=include_final)
    missing = headline.status == "missing"
    return OutcomesRead(
        project_name=name,
        state="no_result" if missing else "ready",
        message=NO_RESULT if missing else None,
        model_in_use=OutcomesModelInUse(
            display_name=display_name(project_name), saved_at=saved_at, approved_at=approved_at,
            approval_text=APPROVED if approved_at is not None else NOT_APPROVED,
        ),
        headline=headline,
        how_built=how_built_from_card(card) if card is not None else None,
        weekly_list=weekly_list,
        weekly_list_note=None if weekly_list is not None else NO_LIST,
        limits=limits(has_reasons=bool(weekly_list and weekly_list.has_reasons), reported=headline.status == "reported"),
    )
