"""Shared shapes of the agent tools: arguments, outputs and the registry entry."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from api.config import Settings
from api.domain.models import Run
from api.state import DemoState


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


@dataclass(frozen=True)
class ToolOutput:
    result: Any
    label: str
    result_label: str | None = None
    log: dict | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    handler: Callable[[DemoState, Run, Any, Settings], ToolOutput]
