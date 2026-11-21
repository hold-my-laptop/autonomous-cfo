"""Deterministic formulas for Vero Proof (docs/PROOF.md §5.7).

Pure functions over integer cents, basis points and index tenths, rounding half up. The model
chooses a formula and its inputs through the `compute` tool; it never does the arithmetic. Each
formula has an id, an input schema and a result, and the whole registry has ENGINE_VERSION."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.dates import window_end
from api.domain.forecast import ForecastInput, compute_forecast
from api.money import DecimalStr, Money, convert

ENGINE_VERSION = "proof-engine-1"


def _half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bps(delta: int, base: int) -> int:
    """delta / base in basis points, half up (8 % = 800)."""
    if base == 0:
        raise ValueError("the base must not be 0")
    return _half_up(Decimal(delta) * 10_000 / Decimal(base))


class _Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VarianceIn(_Inputs):
    previous: Money = Field(description="Previous amount in cents")
    billed: Money = Field(description="New billed amount in cents")


class VpiIn(_Inputs):
    base_amount: Money = Field(description="Amount set at the base period, in cents")
    base_index: int = Field(gt=0, description="Index at the base period, in tenths (117.6 = 1176)")
    current_index: int = Field(gt=0, description="Latest index, in tenths")
    threshold_bps: int = Field(ge=0, description="Minimum index change before an adjustment")


class FxIn(_Inputs):
    amount: Money = Field(description="Amount in the foreign currency, in cents")
    rate: DecimalStr = Field(description="EUR per unit of the foreign currency, e.g. \"1.040\"")


class PriceListCheckIn(_Inputs):
    unit_price: DecimalStr = Field(description="Unit price on the invoice, e.g. \"12.90\"")
    list_price: DecimalStr = Field(description="Unit price on the valid price list")


class SurchargeIn(_Inputs):
    net_goods: Money = Field(description="Net value of the goods on the invoice, in cents")
    below_net_goods: Money = Field(description="Surcharge applies below this net value")
    surcharge: Money = Field(description="Surcharge in cents")
    valid_from: date
    invoice_date: date


class AnnualizeIn(_Inputs):
    monthly: Money


class WindowImpactIn(_Inputs):
    outflow_ref_prefix: str = Field(min_length=1, max_length=20, description="e.g. RENT-")
    billed_amount: Money
    justified_amount: Money
    effective_from: date


def variance(i: VarianceIn, _: Any = None) -> dict:
    delta = i.billed - i.previous
    return {"delta": str(delta), "bps": bps(delta, i.previous)}


def vpi_threshold_indexation(i: VpiIn, _: Any = None) -> dict:
    change = bps(i.current_index - i.base_index, i.base_index)
    allowed = change >= i.threshold_bps
    justified = (
        _half_up(Decimal(i.base_amount) * i.current_index / i.base_index)
        if allowed
        else i.base_amount
    )
    return {
        "change_bps": change,
        "threshold_bps": i.threshold_bps,
        "adjustment_allowed": allowed,
        "justified_amount": str(justified),
    }


def fx_conversion(i: FxIn, _: Any = None) -> dict:
    return {"eur_amount": str(convert(i.amount, i.rate)), "rate": format(i.rate, "f")}


def price_list_check(i: PriceListCheckIn, _: Any = None) -> dict:
    difference = i.unit_price - i.list_price
    return {"matches": difference == 0, "difference": format(difference, "f")}


def surcharge_rule(i: SurchargeIn, _: Any = None) -> dict:
    applies = i.invoice_date >= i.valid_from and i.net_goods < i.below_net_goods
    return {"applies": applies, "surcharge": str(i.surcharge if applies else 0)}


def annualize(i: AnnualizeIn, _: Any = None) -> dict:
    return {"yearly": str(i.monthly * 12)}


def window_impact(i: WindowImpactIn, data: ForecastInput) -> dict:
    """The forecast twice: the item's outflows from `effective_from` at the billed amount and at
    the justified amount. Returns what the difference costs inside the window."""
    end = window_end(data.as_of, data.horizon_days)
    affected = [
        o
        for o in data.outflows
        if o.ref.startswith(i.outflow_ref_prefix) and o.scheduled_date >= i.effective_from
    ]

    def at(amount: int) -> ForecastInput:
        ids = {o.id for o in affected}
        return replace(
            data,
            outflows=tuple(
                replace(o, amount=amount) if o.id in ids else o for o in data.outflows
            ),
        )

    billed = compute_forecast(at(i.billed_amount)).summary
    justified = compute_forecast(at(i.justified_amount)).summary
    inside = [o for o in affected if o.scheduled_date <= end]
    return {
        "outflows_in_window": [o.ref for o in inside],
        "outflow_difference": str(len(inside) * (i.billed_amount - i.justified_amount)),
        "lowest_balance_billed": str(billed.lowest.closing),
        "lowest_balance_justified": str(justified.lowest.closing),
        "lowest_balance_difference": str(justified.lowest.closing - billed.lowest.closing),
    }


@dataclass(frozen=True)
class Formula:
    id: str
    description: str
    inputs: type[_Inputs]
    run: Callable[[Any, ForecastInput | None], dict]
    needs_forecast: bool = False


FORMULAS: dict[str, Formula] = {
    f.id: f
    for f in (
        Formula("variance", "delta = billed - previous; bps = delta x 10000 / previous",
                VarianceIn, variance),
        Formula(
            "vpi_threshold_indexation",
            "change_bps = (current - base) x 10000 / base; below threshold_bps the justified "
            "amount is the base amount, otherwise base amount x current / base",
            VpiIn,
            vpi_threshold_indexation,
        ),
        Formula("fx_conversion", "foreign cents x rate, half up", FxIn, fx_conversion),
        Formula("price_list_check", "the unit price equals the list price",
                PriceListCheckIn, price_list_check),
        Formula(
            "surcharge_rule",
            "net goods below below_net_goods on or after valid_from -> surcharge, else 0",
            SurchargeIn,
            surcharge_rule,
        ),
        Formula("annualize", "monthly x 12", AnnualizeIn, annualize),
        Formula(
            "window_impact",
            "the cash forecast at the billed and at the justified amount: the outflow "
            "difference inside the 91-day window and the change in the lowest balance",
            WindowImpactIn,
            window_impact,
            needs_forecast=True,
        ),
    )
}
