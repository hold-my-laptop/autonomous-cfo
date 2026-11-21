import pytest

from api.config import get_settings
from api.domain.forecast import ForecastInput
from api.domain.proof.engine import ENGINE_VERSION, FORMULAS, FormulaError, compute
from api.state import build_demo_state

settings = get_settings()
needs_sample = pytest.mark.skipif(
    not settings.statement_path.exists(), reason="sample CSV not found"
)
RENT_WINDOW = {
    "outflow_ref_prefix": "RENT",
    "effective_from": "2025-06-01",
    "billed": "410400",
    "justified": "380000",
}


@pytest.fixture(scope="module")
def forecast_input() -> ForecastInput:
    return build_demo_state(settings).forecast_input()


def out(formula_id: str, inputs: dict, forecast: ForecastInput | None = None) -> dict:
    return compute(formula_id, inputs, forecast).as_json()["outputs"]


def vpi(current: int, base: int = 1176, threshold: int = 500) -> dict:
    return out(
        "vpi_threshold_indexation",
        {
            "base_amount": "380000",
            "base_index": base,
            "current_index": current,
            "threshold_bps": threshold,
        },
    )


def test_hero_rent_numbers() -> None:
    assert out("variance", {"billed": "410400", "previous": "380000"}) == {
        "delta": "30400",
        "bps": 800,
    }
    assert vpi(1212) == {"change_bps": 306, "adjustment_allowed": False, "justified": "380000"}
    disputed = out("variance", {"billed": "410400", "previous": "380000"})["delta"]
    assert out("annualize", {"monthly": disputed}) == {"yearly": "364800"}


def test_vpi_above_the_threshold_indexes_the_rent() -> None:
    assert vpi(1236) == {"change_bps": 510, "adjustment_allowed": True, "justified": "399388"}


def test_threshold_is_compared_before_rounding() -> None:
    below = vpi(2101, base=2001)
    assert (below["change_bps"], below["adjustment_allowed"]) == (500, False)
    exactly = vpi(2100, base=2000)
    assert (exactly["change_bps"], exactly["adjustment_allowed"]) == (500, True)
    falling = vpi(1100, base=1176)
    assert (falling["change_bps"], falling["adjustment_allowed"]) == (-646, True)
    assert falling["justified"] == "355442"


def test_basis_points_round_half_up() -> None:
    assert out("variance", {"billed": "8010", "previous": "8000"})["bps"] == 13
    assert out("variance", {"billed": "7999", "previous": "8000"})["bps"] == -1
    assert out("variance", {"billed": "380000", "previous": "380000"}) == {"delta": "0", "bps": 0}


def test_chf_tranche_converts_at_the_statement_rates() -> None:
    assert out("fx_conversion", {"amount": "416667", "rate": "1.038"}) == {"converted": "432500"}
    assert out("fx_conversion", {"amount": "416667", "rate": "1.040"}) == {"converted": "433334"}


def test_surcharge_rule_on_the_asimex_invoices() -> None:
    terms = {"surcharge": "2450", "below_net_goods": "60000", "valid_from": "2025-05-01"}
    assert out("surcharge_rule", {"net_goods": "55871", "date": "2025-05-03", **terms}) == {
        "applies": True,
        "surcharge": "2450",
    }
    assert out("surcharge_rule", {"net_goods": "64227", "date": "2025-04-26", **terms}) == {
        "applies": False,
        "surcharge": "0",
    }
    early = out("surcharge_rule", {"net_goods": "55871", "date": "2025-04-30", **terms})
    assert early == {"applies": False, "surcharge": "0"}
    at_limit = out("surcharge_rule", {"net_goods": "60000", "date": "2025-05-03", **terms})
    assert at_limit["applies"] is False


def test_price_list_check() -> None:
    listed = {"list_price": "9.40", "valid_from": "2025-04-01"}
    assert out("price_list_check", {"unit_price": "9.40", "date": "2025-04-28", **listed}) == {
        "list_valid_on_date": True,
        "matches": True,
        "difference": "0.00",
    }
    old = out("price_list_check", {"unit_price": "8.90", "date": "2025-04-28", **listed})
    assert (old["matches"], old["difference"]) == (False, "-0.50")
    before = out("price_list_check", {"unit_price": "9.40", "date": "2025-03-07", **listed})
    assert (before["list_valid_on_date"], before["matches"]) == (False, False)


@needs_sample
def test_rent_window_impact(forecast_input: ForecastInput) -> None:
    impact = out("window_impact", RENT_WINDOW, forecast_input)
    assert impact["payments"] == ["RENT-06", "RENT-07", "RENT-08"]
    assert (impact["outflows_billed"], impact["outflows_justified"]) == ("1231200", "1140000")
    assert impact["outflow_difference"] == "91200"
    assert impact["window_end"] == "2025-08-03"
    assert impact["lowest_justified"] == "-39630"
    assert impact["lowest_difference"] == "30400"


@needs_sample
def test_window_impact_at_the_indexed_rent(forecast_input: ForecastInput) -> None:
    impact = out("window_impact", RENT_WINDOW | {"billed": "399388"}, forecast_input)
    assert impact["outflow_difference"] == str(3 * 19388)


@needs_sample
def test_window_impact_without_matching_payments(forecast_input: ForecastInput) -> None:
    with pytest.raises(FormulaError, match="no RENT payment from 2025-09-01"):
        compute("window_impact", RENT_WINDOW | {"effective_from": "2025-09-01"}, forecast_input)


def test_window_impact_needs_the_forecast() -> None:
    with pytest.raises(FormulaError, match="needs the current forecast"):
        compute("window_impact", RENT_WINDOW)


@pytest.mark.parametrize(
    ("formula_id", "inputs", "message"),
    [
        ("vpi", {}, "unknown formula 'vpi'"),
        ("variance", {"billed": "410400"}, "previous: Field required"),
        ("variance", {"billed": "410400", "previous": "0"}, "previous: Input should be greater"),
        ("variance", {"billed": 4104.0, "previous": "380000"}, "billed: Value error"),
        ("variance", {"billed": "1", "previous": "1", "rate": "2"}, "rate: Extra inputs"),
        ("fx_conversion", {"amount": "416667", "rate": 1.04}, "rate: Value error"),
        (
            "vpi_threshold_indexation",
            {"base_amount": "380000", "base_index": 0, "current_index": 1212, "threshold_bps": 1},
            "base_index: Input should be greater",
        ),
    ],
)
def test_invalid_inputs_are_refused(formula_id: str, inputs: dict, message: str) -> None:
    with pytest.raises(FormulaError, match=message):
        compute(formula_id, inputs)


def test_every_formula_has_a_schema_and_the_engine_version() -> None:
    assert set(FORMULAS) == {
        "variance",
        "vpi_threshold_indexation",
        "fx_conversion",
        "price_list_check",
        "surcharge_rule",
        "annualize",
        "window_impact",
    }
    for formula in FORMULAS.values():
        schema = formula.input_schema()
        assert schema["type"] == "object" and schema["additionalProperties"] is False
    rate = FORMULAS["fx_conversion"].input_schema()["properties"]["rate"]
    assert rate["type"] == "string"
    result = compute("annualize", {"monthly": "30400"})
    assert result.as_json()["engine_version"] == ENGINE_VERSION
    assert result.outputs.yearly == 364800
