from typing import Annotated

from fastapi import Depends, Request

from api.agent import AgentClient, AgentOrchestrator
from api.config import Settings
from api.proof_runs import ProofPipeline
from api.state import StateRepository


def get_repository(request: Request) -> StateRepository:
    """The repository, after retrying a failed start if one is due."""
    request.app.state.loader.ensure()
    return request.app.state.repository


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


RepositoryDep = Annotated[StateRepository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_agent(request: Request) -> AgentOrchestrator:
    return request.app.state.agent


AgentDep = Annotated[AgentOrchestrator, Depends(get_agent)]


def get_proof(request: Request) -> ProofPipeline:
    return request.app.state.proof


ProofDep = Annotated[ProofPipeline, Depends(get_proof)]


def get_agent_client(request: Request) -> AgentClient:
    return request.app.state.agent_client


AgentClientDep = Annotated[AgentClient, Depends(get_agent_client)]
