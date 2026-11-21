"""MLflow tracing: one trace per agent run with LLM and tool spans; never breaks a run.

Needs a store: full `mlflow` (SQLite) or MLFLOW_TEST_TRACKING_URI=http://localhost:5000 with
`mlflow-skinny`. Without one the trace tests are skipped."""

import asyncio
import os

import pytest

from ai import tracing
from ai.agent import run_agent
from ai.config import Settings

from conftest import ScriptedLLM
from test_agent import GOOD_SUBMIT, MOVE, START

mlflow = pytest.importorskip("mlflow")


@pytest.fixture
def traced(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "false")
    s = Settings(llm_mode="live", llm_base_url="http://unused/v1", llm_timeout_s=5,
                 mlflow_tracking_uri=os.getenv("MLFLOW_TEST_TRACKING_URI", f"sqlite:///{tmp_path}/mlflow.db"), mlflow_experiment="test-agent")
    if not tracing.setup(s):
        pytest.skip("MLflow store not usable here")
    yield s
    tracing._mlflow = None
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)  # set_tracking_uri exports it


def test_run_is_traced_with_llm_and_tool_spans(traced, backend):
    llm = ScriptedLLM([("get_forecast", {}), ("find_reschedule_options", {}),
                       ("size_credit_line", {"after_moves": [MOVE]}), GOOD_SUBMIT])
    assert asyncio.run(run_agent(START, llm, backend, traced)) == "COMPLETED"

    trace = mlflow.get_trace(mlflow.get_last_active_trace_id())
    names = [s.name for s in trace.data.spans]
    assert names[0] == "agent_run"
    assert names.count("llm_call") == 4
    assert {"tool:get_forecast", "tool:find_reschedule_options", "tool:size_credit_line"} <= set(names)
    tags = trace.info.tags
    assert tags["run_id"] == "r1" and tags["status"] == "COMPLETED" and tags["model"] == "scripted-model"
    root = trace.data.spans[0]
    assert [p["kind"] for p in root.outputs["proposals"]] == ["RESCHEDULE_PAYMENT", "CREDIT_LINE"]


def test_failed_run_is_tagged_with_reason(traced, backend):
    llm = ScriptedLLM(["no tools", "still none", "nope"])
    assert asyncio.run(run_agent(START, llm, backend, traced)) == "FAILED"
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id())
    assert trace.info.tags["status"] == "FAILED"
    assert trace.info.tags["failure_reason"] == "NO_FINAL_ANSWER"


def test_broken_mlflow_does_not_break_the_run(backend, settings, monkeypatch):
    class Broken:
        def start_span(self, **kw):
            raise RuntimeError("mlflow down")

    monkeypatch.setattr(tracing, "_mlflow", Broken())
    llm = ScriptedLLM([("get_forecast", {}), ("find_reschedule_options", {}),
                       ("size_credit_line", {"after_moves": [MOVE]}), GOOD_SUBMIT])
    assert asyncio.run(run_agent(START, llm, backend, settings)) == "COMPLETED"


def test_tracing_off_without_uri(settings):
    off = settings.model_copy(update={"mlflow_tracking_uri": ""})
    assert tracing.setup(off) is False and not tracing.enabled()
