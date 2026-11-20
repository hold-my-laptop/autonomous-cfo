from fastapi import APIRouter

from api.deps import RepositoryDep
from api.schemas import Backtest, Forecast

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("")
def get_forecast(repository: RepositoryDep, weekly: bool = False) -> Forecast:
    """91-day daily forecast; `weekly=true` adds 13 weekly buckets for display."""
    with repository.read() as state:
        return Forecast.from_domain(state.forecast(), state, weekly=weekly)


@router.get("/backtest")
def get_backtest(repository: RepositoryDep) -> Backtest:
    """The current forecast compared with what was actually booked in the sample year."""
    with repository.read() as state:
        return Backtest.from_domain(state.backtest(), state)
