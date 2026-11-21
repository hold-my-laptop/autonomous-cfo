"""Vero Proof's formula engine (docs/PROOF.md §5.7): deterministic, integer cents, half-up.

The model chooses a formula and its inputs; only this code computes numbers. Each formula has
an id, a strict input model (also the tool's input schema), an output model and a pure
function. Percentages are whole basis points, rounded half up."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.domain.forecast import ForecastInput, compute_forecast
from api.money import Cents, DecimalStr, Money, convert

ENGINE_VERSION = "proof-engine-1.0.0"


class FormulaError(ValueError):
    """An unknown formula or inputs it cannot use; the message says which."""


class _Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Outputs(BaseModel):
    model_config = ConfigDict(frozen=True)


def _half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _bps(part: int, whole: int) -> int:
    """part / whole in basis points, half up."""
    return _half_up(Decimal(part) * 10_000 / Decimal(whole))


class VarianceIn(_Inputs):
    billed: Money
    previous: Money = Field(gt=0)


class VarianceOut(_Outputs):
    delta: Money
    bps: int


def variance(i: VarianceIn) -> VarianceOut:
    delta = i.billed - i.previous
    return VarianceOut(delta=delta, bps=_bps(delta, i.previous))


class VpiIn(_Inputs):
    base_amount: Money = Field(gt=0, description="The amount set at the last adjustment")
    base_index: int = Field(gt=0, description="Index at the last adjustment, tenths (1176)")
    current_index: int = Field(gt=0, description="Latest index, tenths (1212)")
    threshold_bps: int = Field(ge=0, description="Minimum change that allows an adjustment")


class VpiOut(_Outputs):
    change_bps: int
    adjustment_allowed: bool
    justified: Money


def vpi_threshold_indexation(i: VpiIn) -> VpiOut:
    """Adjust in the index's proportion only when it moved by at least the threshold (either
    way), compared exactly before rounding."""
    change = i.current_index - i.base_index
    allowed = abs(change) * 10_000 >= i.threshold_bps * i.base_index
    justified = (
        _half_up(Decimal(i.base_amount) * i.current_index / i.base_index)
        if allowed
        else i.base_amount
    )
    return VpiOut(
        change_bps=_bps(change, i.base_index), adjustment_allowed=allowed, justified=justified
    )


class FxIn(_Inputs):
    amount: Money = Field(description="Cents in the foreign currency")
    rate: DecimalStr = Field(description="Euro per unit of the foreign currency, e.g. 1.040")


class FxOut(_Outputs):
    converted: Money


def fx_conversion(i: FxIn) -> FxOut:
    return FxOut(converted=convert(i.amount, i.rate))


class PriceCheckIn(_Inputs):
    unit_price: DecimalStr
    list_price: DecimalStr
    date: date
    valid_from: date
    valid_to: date | None = None


class PriceCheckOut(_Outputs):
    list_valid_on_date: bool
    matches: bool
    difference: DecimalStr


def price_list_check(i: PriceCheckIn) -> PriceCheckOut:
    valid = i.valid_from <= i.date and (i.valid_to is None or i.date <= i.valid_to)
    return PriceCheckOut(
        list_valid_on_date=valid,
        matches=valid and i.unit_price == i.list_price,
        difference=i.unit_price - i.list_price,
    )


class SurchargeIn(_Inputs):
    net_goods: Money = Field(ge=0, description="Net value of the goods on the delivery")
    date: date
    surcharge: Money = Field(ge=0)
    below_net_goods: Money = Field(gt=0)
    valid_from: date


class SurchargeOut(_Outputs):
    applies: bool
    surcharge: Money


def surcharge_rule(i: SurchargeIn) -> SurchargeOut:
    applies = i.date >= i.valid_from and i.net_goods < i.below_net_goods
    return SurchargeOut(applies=applies, surcharge=i.surcharge if applies else 0)


class AnnualizeIn(_Inputs):
    monthly: Money


class AnnualizeOut(_Outputs):
    yearly: Money


def annualize(i: AnnualizeIn) -> AnnualizeOut:
    return AnnualizeOut(yearly=i.monthly * 12)


class WindowImpactIn(_Inputs):
    outflow_ref_prefix: str = Field(
        min_length=1, description='Outflows to change, by ref prefix: "RENT" for RENT-06'
    )
    effective_from: date
    billed: Money = Field(ge=0, description="Euro cents per payment as billed")
    justified: Money = Field(ge=0, description="Euro cents per payment as justified")


class WindowImpactOut(_Outputs):
    payments: list[str] = Field(description="Refs of the payments in the window, by date")
    outflows_billed: Money
    outflows_justified: Money
    outflow_difference: Money = Field(description="billed − justified inside the window")
    lowest_billed: Money
    lowest_justified: Money
    lowest_difference: Money = Field(description="justified − billed lowest balance")
    window_end: date


def window_impact(i: WindowImpactIn, data: ForecastInput) -> WindowImpactOut:
    """The Phase 1 forecast run twice, with the matching payments from `effective_from` at
    the billed and at the justified amount."""
    targets = [
        o
        for o in data.outflows
        if o.ref.startswith(f"{i.outflow_ref_prefix}-") and o.scheduled_date >= i.effective_from
    ]
    if not targets:
        raise FormulaError(
            f"no {i.outflow_ref_prefix} payment from {i.effective_from} in the forecast window"
        )
    ids = {o.id for o in targets}

    def scenario(amount: Cents) -> ForecastInput:
        return replace(
            data,
            outflows=tuple(replace(o, amount=amount) if o.id in ids else o for o in data.outflows),
        )

    billed = compute_forecast(scenario(i.billed))
    justified = compute_forecast(scenario(i.justified))
    count = len(targets)
    return WindowImpactOut(
        payments=[o.ref for o in sorted(targets, key=lambda o: (o.scheduled_date, o.ref))],
        outflows_billed=i.billed * count,
        outflows_justified=i.justified * count,
        outflow_difference=(i.billed - i.justified) * count,
        lowest_billed=billed.summary.lowest.closing,
        lowest_justified=justified.summary.lowest.closing,
        lowest_difference=justified.summary.lowest.closing - billed.summary.lowest.closing,
        window_end=billed.days[-1].date,
    )


@dataclass(frozen=True, slots=True)
class Formula:
    id: str
    description: str
    inputs: type[_Inputs]
    outputs: type[_Outputs]
    run: Callable[..., _Outputs]
    needs_forecast: bool = False

    def input_schema(self) -> dict[str, Any]:
        return self.inputs.model_json_schema()


FORMULAS: dict[str, Formula] = {
    f.id: f
    for f in (
        Formula(
            "variance",
            "Change from the previous amount: delta and basis points",
            VarianceIn,
            VarianceOut,
            variance,
        ),
        Formula(
            "vpi_threshold_indexation",
            "Rent allowed by an index clause: adjusted in the index's proportion only if the "
            "index moved by at least the threshold",
            VpiIn,
            VpiOut,
            vpi_threshold_indexation,
        ),
        Formula(
            "fx_conversion",
            "Foreign-currency cents to euro cents at a rate, half up",
            FxIn,
            FxOut,
            fx_conversion,
        ),
        Formula(
            "price_list_check",
            "Whether a unit price equals the list price valid on that date",
            PriceCheckIn,
            PriceCheckOut,
            price_list_check,
        ),
        Formula(
            "surcharge_rule",
            "A per-delivery surcharge that applies below a net goods value from a date",
            SurchargeIn,
            SurchargeOut,
            surcharge_rule,
        ),
        Formula(
            "annualize", "A monthly amount over 12 months", AnnualizeIn, AnnualizeOut, annualize
        ),
        Formula(
            "window_impact",
            "Effect of billed vs justified payments on the 13-week cash forecast",
            WindowImpactIn,
            WindowImpactOut,
            window_impact,
            needs_forecast=True,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class FormulaResult:
    formula_id: str
    engine_version: str
    inputs: _Inputs
    outputs: _Outputs

    def as_json(self) -> dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "engine_version": self.engine_version,
            "inputs": self.inputs.model_dump(mode="json"),
            "outputs": self.outputs.model_dump(mode="json"),
        }


def compute(
    formula_id: str, inputs: dict[str, Any], forecast: ForecastInput | None = None
) -> FormulaResult:
    """Run one formula on raw inputs (as a tool call sends them). Raises FormulaError for an
    unknown formula, invalid inputs or a window_impact call without a forecast."""
    formula = FORMULAS.get(formula_id)
    if formula is None:
        raise FormulaError(f"unknown formula {formula_id!r}; known: {', '.join(FORMULAS)}")
    try:
        parsed = formula.inputs.model_validate(inputs)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'inputs'}: {e['msg']}" for e in exc.errors()
        )
        raise FormulaError(f"{formula_id}: {problems}") from exc
    if formula.needs_forecast:
        if forecast is None:
            raise FormulaError(f"{formula_id} needs the current forecast")
        outputs = formula.run(parsed, forecast)
    else:
        outputs = formula.run(parsed)
    return FormulaResult(formula.id, ENGINE_VERSION, parsed, outputs)
