from fastapi import APIRouter

from api.deps import RepositoryDep
from api.schemas import Mandate

router = APIRouter(prefix="/mandate", tags=["mandate"])


@router.get("")
def get_mandate(repository: RepositoryDep) -> Mandate:
    with repository.read() as state:
        return Mandate.from_domain(state.active_mandate)


@router.get("/versions")
def list_mandate_versions(repository: RepositoryDep) -> list[Mandate]:
    """All versions, newest first."""
    with repository.read() as state:
        return [
            Mandate.from_domain(m)
            for m in sorted(state.mandates, key=lambda m: m.version, reverse=True)
        ]
