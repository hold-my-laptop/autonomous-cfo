from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.deps import RepositoryDep, SettingsDep
from api.domain.importer import StatementImportError

router = APIRouter(prefix="/demo", tags=["demo"])


class ResetResult(BaseModel):
    reset: Literal[True]
    as_of_date: date
    financial_version: int


@router.post("/reset")
def reset_demo(repository: RepositoryDep, settings: SettingsDep) -> ResetResult:
    """Rebuild the demo company from the sample statement. Only when DEMO_MODE is on."""
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "demo mode is off")
    try:
        state = repository.reset()
    except StatementImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return ResetResult(
        reset=True, as_of_date=state.as_of, financial_version=state.financial_version
    )
