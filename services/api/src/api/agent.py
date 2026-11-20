"""Agent run orchestration: start the AI service, watch the deadline, accept its callbacks,
fall back to the backend pipeline whenever the agent cannot finish, and ask the AI service for
the texts once the run has acted."""

import logging
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

from api.config import Settings
from api.decisions import fall_back, finalize_agent_run
from api.domain.models import Run, RunStatus, TraceStep
from api.explain import ExplainResult, settle
from api.schemas import ModelHealth
from api.state import DemoState, StateRepository

logger = logging.getLogger(__name__)


class AgentUnavailableError(Exception):
    pass


class RunClosedError(Exception):
    pass


class AgentClient(Protocol):
    def start_run(self, payload: dict[str, Any]) -> None: ...

    def explain(self, payload: dict[str, Any]) -> ExplainResult: ...

    def model_health(self) -> ModelHealth: ...


class HttpAgentClient:
    """Calls the AI service; any failure raises AgentUnavailableError."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _headers(self) -> dict[str, str]:
        token = self._settings.internal_token
        return {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}

    def _url(self, path: str) -> str:
        return f"{self._settings.ai_url.rstrip('/')}{path}"

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
        try:
            return httpx.post(
                self._url(path), json=payload, headers=self._headers(), timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise AgentUnavailableError(str(exc)) from exc

    def start_run(self, payload: dict[str, Any]) -> None:
        response = self._post("/v1/agent/runs", payload, self._settings.agent_start_timeout_s)
        if response.status_code != 202:
            raise AgentUnavailableError(f"AI service answered {response.status_code}")

    def explain(self, payload: dict[str, Any]) -> ExplainResult:
        response = self._post("/v1/explain", payload, self._settings.explain_timeout_s)
        if response.status_code != 200:
            raise AgentUnavailableError(f"AI service answered {response.status_code}")
        try:
            return ExplainResult.model_validate_json(response.content)
        except ValueError as exc:
            raise AgentUnavailableError(f"unusable explain result: {exc}") from exc

    def model_health(self) -> ModelHealth:
        """The AI service's own model check, passed through. When the AI service itself
        cannot be reached or answers nonsense, a failed ModelHealth says so."""
        started = time.perf_counter()
        try:
            response = httpx.get(
                self._url("/health/model"),
                headers=self._headers(),
                timeout=self._settings.model_health_timeout_s,
            )
            return ModelHealth.model_validate_json(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            return ModelHealth(
                ok=False,
                model="unknown",
                mode=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"AI service unavailable: {type(exc).__name__}"[:300],
            )


def start_payload(state: DemoState, run: Run, settings: Settings) -> dict[str, Any]:
    event = next(e for e in state.events if e.id == run.event_id)
    return {
        "run_id": str(run.id),
        "event": {
            "type": run.trigger_type.value,
            "label": run.trigger_label,
            "payload": event.payload,
        },
        "company": {
            "name": state.company.name,
            "as_of_date": state.as_of.isoformat(),
            "currency": state.company.currency,
        },
        "limits": {
            "max_tool_calls": settings.agent_max_tool_calls,
            "deadline_s": int(settings.agent_deadline_s),
        },
    }


def _running(state: DemoState, run_id: UUID) -> Run:
    run = state.runs.get(run_id)
    if run is None or run.status is not RunStatus.RUNNING:
        raise RunClosedError(f"run {run_id} is not running")
    return run


class AgentOrchestrator:
    def __init__(
        self, repository: StateRepository, settings: Settings, client: AgentClient
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._client = client
        self._timers: dict[UUID, threading.Timer] = {}
        self._lock = threading.Lock()

    def start(self, run_id: UUID) -> None:
        """Hand the run to the AI service and start the deadline; fall back if it is down."""
        with self._repository.read() as state:
            payload = start_payload(state, _running(state, run_id), self._settings)
        timer = threading.Timer(
            self._settings.agent_deadline_s, self.fall_back, args=(run_id, "deadline passed")
        )
        timer.daemon = True
        with self._lock:
            self._timers[run_id] = timer
        timer.start()
        try:
            self._client.start_run(payload)
        except AgentUnavailableError as exc:
            logger.warning("agent unavailable for run %s: %s", run_id, exc)
            self.fall_back(run_id, "AI service unreachable")

    def recover(self) -> None:
        """Runs left RUNNING by a restart have lost their agent; finish them without it.
        Runs left FINALIZING only miss their texts."""
        with self._repository.read() as state:
            running = [r.id for r in state.runs.values() if r.status is RunStatus.RUNNING]
            finalizing = [r.id for r in state.runs.values() if r.status is RunStatus.FINALIZING]
        for run_id in running:
            self.fall_back(run_id, "service restarted")
        for run_id in finalizing:
            self.explain(run_id)

    def explain(self, run_id: UUID) -> None:
        """Ask the AI service for the texts of a FINALIZING run, outside any transaction,
        then finish the run. On any failure the backend templates stay (source TEMPLATE)."""
        with self._repository.read() as state:
            run = state.runs.get(run_id)
            request = run.explain_request if run and run.status is RunStatus.FINALIZING else None
        if request is None:
            return
        try:
            result = self._client.explain(request)
        except AgentUnavailableError as exc:
            logger.warning("explain failed for run %s, keeping templates: %s", run_id, exc)
            result = None
        with self._repository.write() as state:
            settle(state, run_id, result, datetime.now(UTC))

    def _stop_timer(self, run_id: UUID) -> None:
        with self._lock:
            timer = self._timers.pop(run_id, None)
        if timer is not None:
            timer.cancel()

    def _fall_back(self, state: DemoState, run: Run, reason: str) -> None:
        fall_back(
            state,
            run,
            reason,
            datetime.now(UTC),
            self._settings.credit_line_available_until,
            self._settings.credit_line_rounding,
        )

    def fall_back(self, run_id: UUID, reason: str) -> None:
        self._stop_timer(run_id)
        with self._repository.write() as state:
            run = state.runs.get(run_id)
            if run is None or run.status is not RunStatus.RUNNING:
                return
            self._fall_back(state, run, reason)
        self.explain(run_id)

    def note(self, run_id: UUID, text: str) -> None:
        with self._repository.write() as state:
            run = _running(state, run_id)
            step = TraceStep(len(run.trace) + 1, "THINK", "AGENT", text[:500], datetime.now(UTC))
            state.runs[run_id] = replace(run, trace=(*run.trace, step))

    def complete(self, run_id: UUID, completion: dict[str, Any]) -> None:
        with self._repository.write() as state:
            run = _running(state, run_id)
            self._stop_timer(run_id)
            if state.financial_version != run.started_financial_version:
                self._fall_back(state, run, "data changed during the run")
                return
            finalize_agent_run(
                state,
                run,
                completion.get("proposals") or [],
                completion.get("summary_note"),
                {k: completion.get(k) for k in ("model", "prompt_version", "tool_calls")},
                datetime.now(UTC),
                self._settings.credit_line_available_until,
                self._settings.credit_line_rounding,
            )

    def fail(self, run_id: UUID, reason: str) -> None:
        with self._repository.write() as state:
            run = _running(state, run_id)
            self._stop_timer(run_id)
            self._fall_back(state, run, reason[:100])

    def close(self) -> None:
        with self._lock:
            run_ids = list(self._timers)
        for run_id in run_ids:
            self._stop_timer(run_id)
