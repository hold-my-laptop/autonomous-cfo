"""Size a short-term credit line for the gap the planner could not close."""

from dataclasses import dataclass, replace
from datetime import date

from api.domain.forecast import ForecastInput, ForecastSummary, compute_forecast
from api.domain.models import CreditLine
from api.money import Cents

DEFAULT_ROUNDING = 500_000


@dataclass(frozen=True, slots=True)
class CreditSizing:
    """`credit_line` is None when there is no gap."""

    needed: bool
    credit_line: CreditLine | None
    shortfall: Cents
    rounded_to: Cents
    headroom_at_lowest: Cents
    forecast_with_line: ForecastSummary


def round_up(amount: Cents, step: Cents) -> Cents:
    return -(-amount // step) * step


def size_credit_line(
    data: ForecastInput, available_until: date, rounding: Cents = DEFAULT_ROUNDING
) -> CreditSizing:
    """Smallest line (rounded up to `rounding`) that keeps every day at or above the floor.

    The line starts on the first day below the floor and runs at least until `available_until`,
    longer if the gap lasts beyond it."""
    if rounding <= 0:
        raise ValueError("rounding must be positive")
    forecast = compute_forecast(data)
    below = [d for d in forecast.days if d.below_floor]
    if not below:
        return CreditSizing(
            needed=False,
            credit_line=None,
            shortfall=0,
            rounded_to=rounding,
            headroom_at_lowest=forecast.summary.headroom_at_lowest,
            forecast_with_line=forecast.summary,
        )

    shortfall = max(data.cash_floor - (d.closing + d.credit_available) for d in below)
    line = CreditLine(
        amount=round_up(shortfall, rounding),
        available_from=below[0].date,
        available_until=max(available_until, below[-1].date),
    )
    with_line = compute_forecast(replace(data, credit_lines=(*data.credit_lines, line))).summary
    return CreditSizing(
        needed=True,
        credit_line=line,
        shortfall=shortfall,
        rounded_to=rounding,
        headroom_at_lowest=with_line.headroom_at_lowest,
        forecast_with_line=with_line,
    )
