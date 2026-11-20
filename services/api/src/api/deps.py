from typing import Annotated

from fastapi import Depends, Request

from api.agent import AgentClient, AgentOrchestrator
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


def get_agent_client(request: Request) -> AgentClient:
    return request.app.state.agent_client


AgentClientDep = Annotated[AgentClient, Depends(get_agent_client)]
