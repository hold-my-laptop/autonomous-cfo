"""Optional MLflow tracing for agent runs, chat answers and explanations.

Off unless MLFLOW_TRACKING_URI is set. Tracing must never change what the agent does:
every MLflow call is wrapped, and any error turns tracing off for that span (or for the
whole process if setup fails). Each agent run becomes one trace:

    agent_run (CHAIN)            event, company, limits -> status, proposals
      ├─ llm_call (CHAT_MODEL)   last messages, offered tools -> content, tool calls
      ├─ tool:get_forecast (TOOL)  arguments -> backend result
      └─ ...

Trace tags carry run_id, model, prompt_version and the final status, so runs can be
filtered in the MLflow UI ("tags.status = 'COMPLETED'").
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from .config import Settings

log = logging.getLogger("ai.tracing")

_mlflow: Any = None  # the imported module once setup succeeded


def setup(settings: Settings) -> bool:
    """Connects to MLflow. Returns True when tracing is on. Safe to call more than once."""
    global _mlflow
    if not settings.mlflow_tracking_uri:
        _mlflow = None
        return False
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        _mlflow = mlflow
        log.info("MLflow tracing on: %s, experiment %r", settings.mlflow_tracking_uri, settings.mlflow_experiment)
        return True
    except Exception as e:  # noqa: BLE001 - no MLflow, no tracing; the agent still works
        log.warning("MLflow tracing off: %s: %s", type(e).__name__, e)
        _mlflow = None
        return False


def enabled() -> bool:
    return _mlflow is not None


class _Span:
    """Wraps a live MLflow span; every method swallows errors."""

    def __init__(self, live: Any = None):
        self._live = live

    def set_inputs(self, value: Any) -> None:
        self._call("set_inputs", value)

    def set_outputs(self, value: Any) -> None:
        self._call("set_outputs", value)

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        self._call("set_attributes", {k: v for k, v in attrs.items() if v is not None})

    def tag_trace(self, tags: dict[str, Any]) -> None:
        """Tags on the whole trace (searchable in the UI)."""
        if self._live is None or _mlflow is None:
            return
        try:
            _mlflow.update_current_trace(tags={k: str(v) for k, v in tags.items() if v is not None})
        except Exception as e:  # noqa: BLE001
            log.debug("trace tags failed: %s", e)

    def _call(self, method: str, value: Any) -> None:
        if self._live is None:
            return
        try:
            getattr(self._live, method)(value)
        except Exception as e:  # noqa: BLE001
            log.debug("span.%s failed: %s", method, e)


NOOP = _Span()


@contextmanager
def span(name: str, span_type: str = "UNKNOWN", inputs: Any = None) -> Iterator[_Span]:
    """A traced block. Exceptions from the block propagate unchanged and mark the span as error."""
    if _mlflow is None:
        yield NOOP
        return
    try:
        cm = _mlflow.start_span(name=name, span_type=span_type)
        live = cm.__enter__()
    except Exception as e:  # noqa: BLE001
        log.debug("start_span %s failed: %s", name, e)
        yield NOOP
        return
    wrapper = _Span(live)
    if inputs is not None:
        wrapper.set_inputs(inputs)
    exc: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield wrapper
    except BaseException as e:
        exc = (type(e), e, e.__traceback__)
        raise
    finally:
        try:
            cm.__exit__(*exc)
        except Exception as e:  # noqa: BLE001
            log.debug("end_span %s failed: %s", name, e)


def tail(messages: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    """The last few chat messages, which is what changed since the previous LLM span."""
    return messages[-n:]
