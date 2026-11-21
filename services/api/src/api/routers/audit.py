from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import RepositoryDep
from api.schemas import AuditEntry

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit_entries(
    repository: RepositoryDep,
    after_seq: Annotated[int, Query(ge=0, description="Only entries after this seq")] = 0,
) -> list[AuditEntry]:
    """The activity log, oldest first: what the owner, the agent, the rules and the system did.
    Each entry is sealed to the one before it (see AuditEntry), so the browser can verify that
    nothing was changed."""
    with repository.read() as state:
        return [AuditEntry.from_domain(e) for e in state.audit if e.seq > after_seq]
