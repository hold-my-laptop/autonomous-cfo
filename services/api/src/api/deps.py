from typing import Annotated

from fastapi import Depends, Request

from api.agent import AgentOrchestrator
from api.config import Settings
from api.state import StateRepository


def get_repository(request: Request) -> StateRepository:
    return request.app.state.repository


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


RepositoryDep = Annotated[StateRepository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_agent(request: Request) -> AgentOrchestrator:
    return request.app.state.agent


AgentDep = Annotated[AgentOrchestrator, Depends(get_agent)]
