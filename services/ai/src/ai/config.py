from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AI service configuration. Every field can be overridden by an environment
    variable of the same name in upper case (e.g. LLM_BASE_URL) or in a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (vLLM on OpenShift AI, OpenAI-compatible). Never a public LLM API.
    llm_mode: Literal["live", "stub"] = "live"
    llm_base_url: str = ""  # e.g. http://vero-llm-predictor.autonomous-cfo.svc.cluster.local:8080/v1
    llm_model: str = ""  # empty: use the first model the server lists
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 600

    # Backend tools (contract section 3)
    api_internal_url: str = "http://api:8080/internal/v1"
    internal_token: SecretStr | None = None
    backend_timeout_s: float = 15.0

    # Agent defaults (the backend's AgentRunStart.limits wins)
    default_max_tool_calls: int = 8
    default_deadline_s: int = 60
    explain_timeout_s: float = 9.0  # backend gives up at 10 s

    prompt_version: str = "agent-prompt-1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
