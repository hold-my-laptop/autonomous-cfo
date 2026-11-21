"""Public, read-only question answering over retrieved financial records.

The AI service still receives the records as `api.rag.retrieve` builds them (`amount_cents`,
lower-case `kind`); the public answer carries them typed, with the API's conventions."""

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.agent import AgentUnavailableError
from api.deps import AgentClientDep, RepositoryDep
from api.money import Money, format_eur
from api.rag import retrieve

router = APIRouter(tags=["assistant"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AssistantSource(BaseModel):
    """A record the answer rests on."""

    id: str
    kind: Literal["TRANSACTION", "OUTFLOW", "INFLOW"] = Field(
        description="TRANSACTION: booked; OUTFLOW / INFLOW: planned"
    )
    date: dt.date = Field(description="Booking date, or the planned date")
    amount: Money = Field(description="Signed: negative leaves the account")
    currency: Literal["EUR"]
    category: str
    counterparty: str
    description: str

    @classmethod
    def from_record(cls, record: dict[str, str]) -> "AssistantSource":
        return cls(
            id=record["id"],
            kind=record["kind"].upper(),
            date=dt.date.fromisoformat(record["date"]),
            amount=int(record["amount_cents"]),
            currency=record["currency"],
            category=record["category"],
            counterparty=record["counterparty"],
            description=record["description"],
        )


class AskResponse(BaseModel):
    answer: str
    sources: list[AssistantSource] = Field(description="The records the answer cites")
    model: str | None = None
    answer_source: Literal["MODEL", "TEMPLATE"] = "MODEL"
    fallback_reason: str | None = None


class _ModelAnswer(BaseModel):
    answer: str = Field(min_length=1)
    sources: list[dict[str, Any]] = []
    model: str | None = None
    answer_source: Literal["MODEL", "TEMPLATE"] = "MODEL"
    fallback_reason: str | None = None


def _listing(sources: list[dict[str, str]], reason: str) -> AskResponse:
    """Used when the AI service cannot be reached: the retrieved records, by relevance."""
    items = sources[:5]
    head = f"Records matching your question ({len(items)}):"
    lines = [
        f"• {s['date']} · {s['counterparty']} · "
        f"{format_eur(int(s['amount_cents']))} ({s['category']})"
        for s in items
    ]
    return AskResponse(
        answer="\n".join([head, *lines]),
        sources=[AssistantSource.from_record(s) for s in items],
        answer_source="TEMPLATE",
        fallback_reason=reason,
    )


@router.post("/assistant/ask", response_model=AskResponse)
def ask(body: AskRequest, repository: RepositoryDep, agent_client: AgentClientDep) -> AskResponse:
    """Answers from at most 12 retrieved records, citing them. Never an error for model
    trouble: without a usable model answer the matching records are listed instead. Only
    records Vero retrieved can be cited; any other id in the model's answer is dropped."""
    with repository.read() as state:
        sources = retrieve(state, body.question)
    if not sources:
        return AskResponse(
            answer="I found no financial records matching this question.",
            sources=[],
            answer_source="TEMPLATE",
        )
    try:
        result = _ModelAnswer.model_validate(
            agent_client.ask({"question": body.question, "sources": sources})
        )
    except (AgentUnavailableError, ValueError) as exc:
        return _listing(sources, f"AI service unavailable: {type(exc).__name__}")
    retrieved = {s["id"]: s for s in sources}
    cited = [str(s.get("id")) for s in result.sources]
    return AskResponse(
        answer=result.answer,
        sources=[
            AssistantSource.from_record(retrieved[i])
            for i in dict.fromkeys(cited)
            if i in retrieved
        ],
        model=result.model,
        answer_source=result.answer_source,
        fallback_reason=result.fallback_reason,
    )
