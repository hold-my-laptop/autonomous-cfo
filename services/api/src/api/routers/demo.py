from datetime import date
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from api.deps import RepositoryDep, SettingsDep
from api.domain.importer import StatementImportError
from api.errors import ApiError, ErrorCode

router = APIRouter(prefix="/demo", tags=["demo"])


class ResetResult(BaseModel):
    reset: Literal[True]
    as_of_date: date
    financial_version: int


@router.post("/reset")
def reset_demo(repository: RepositoryDep, settings: SettingsDep) -> ResetResult:
    """Rebuild the demo company from the sample statement. Only when DEMO_MODE is on."""
    if not settings.demo_mode:
        raise ApiError(status.HTTP_403_FORBIDDEN, ErrorCode.DEMO_DISABLED)
    try:
        state = repository.reset()
    except StatementImportError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.STATE_NOT_LOADED, str(exc)
        ) from exc
    return ResetResult(
        reset=True, as_of_date=state.as_of, financial_version=state.financial_version
    )
