"""Implementation of the offline AI test harness (P6.10-B); the public API is re-exported
and documented in ``ai_harness/__init__.py``."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.gateway.cache import canonical_json
from app.agents.gateway.limits import BREAKER_FAILURES, GatewayLimits
from app.agents.gateway.providers import ProviderCall, ProviderError
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.harness.recorder import load_events
from app.agents.prompt_releases import output_schema_digest
from app.db.models import AgentProposal, AgentRun, LlmInvocation
from app.domain.agent_records import AGENT_RUN_TERMINAL_STATUSES

TESTS = Path(__file__).resolve().parents[1]
APP_ROOT = TESTS.parent / "app"
FIXTURES_ROOT = TESTS / "fixtures" / "ai"
GOLDENS_ROOT = TESTS / "golden" / "ai"
RECORD = os.environ.get("DCLAB_RECORD_AI") == "1"
UPDATE_GOLDENS = os.environ.get("DCLAB_UPDATE_GOLDEN") == "1"
FIXTURE_SCHEMA_VERSION = 1
if os.environ.get("CI") and (RECORD or UPDATE_GOLDENS):  # rewriting the expectations would pass trivially
    raise RuntimeError("DCLAB_RECORD_AI / DCLAB_UPDATE_GOLDEN are refused in CI")
__all__ = ["CHAOS_KINDS", "Chaos", "FixtureMissing", "FixtureRejected", "Ids", "Scenario", "answers", "assert_golden",
           "call_key", "check_codeact_ban", "check_harness_records", "check_ledger", "check_no_raw_rows",
           "codeact_findings", "fixture_cases", "harness_findings", "ledger_findings", "ledger_summary",
           "load_fixture", "raw_row_findings", "registry_findings", "run_transcript"]

# --- normalisation: ids by first appearance, timestamps, digests, volatile numbers --------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")  # uuid4().hex in seeded slugs
_PLACEHOLDER = re.compile(r"<id:\d+>")
VOLATILE_KEYS = frozenset({"latency_ms", "duration_ms", "wall_ms"})


def _volatile(key: Any, value: Any) -> bool:
    return key in VOLATILE_KEYS or (isinstance(key, str) and key.endswith("_at") and isinstance(value, (int, float))
                                    and not isinstance(value, bool))


def _scrub(text: str, uuid: Any = "<id>") -> str:
    return _HEX.sub("<hex>", _DIGEST.sub("<digest>", _TS.sub("<ts>", _UUID.sub(uuid, text))))


def _mask(item: Any) -> str:
    return _scrub(item if isinstance(item, str) else canonical_json(item))


def _entries(value: dict) -> list[tuple[Any, Any]]:
    """Dict entries in an order that does not depend on random ids (canonical JSON sorts
    keys such as ``experiment:<uuid>`` by the uuid; services sort node lists by id)."""

    return sorted(value.items(), key=lambda kv: (_mask(kv[0]), _mask(kv[1])))


class Ids:
    """Per-run identifiers become ``<id:N>`` (first appearance in values, then in keys, in an
    id-independent order), so a fixture or golden pins the shape of a run, not its random
    ids; ``fill`` maps placeholders back."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def _id(self, match: re.Match) -> str:
        return self.ids.setdefault(match.group(0), f"<id:{len(self.ids) + 1}>")

    def _collect(self, value: Any, keys: bool) -> None:
        if isinstance(value, dict):
            for k, v in _entries(value):
                if keys and isinstance(k, str):
                    _UUID.sub(self._id, k)
                self._collect(v, keys)
        elif isinstance(value, (list, tuple)):
            for v in sorted(value, key=_mask):
                self._collect(v, keys)
        elif isinstance(value, str) and not keys:
            _UUID.sub(self._id, value)

    def _render(self, value: Any, unordered: bool) -> Any:
        if isinstance(value, dict):
            return {_scrub(k, self._id) if isinstance(k, str) else k:
                    "<volatile>" if _volatile(k, v) else self._render(v, unordered) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            items = [self._render(v, unordered) for v in value]
            return sorted(items, key=canonical_json) if unordered else items
        return _scrub(value, self._id) if isinstance(value, str) else value

    def norm(self, value: Any, *, unordered: bool = False) -> Any:
        """``unordered``: lists compare as multisets (a fixture key; goldens keep order)."""

        self._collect(value, keys=False)
        self._collect(value, keys=True)
        return self._render(value, unordered)

    def template(self, value: Any) -> Any:
        """Known ids only (an output's fabricated id stays as it is)."""

        text = canonical_json(value)
        return json.loads(_UUID.sub(lambda m: self.ids.get(m.group(0), m.group(0)), text))

    def fill(self, value: Any) -> Any:
        back = {placeholder: real for real, placeholder in self.ids.items()}
        return json.loads(_PLACEHOLDER.sub(lambda m: back.get(m.group(0), m.group(0)), canonical_json(value)))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


KEY_FIELDS = ("instructions_digest", "model", "output_schema_digest", "payload_digest")


def stamp(key: Mapping[str, str]) -> str:
    return _sha("\x1f".join(key[k] for k in KEY_FIELDS))


def call_key(call: ProviderCall, ids: Ids | None = None) -> dict[str, str]:
    """The fixture key of one provider call: the redacted payload the gateway sent (per-run
    ids normalised), the prompt release text it sent, the output schema digest and the model."""

    ids = ids or Ids()
    key = {"payload_digest": _sha(canonical_json(ids.norm(json.loads(call.input_json), unordered=True))),
           "instructions_digest": _sha(call.instructions),
           "output_schema_digest": output_schema_digest(call.output_schema), "model": call.model}
    return {**key, "input_digest": stamp(key)}


# --- recorded fixtures and scripted scenarios ---------------------------------------------------


def key_paths(value: Any, path: str = "$") -> list[str]:
    """The payload's key paths (no values; list positions folded, per-run ids normalised): a
    re-record that bakes in a new field shows up in the fixture diff."""

    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{_scrub(str(k))}"
            out.add(child)
            out.update(key_paths(v, child))
    elif isinstance(value, list):
        for v in value:
            out.update(key_paths(v, f"{path}[]"))
    return sorted(out)


class FixtureRejected(RuntimeError):
    """A recorded exchange exists for this payload but under another prompt release, output
    schema or model: never reused."""


class FixtureMissing(RuntimeError):
    pass


def fixture_path(agent: str, case: str) -> Path:
    return FIXTURES_ROOT / agent / f"{case}.json"


def fixture_cases() -> list[tuple[str, str]]:
    return sorted((path.parent.name, path.stem) for path in FIXTURES_ROOT.glob("*/*.json"))


def load_fixture(agent: str, case: str) -> dict[str, Any]:
    data = json.loads(fixture_path(agent, case).read_text(encoding="utf-8"))
    assert (data.get("schema_version"), data.get("agent"), data.get("case")) == (FIXTURE_SCHEMA_VERSION, agent, case)
    return data


class Scenario:
    """A ``FakeProvider`` handler for one ``<agent>/<case>``: replays the recorded fixture
    (default) or, with ``DCLAB_RECORD_AI=1`` (or ``record=True``), answers with ``script``
    (a handler: ``ProviderCall -> dict``) and writes the fixture on ``save()``."""

    def __init__(self, agent: str, case: str, script: Callable[[ProviderCall], Any] | None = None, *,
                 record: bool | None = None) -> None:
        self.agent, self.case, self.script = agent, case, script
        self.record = RECORD if record is None else record
        self.exchanges: list[dict[str, Any]] = [] if self.record else load_fixture(agent, case)["exchanges"]
        self.used: set[int] = set()
        self.misses: list[dict[str, Any]] = []
        self.rejections: list[str] = []
        self.calls: list[ProviderCall] = []

    def __call__(self, call: ProviderCall) -> Any:
        self.calls.append(call)
        ids = Ids()
        key = call_key(call, ids)
        if self.record:
            assert self.script is not None, "recording needs a script"
            answer = self.script(call)
            if not isinstance(answer, BaseException):
                self.exchanges.append({**key, "purpose": call.purpose, "output": ids.template(answer),
                                       "payload_keys": key_paths(json.loads(call.input_json))})
            return answer
        for index, exchange in enumerate(self.exchanges):
            if exchange["input_digest"] == key["input_digest"]:
                self.used.add(index)
                return ids.fill(exchange["output"])
        for exchange in self.exchanges:
            if exchange["payload_digest"] == key["payload_digest"]:
                changed = next(k for k in ("instructions_digest", "output_schema_digest", "model")
                               if exchange[k] != key[k])
                reason = {"instructions_digest": "prompt_release_changed",
                          "output_schema_digest": "output_schema_changed", "model": "model_changed"}[changed]
                self.rejections.append(reason)
                raise FixtureRejected(reason)
        self.misses.append({"purpose": call.purpose, "payload": ids.norm(json.loads(call.input_json))})
        raise FixtureMissing(f"{self.agent}/{self.case}: no recorded exchange for this input")

    def provider(self, **kwargs: Any) -> FakeProvider:
        return FakeProvider(handler=self, environment="test", **kwargs)

    def finish(self) -> None:
        """Recording: write the fixture. Replay: every call matched and every exchange was used."""

        if self.record:
            path = fixture_path(self.agent, self.case)
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {"schema_version": FIXTURE_SCHEMA_VERSION, "agent": self.agent, "case": self.case,
                    "exchanges": self.exchanges}
            path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return
        assert not self.misses, json.dumps(self.misses, indent=1, default=str)[:4000]
        assert not self.rejections, self.rejections
        assert self.used == set(range(len(self.exchanges))), f"unused exchanges in {self.agent}/{self.case}"


def answers(*outputs: Any) -> Callable[[ProviderCall], Any]:
    """A script answering each call with the next output (a dict or an exception)."""

    queue = list(outputs)
    return lambda _call: queue.pop(0)


# --- chaos --------------------------------------------------------------------------------------

CHAOS_KINDS = ("provider_timeout", "breaker_open", "over_budget", "invalid_output", "kill_switch_mid_run")


class Chaos:
    """Fault injection for one run. ``handler`` wraps a valid script (timeout, invalid output);
    ``trip`` opens the provider breaker for a workspace; ``wrap`` runs ``flip`` right before
    the gateway call number ``flip_at`` (a kill switch flipped mid-run). Over budget is the
    caller's narrow hold (agent runs) or an exhausted workspace budget (``exhaust_budget``)."""

    def __init__(self, kind: str, valid: Callable[[ProviderCall], Any], *, flip: Callable[[], None] | None = None,
                 flip_at: int = 1, invalid: Mapping[str, Any] | None = None) -> None:
        assert kind in CHAOS_KINDS, kind
        self.kind, self.valid, self.flip, self.flip_at = kind, valid, flip, flip_at
        self.invalid = dict(invalid or {"unexpected": "not the output schema"})
        self.gateway_calls = 0

    def handler(self, call: ProviderCall) -> Any:
        if self.kind == "provider_timeout":
            return ProviderError("timeout", "chaos")
        if self.kind == "invalid_output":
            return dict(self.invalid)
        return self.valid(call)

    def trip(self, limits: GatewayLimits, *, provider: str, workspace_id: Any) -> None:
        for _ in range(BREAKER_FAILURES):
            limits.record(provider=provider, workspace_id=workspace_id, purpose="chaos", outcome="failure")

    def wrap(self, gateway: Any) -> Any:
        for name in ("complete", "decide"):
            original = getattr(gateway, name)

            def wrapped(db: Session, request: Any, *, _original: Any = original, **kwargs: Any) -> Any:
                self.gateway_calls += 1
                if self.kind == "kill_switch_mid_run" and self.gateway_calls == self.flip_at and self.flip:
                    self.flip()
                return _original(db, request, **kwargs)

            setattr(gateway, name, wrapped)
        return gateway

    @staticmethod
    def exhaust_budget(gateway: Any, db: Session, workspace_id: Any) -> None:
        """The real budget path: the workspace counter's limit drops below any reservation."""

        from sqlalchemy import update

        from app.db.models import WorkspaceLlmBudget

        held = gateway.reserve(db, workspace_id=workspace_id, estimate_micros=1)
        assert getattr(held, "held_micros", None) == 1, held
        db.execute(update(WorkspaceLlmBudget).where(WorkspaceLlmBudget.workspace_id == workspace_id,
                                                    WorkspaceLlmBudget.scope == "workspace").values(limit_micros=1))
        db.commit()


# --- property checks (each raises AssertionError with its findings) -----------------------------

ROW_CARRIER_KEYS = frozenset({"rows", "raw_rows", "sample_rows", "records", "head", "tail", "sample_values",
                              "examples", "data_rows", "top_values", "values", "distinct_values"})
_SECRETS = (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
            re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), re.compile(r"dclab_st_[A-Za-z0-9_]{16,}"),
            re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret)\b\s*[:=]\s*\S{6,}"))
_SECRET_NAME = re.compile(r"(?i)(api_key|secret|password|passwd|token|access_key|database_url|dsn)")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_SPACELESS_ROW = re.compile(r"^[^\s,]*(?:,[^\s,]*)+$")


def numbers_in(text: str) -> set[float]:
    """Every number written in ``text``, as floats (``0.81`` never matches ``0.8123``)."""

    return {float(item) for item in _NUMBER.findall(text)}


def _secret_values(extra: Iterable[str] = ()) -> set[str]:
    """Secret-typed values from the environment and the process settings (``.env`` included)."""

    from app.config import get_settings

    values = {v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= 12}
    values |= {str(v) for k, v in get_settings().model_dump().items()
               if _SECRET_NAME.search(k) and isinstance(v, str) and len(v) >= 12}
    return values | {v for v in extra if v}


def _scalars(value: Any) -> bool:
    return isinstance(value, list) and all(not isinstance(x, (dict, list)) for x in value)


def _csv_rows(lines: list[str], names: set[str]) -> bool:
    lines = [line for line in lines if line.strip()]
    counts = {line.count(",") or line.count("\t") for line in lines}
    return len(lines) >= 2 and len(counts) == 1 and 0 not in counts and (
        len(names & {t.strip() for t in lines[0].replace("\t", ",").split(",")}) >= 2
        or all(_SPACELESS_ROW.match(line.replace("\t", ",")) for line in lines))


HOLDOUT_TOLERANCE = 0.0005
_WRITTEN = re.compile(r"(?<![\w.])(-?\d+(?:\.(\d+))?)(\s*%)?(?![\w])")
_PATH_LIKE = re.compile(r"^[A-Za-z_][\w-]*(?:[.\[\]/:][\w\]-]*)+$")
_SEGMENTS = re.compile(r"[.\[\]/:]")
PATH_VALUE_KEYS = frozenset({"key", "path", "check", "check_id", "field"})  # their values name fields


def _holdout_names() -> tuple[re.Pattern, frozenset[str]]:
    """Holdout OUTCOME names. Holdout size and fraction (``n_test``, ``evaluation_rows``,
    ``test_rows``, ``evaluation_fraction``) are allowed SplitPlan design metadata."""

    from app.agents.gateway.redaction import HOLDOUT_KEY
    from app.agents.tools.shaping import HOLDOUT_SCOPED_REPORT_KEYS
    from app.services import pipeline_verifier

    outcomes = {"holdout_metrics", "test_metrics", "test_predictions", "test_source_rows", "label_counts_holdout"}
    names = HOLDOUT_SCOPED_REPORT_KEYS | (pipeline_verifier._HOLDOUT_KEYS & outcomes) | {
        pipeline_verifier.HOLDOUT_CONSTRAINTS_CHECK}
    return HOLDOUT_KEY, frozenset(names)


def holdout_canaries(db: Session, workspace_id: Any) -> dict[str, tuple]:
    """The workspace's final-holdout OUTCOMES, read from the database: metric values with
    their 2-decimal and percent renderings (a rendering equal to the same rendering of any
    other metric of the workspace is dropped: it cannot tell a leak apart) and the label /
    class counts of the final-holdout summaries. Holdout sizes are allowed and not canaries."""

    from app.db.models import EvaluationMetric, ModelEvaluation

    rows = db.execute(select(EvaluationMetric.metric_value, ModelEvaluation.summary,
                             ModelEvaluation.evaluation_scope).join(
        ModelEvaluation, ModelEvaluation.id == EvaluationMetric.model_evaluation_id).where(
        ModelEvaluation.workspace_id == workspace_id)).all()
    other = [float(v) for v, _, scope in rows if scope != "final_holdout"]
    holdout = [(float(v), summary) for v, summary, scope in rows if scope == "final_holdout"]

    def forms(value: float) -> set[tuple[str, float]]:
        return {("exact", round(value, 6)), ("dp2", round(value, 2)), ("pct", float(round(value * 100))),
                ("pct", round(value * 100, 1))}

    taken = set().union(*(forms(v) for v in other)) if other else set()
    numbers = {round(v, 6) for v, _ in holdout if round(v, 1) != round(v, 6)
               and all(abs(v - c) > 2 * HOLDOUT_TOLERANCE for c in other)}
    renderings = {f for v in numbers for f in forms(v) if f[0] != "exact" and f not in taken}
    counts = {x for _, summary in holdout for x in _class_counts(summary or {})}
    return {"numbers": tuple(sorted(numbers)), "renderings": tuple(sorted(renderings)), "counts": tuple(sorted(counts))}


def _class_counts(value: Any, inside: bool = False) -> list[int]:
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in _class_counts(v, inside or bool(re.search(
            r"label|class", str(k), re.I)))]
    if isinstance(value, list):
        return [x for v in value for x in _class_counts(v, inside)]
    return [value] if inside and isinstance(value, int) and not isinstance(value, bool) else []


def written_numbers(text: str) -> list[tuple[float, int, bool]]:
    """``(value, decimals, percent)`` for every number written in ``text``."""

    return [(float(m.group(1)), len(m.group(2) or ""), bool(m.group(3))) for m in _WRITTEN.finditer(text)]


def raw_row_findings(call: ProviderCall, *, columns: Iterable[str] = (), numbers: Iterable[float] = (),
                     renderings: Iterable[tuple[str, float]] = (), counts: Iterable[int] = (),
                     cells: Iterable[str] = (), secrets: Iterable[str] = ()) -> list[str]:
    """Raw rows, holdout outcomes, planted canaries or secrets in one provider request body.

    Holdout rule (orchestrator decision, P6.10-B): holdout SIZE and FRACTION are allowed design
    metadata (the SplitPlan); holdout OUTCOMES are forbidden — metric values, verifier
    verdicts, report keys, label counts / class distributions / positive rate, predictions.

    * holdout names (in ``input_json`` only; instructions may say "holdout"): every dict key and
      every path-like string value (``{"key": "get_evidence.openai_audit"}``), split into
      segments on ``. [ ] / :``, checked against ``redaction.HOLDOUT_KEY``, the holdout-scoped
      report keys and the holdout outcome / check names; free-text data values (user text,
      column names) and ``untrusted_text`` are not names and are not scanned;
    * holdout values within ``HOLDOUT_TOLERANCE``; their 2-decimal and percent renderings
      (percent only for numbers written with ``%`` or a decimal part); exact label counts;
    * row shapes: a row-carrier key; one dict or a list of >= 2 dicts keyed by >= 2 dataset
      column names; a dict of >= 2 column names to lists of >= 3 scalars; a matrix; a vector of
      >= 8 integers (labels); rows as delimited strings or CSV text; raw cell canaries;
    * secrets: key patterns and secret-typed environment / settings values."""

    body = f"{call.instructions}\n{call.input_json}"
    written = written_numbers(call.input_json)
    rendered = set(renderings)
    found = [f"holdout_value:{v}" for v in numbers if any(
        decimals and abs(x - v) <= HOLDOUT_TOLERANCE for x, decimals, _pct in written)]
    found += [f"holdout_rendering:{kind}:{v}" for kind, v in sorted(rendered) if any(
        (kind == "dp2" and decimals == 2 and not pct and x == v)
        or (kind == "pct" and (pct or decimals) and abs(x - v) < 0.05 and (pct or 1 < x <= 100)) for x, decimals, pct
        in written)]
    found += [f"holdout_count:{n}" for n in counts if any(x == n and not decimals for x, decimals, _ in written)]
    found += [f"cell:{c}" for c in cells if c and c in body]
    found += [f"secret:{p.pattern[:20]}" for p in _SECRETS if p.search(body)]
    found += ["secret:value" for v in _secret_values(secrets) if v in body]
    names = set(columns)
    holdout_key, holdout_names = _holdout_names()

    def holdout(text: str, path: str) -> None:
        if any(holdout_key.search(seg) or seg in holdout_names for seg in _SEGMENTS.split(text) if seg):
            found.append(f"holdout_name:{path}")

    def walk(value: Any, path: str, parent: str | None = None) -> None:
        if isinstance(value, dict):
            keyed = [k for k in value if k in names]
            if len(keyed) >= 2 and all(not isinstance(value[k], (dict, list)) for k in keyed):
                found.append(f"row_dict:{path}")
            if sum(_scalars(value[k]) and len(value[k]) >= 3 for k in keyed) >= 2:
                found.append(f"column_table:{path}")
            for k, v in value.items():
                if k == "untrusted_text":
                    continue  # data, not a field name
                if k not in names:
                    holdout(str(k), f"{path}.{k}")
                if k in ROW_CARRIER_KEYS and v not in (None, [], {}, 0, ""):
                    found.append(f"row_carrier:{path}.{k}")
                walk(v, f"{path}.{k}", str(k))
        elif isinstance(value, list):
            dicts = [v for v in value if isinstance(v, dict)]
            if len(dicts) >= 2 and sum(len(names & set(d)) >= 2 for d in dicts) >= 2:
                found.append(f"row_table:{path}")
            if len(value) >= 2 and all(_scalars(v) and v for v in value):
                found.append(f"matrix:{path}")
            if len(value) >= 8 and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
                found.append(f"value_vector:{path}")
            strings = [v for v in value if isinstance(v, str)]
            if len(strings) >= 2 and _csv_rows(strings, names):
                found.append(f"row_strings:{path}")
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]", parent)
        elif isinstance(value, str):
            if parent in PATH_VALUE_KEYS or (_PATH_LIKE.match(value) and value not in names):
                holdout(value, path)
            if "\n" in value and _csv_rows(value.splitlines(), names):
                found.append(f"csv_text:{path}")

    walk(json.loads(call.input_json), "$")
    return found


def check_no_raw_rows(calls: Iterable[ProviderCall], **kwargs: Any) -> None:
    findings = {i: f for i, call in enumerate(calls) if (f := raw_row_findings(call, **kwargs))}
    _require(findings, "raw rows / secrets left the gateway")


def _require(findings: Any, what: str) -> None:
    assert not findings, f"{what}: {findings}"


LEDGER_FINAL = ("completed", "failed", "rejected")


def ledger_findings(db: Session, calls: Iterable[ProviderCall]) -> list[str]:
    """Every provider call is one finalized ``llm_invocations`` row (joined by the row id and
    cache key the gateway puts on the call; a specialist's retry shares its call's row) with
    provider, model, prompt release, digest, usage and outcome; and every completed model
    row had a provider call."""

    db.expire_all()
    calls = list(calls)
    rows = {r.id: r for r in db.scalars(select(LlmInvocation).where(
        LlmInvocation.llm_used.is_(True), LlmInvocation.cache_hit.is_(False)))}
    found = []
    for i, call in enumerate(calls):
        row = rows.get(call.invocation_id)
        if row is None:
            found.append(f"call {i} ({call.purpose}): no ledger row")
            continue
        missing = [name for name in ("provider", "model", "prompt_release_id", "validator_verdict", "completed_at",
                                     "latency_ms") if getattr(row, name) is None]
        if row.status not in LEDGER_FINAL:
            missing.append(f"status={row.status}")
        if row.prompt_version in (None, "", "unreleased"):
            missing.append("prompt_version")
        if row.status == "completed" and (row.input_tokens is None or row.output_tokens is None):
            missing.append("usage")
        if (row.model, (row.input_evidence_digest or "").strip()) != (call.model, call.input_digest):
            missing.append("model_or_digest_mismatch")
        found += [f"call {i} ({call.purpose}): {name}" for name in missing]
    sent = {call.invocation_id for call in calls}
    found += [f"row {key} ({r.purpose}): completed without a provider call" for key, r in rows.items()
              if r.status == "completed" and key not in sent]
    return found


def check_ledger(db: Session, calls: Iterable[ProviderCall]) -> None:
    _require(ledger_findings(db, calls), "model calls without a complete ledger row")


def harness_findings(db: Session, results: Iterable[Any], calls: Iterable[ProviderCall] | None = None) -> list[str]:
    """Every agent run (completed, refused by a gateway, failed, over budget) has its
    ``agent_runs`` row, terminal and released, and ``agent_events`` that end with the run's
    outcome and list every ledger row of the run. With ``calls`` (the provider's), every model
    row of the run (``llm_used``, not a cache hit) and every answered call event is one of
    those calls. A result without a run id must be an authorization refusal that called nothing."""

    db.expire_all()
    sent = None if calls is None else {str(c.invocation_id) for c in calls}
    found = []
    for result in results:
        if result.run_id is None:
            if result.status != "refused" or result.usage:
                found.append(f"unrecorded run: {result.status}/{result.error_code}")
            continue
        run = db.get(AgentRun, result.run_id)
        if run is None:
            found.append(f"{result.run_id}: no agent_runs row")
            continue
        events = load_events(db, workspace_id=run.workspace_id, run_id=run.id)
        types = [e.type for e in events]
        last = events[-1] if events else None
        if run.status not in AGENT_RUN_TERMINAL_STATUSES or run.status != result.status:
            found.append(f"{run.id}: status {run.status} (result {result.status})")
        if run.budget_released_at is None:
            found.append(f"{run.id}: budget never released")
        if last is None or last.type not in ("run_finished", "run_failed") or last.payload.get("status") != run.status:
            found.append(f"{run.id}: events do not end with the run outcome: {types[-3:]}")
        elif (last.type == "run_finished") != (run.status == "completed"):
            found.append(f"{run.id}: {last.type} for status {run.status}")
        if types.count("llm_call_started") != types.count("llm_call_finished"):
            found.append(f"{run.id}: unfinished model call events")
        recorded = {e.payload.get("invocation_id") for e in events if e.type == "llm_call_finished"} - {None}
        ledger = {str(i) for i in db.scalars(select(LlmInvocation.id).where(
            LlmInvocation.workspace_id == run.workspace_id, LlmInvocation.agent_run_id == run.id))}
        if recorded != ledger:
            found.append(f"{run.id}: ledger rows {sorted(ledger - recorded)} missing from the events")
        if sent is not None:
            model_rows = {str(i) for i in db.scalars(select(LlmInvocation.id).where(
                LlmInvocation.workspace_id == run.workspace_id, LlmInvocation.agent_run_id == run.id,
                LlmInvocation.llm_used.is_(True), LlmInvocation.cache_hit.is_(False)))}
            answered = {e.payload.get("invocation_id") for e in events if e.type == "llm_call_finished"
                        and e.payload.get("ok") and not e.payload.get("cache_hit")}
            if not (model_rows | answered) <= sent:
                found.append(f"{run.id}: model rows / answers without a provider call: "
                             f"{sorted((model_rows | answered) - sent)}")
    return found


def check_harness_records(db: Session, results: Iterable[Any], calls: Iterable[ProviderCall] | None = None) -> None:
    _require(harness_findings(db, results, calls), "agent runs without a complete harness record")


HOLDOUT_WORDS = re.compile(r"holdout|hold_out|final_test|test_metric|winner|select|promot|champion|"
                           r"compute_metric|build_split|read_rows|raw_sql|execute|exec_|eval_code|code_exec",
                           re.IGNORECASE)


def _schema_names(schema: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            names |= _schema_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= _schema_names(value)
    return names


def registry_findings(definitions: Iterable[Any], export: Mapping[str, Any], contract: Mapping[str, Any],
                      mcp_tools: Mapping[str, Any] | None) -> list[str]:
    """Static half: no tool on any surface (assistant, MCP, Studio forms) reads holdout data,
    selects, promotes, computes metrics, builds splits, reads rows or executes code — by name,
    operation, service, input schema or declared result; every read tool has a fetch and a
    holdout-free shaper; writes are <= L1 proposals; the in-code catalog, its export, the
    committed contract and the MCP listing agree. The shaped results themselves are checked by
    ``test_ai_properties`` (every read tool on the seeded graph), ``test_agent_tool_catalog``
    (consumer mode on a trained project, lines ~431-472) and ``test_dclab_mcp_server`` (~322-338)."""

    from app.agents.governance.decision_points import REGISTRY
    from app.agents.tools.catalog import FORBIDDEN_OPERATIONS

    found = []
    defs = list(definitions)
    for d in defs:
        texts = [d.name, *d.operations, *(s.rsplit(".", 1)[-1] for s in d.services)]
        texts += [n for n in _schema_names(d.input_schema.model_json_schema()) if n != "idempotency_key"]
        found += [f"{d.name}: forbidden term in {t!r}" for t in texts if HOLDOUT_WORDS.search(t)
                  or any(op in t for op in FORBIDDEN_OPERATIONS)]
        if d.effect not in ("read", "proposal") or d.outcome_scope not in ("none", "cv") \
                or d.data_class not in ("metadata", "aggregates"):
            found.append(f"{d.name}: effect/result {d.effect}/{d.data_class}/{d.outcome_scope}")
        if d.effect == "read" and (not all(op.startswith("GET ") for op in d.operations)
                                   or d.fetch is None or d.shaper is None):
            found.append(f"{d.name}: a read tool with a non-GET operation or without fetch + shaper")
        if d.effect == "proposal":
            point = REGISTRY.get(d.decision_point_key or "")
            if point is None or point.cap > 1 or d.fetch is not None:
                found.append(f"{d.name}: a write tool that is not an <= L1 proposal")
    names = {d.name for d in defs}
    for label, payload in (("export", export), ("contract", contract)):
        listed = {t["name"]: t for t in payload.get("tools", [])}
        if set(listed) != names:
            found.append(f"{label}: tools differ from the catalog: {sorted(set(listed) ^ names)}")
        for name, tool in listed.items():
            texts = [name, *tool.get("operations", []), *(s.rsplit(".", 1)[-1] for s in tool.get("services", []))]
            texts += [n for n in _schema_names(tool.get("input_schema", {})) if n != "idempotency_key"]
            if any(HOLDOUT_WORDS.search(t) for t in texts) or tool.get("result", {}).get("outcome_scope") not in (
                    "none", "cv"):
                found.append(f"{label}: {name} is not holdout-blind")
    if mcp_tools is not None:
        mcp_names = {d.name for d in defs if "mcp" in d.surfaces}
        if set(mcp_tools) != mcp_names:
            found.append(f"mcp: listing differs from the catalog: {sorted(set(mcp_tools) ^ mcp_names)}")
        for name, schema in mcp_tools.items():
            bad = [n for n in _schema_names(schema) if n != "idempotency_key" and HOLDOUT_WORDS.search(n)]
            if HOLDOUT_WORDS.search(name) or bad:
                found.append(f"mcp: {name} is not holdout-blind {bad}")
    return found


# CodeAct ban (ADR 0008 §6, ADR 0009 §1 b): no generated code runs in the API / worker.
EXEC_CALLS = re.compile(
    r"^(?:(?:builtins\.)?(?:exec|eval|compile|execfile)|os\.(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*)"
    r"|pty\.spawn|runpy\.\w+|code\.(?:interact|InteractiveConsole|InteractiveInterpreter)|(?:.+\.)?exec_module"
    r"|subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)|(?:.+\.)?create_subprocess_\w+"
    r"|multiprocessing\.\w+|pickle\.loads?|marshal\.loads?)$")
EXEC_ATTRS = frozenset({"exec_module", "system", "popen", "create_subprocess_exec", "create_subprocess_shell"})
EXEC_MODULES = ("runpy", "code", "pty", "subprocess", "multiprocessing")
DYNAMIC_FORBIDDEN = EXEC_MODULES + ("os", "posix", "nt", "builtins", "pickle", "marshal")
DYNAMIC_IMPORTS = ("__import__", "importlib.import_module")
GETATTR_TARGETS = ("builtins", "os", "subprocess", "importlib", "runpy", "pty", "code", "asyncio", "sys")
REVIEWED_REFERENCES = frozenset({"platform.system"})  # an OS name, not a shell
# Reviewed exception: the git revision probe, exactly this argv, no shell / input / stdin.
EXEC_ALLOWED_FILE = "services/lab_service.py"
EXEC_ALLOWED_ARGV = ("git", "rev-parse", "HEAD")
SANDBOX_FLAG = re.compile(r"sandbox|codeact|code_?exec|exec(?:ute)?_?code|generated_code|python_exec", re.I)
REVIEWED_RUNTIMES = frozenset({"fake", "nooa_predict", "lead_loop"})


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _const(call: ast.Call) -> str | None:
    first = call.args[0] if call.args else None
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> the module or ``module.attr`` it is bound to by an import."""

    names = {"__builtins__": "builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _resolve(node: ast.AST, aliases: Mapping[str, str]) -> str:
    head, _, rest = _dotted(node).partition(".")
    full = aliases.get(head, head)
    return f"{full}.{rest}" if rest and full else full


def _allowed_probe(call: ast.Call) -> bool:
    argv = call.args[0] if call.args else None
    return (isinstance(argv, (ast.List, ast.Tuple)) and len(call.args) == 1
            and tuple(e.value if isinstance(e, ast.Constant) else None for e in argv.elts) == EXEC_ALLOWED_ARGV
            and not any(k.arg in (None, "shell", "input", "stdin", "executable", "env") for k in call.keywords))


def _call_hit(node: ast.Call, aliases: Mapping[str, str]) -> str | None:
    func = node.func
    if isinstance(func, ast.Call) and _resolve(func.func, aliases) == "getattr" and func.args:
        target = _resolve(func.args[0], aliases)  # getattr(__builtins__, 'eval')(src)
        if target.split(".")[0] in GETATTR_TARGETS:
            attr = func.args[1].value if len(func.args) > 1 and isinstance(func.args[1], ast.Constant) else "<dynamic>"
            return f"getattr({target}, {attr})"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call) \
            and _resolve(func.value.func, aliases) in DYNAMIC_IMPORTS:  # __import__('os').system(cmd)
        return f"{_const(func.value) or '<dynamic>'}.{func.attr}"
    name = _resolve(func, aliases)
    if name in DYNAMIC_IMPORTS:
        module = _const(node) or ""
        return None if module and module.split(".")[0] not in DYNAMIC_FORBIDDEN else f"{name}({module or '<dynamic>'})"
    return None


def _ref_hit(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    """A reference to an exec target, called or not (``run = eval``, ``f = os.system``)."""

    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        name = _resolve(node, aliases)
        return name if EXEC_CALLS.match(name) and (node.id != name or node.id in ("exec", "eval", "compile")) else None
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        name = _resolve(node, aliases)
        if name in REVIEWED_REFERENCES:
            return None
        if (name and EXEC_CALLS.match(name)) or node.attr in EXEC_ATTRS:
            return name or f"*.{node.attr}"
    if isinstance(node, ast.Subscript):  # sys.modules['os'], vars(x)['exec'], obj.__dict__['system']
        base = _resolve(node.value, aliases)
        if base == "sys.modules" or base.endswith(".__dict__") or base == "__dict__":
            return f"{base}[...]"
    return None


def codeact_findings(root: Path = APP_ROOT, *, settings_fields: Mapping[str, Any] | None = None,
                     runtimes: Mapping[str, Any] | None = None, classes: Mapping[str, Any] | None = None) -> list[str]:
    """Exec-like calls and references resolved through import aliases (``from os import
    system``, ``import os as o``, ``run = eval``, ``builtins.eval``, ``getattr(__builtins__,
    'eval')``, ``__import__('os').system``, ``sys.modules[...]`` / ``__dict__[...]``, dynamic
    imports of exec / os modules, any ``.exec_module`` / ``.system`` / ``.popen`` /
    ``create_subprocess_*``, ``pickle.loads``) and imports of exec modules. Reviewed: the git
    probe in ``services/lab_service.py`` (exactly ``['git', 'rev-parse', 'HEAD']``, no shell /
    input / stdin) and ``platform.system``. Plus NOOA banned modules, sandbox / CodeAct
    settings defaulting on, unreviewed runtimes and non-Predict agent classes."""

    from test_agent_harness import nooa_violations

    if settings_fields is None:
        from app.config import Settings

        settings_fields = {name: field.default for name, field in Settings.model_fields.items()}
    if runtimes is None:
        from app.agents.harness.service import RUNTIMES as runtimes
    if classes is None:
        from app.agents.runtime.base import AGENT_CLASSES as classes
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _aliases(tree)
        found += [f"{rel}: nooa {name}" for name in nooa_violations(tree)]
        probes = {id(n.func) for n in ast.walk(tree) if rel == EXEC_ALLOWED_FILE and isinstance(n, ast.Call)
                  and _resolve(n.func, aliases).startswith("subprocess.") and _allowed_probe(n)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                hit = next((m for m in modules if m.split(".")[0] in EXEC_MODULES), None)
                if hit == "subprocess" and rel == EXEC_ALLOWED_FILE:
                    hit = None
            elif isinstance(node, ast.Call):
                hit = _call_hit(node, aliases)
            else:
                hit = None if id(node) in probes else _ref_hit(node, aliases)
            if hit:
                found.append(f"{rel}:{getattr(node, 'lineno', 0)}: {hit}")
    found += [f"setting {name} defaults on" for name, default in settings_fields.items()
              if SANDBOX_FLAG.search(name) and default]
    found += [f"runtime {name} is not reviewed" for name in runtimes if name not in REVIEWED_RUNTIMES]
    found += [f"agent class {key} uses {item.strategy}" for key, item in classes.items() if item.strategy != "predict"]
    return found


def check_codeact_ban(**kwargs: Any) -> None:
    _require(codeact_findings(**kwargs), "generated code could run in the API / worker")


# --- goldens --------------------------------------------------------------------------------------


def run_transcript(db: Session, result: Any, ids: Ids | None = None) -> dict[str, Any]:
    """An agent run as a normalised golden: result, events, proposals and ledger rows."""

    ids = ids or Ids()
    db.expire_all()
    run = db.get(AgentRun, result.run_id)
    proposals = db.scalars(select(AgentProposal).where(AgentProposal.workspace_id == run.workspace_id,
                                                       AgentProposal.run_id == run.id).order_by(AgentProposal.created_at))
    ledger = db.scalars(select(LlmInvocation).where(LlmInvocation.workspace_id == run.workspace_id,
                                                    LlmInvocation.agent_run_id == run.id)
                        .order_by(LlmInvocation.started_at, LlmInvocation.created_at))
    return ids.norm({
        "result": {"status": result.status, "error_code": result.error_code, "proposals": len(result.proposal_ids)},
        "run": {k: getattr(run, k) for k in ("kind", "agent_key", "runtime", "purpose", "decision_point_key",
                                             "data_class", "outcome_scope", "status", "error_code", "model")},
        "events": [{"type": e.type, "payload": e.payload} for e in load_events(db, workspace_id=run.workspace_id,
                                                                            run_id=run.id)],
        "proposals": [{k: getattr(p, k) for k in ("proposal_type", "status", "level_at_proposal", "decision_point_key",
                                                  "validator_verdict", "payload", "citations")} for p in proposals],
        "ledger": [ledger_summary(r) for r in ledger],
    })


def ledger_summary(row: LlmInvocation) -> dict[str, Any]:
    return {k: getattr(row, k) for k in ("purpose", "status", "provider", "model", "prompt_version", "validator_verdict",
                                         "refusal_code", "cache_hit", "data_class", "outcome_scope", "input_tokens",
                                         "output_tokens", "llm_used")}


def assert_golden(name: str, actual: Any) -> None:
    """Compare with ``tests/golden/ai/<name>.json``; ``DCLAB_UPDATE_GOLDEN=1`` rewrites it."""

    actual = json.loads(json.dumps(actual, default=str))
    target = GOLDENS_ROOT / f"{name}.json"
    if UPDATE_GOLDENS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert target.exists(), f"missing {target}; run with DCLAB_UPDATE_GOLDEN=1"
    assert actual == json.loads(target.read_text(encoding="utf-8")), f"golden {name} changed"
