from datetime import date

import pytest

from api.config import get_settings
from api.dates import window_end
from api.domain.balances import balance_before
from api.domain.forecast import ForecastInput
from api.domain.importer import StatementImport, load_statement
from api.domain.models import Category
from api.domain.recurring import detect_recurring, project_outflows
from api.domain.revenue import build_revenue_model
from api.domain.schedule import project_scheduled, sort_outflows
from api.seed import PIZZERIA_TAX_SCHEDULE

DEMO_AS_OF = date(2025, 5, 5)
DEMO_CASH_FLOOR = -500_000


@pytest.fixture(scope="session")
def sample_statement() -> StatementImport:
    path = get_settings().statement_path
    if not path.exists():
        pytest.skip(f"sample CSV not found at {path}")
    return load_statement(path)


@pytest.fixture(scope="session")
def demo_input(sample_statement: StatementImport) -> ForecastInput:
    """Pizzeria Amalfi as of 5 May 2025, before any planned event (state S0)."""
    settings = get_settings()
    transactions = sample_statement.transactions
    history = [t for t in transactions if t.booking_date < DEMO_AS_OF]
    end = window_end(DEMO_AS_OF, settings.forecast_horizon_days)
    outflows = project_outflows(
        detect_recurring(history, DEMO_AS_OF),
        DEMO_AS_OF,
        end,
        {Category.PARTNER_TRANSFER: settings.partner_transfer_movable_days},
    )
    outflows += project_scheduled(PIZZERIA_TAX_SCHEDULE, DEMO_AS_OF, end)
    return ForecastInput(
        as_of=DEMO_AS_OF,
        start_balance=balance_before(settings.opening_balance, transactions, DEMO_AS_OF),
        cash_floor=DEMO_CASH_FLOOR,
        revenue_model=build_revenue_model(history, DEMO_AS_OF, settings.revenue_model_weeks),
        outflows=sort_outflows(outflows),
        horizon_days=settings.forecast_horizon_days,
    )
