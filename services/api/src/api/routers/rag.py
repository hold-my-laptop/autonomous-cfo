"""Public, read-only question answering over retrieved financial records."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.agent import AgentUnavailableError
from api.deps import AgentClientDep, RepositoryDep
from api.money import format_eur
from api.rag import retrieve

router = APIRouter(tags=["assistant"])

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AskResponse(BaseModel):
    answer: str
    sources: list[dict[str, str]]
    model: str | None = None
    answer_source: Literal["MODEL", "TEMPLATE"] = "MODEL"
    fallback_reason: str | None = None


def _listing(sources: list[dict[str, str]], reason: str) -> AskResponse:
    """Used when the AI service cannot be reached: the retrieved records, by relevance."""
    items = sources[:5]
    head = f"Records matching your question ({len(items)}):"
    lines = [f"• {s['date']} · {s['counterparty']} · {format_eur(int(s['amount_cents']))} ({s['category']})"
             for s in items]
    return AskResponse(answer="\n".join([head, *lines]), sources=items,
                       answer_source="TEMPLATE", fallback_reason=reason)


@router.post("/assistant/ask", response_model=AskResponse)
def ask(body: AskRequest, repository: RepositoryDep, agent_client: AgentClientDep) -> AskResponse:
    """Answers from at most 12 retrieved records, citing them. Never an error for model
    trouble: without a usable model answer the matching records are listed instead."""
    with repository.read() as state:
        sources = retrieve(state, body.question)
    if not sources:
        return AskResponse(answer="I found no financial records matching this question.",
                           sources=[], answer_source="TEMPLATE")
    try:
        result = agent_client.ask({"question": body.question, "sources": sources})
        return AskResponse.model_validate(result)
    except (AgentUnavailableError, ValueError) as exc:
        return _listing(sources, f"AI service unavailable: {type(exc).__name__}")
