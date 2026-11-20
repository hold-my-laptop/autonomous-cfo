from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from api.domain.forecast import ForecastInput, compute_forecast
from api.domain.importer import StatementImport
from api.domain.models import PlannedEvent, PlannedEventKind
from api.domain.montecarlo import collect_day_samples, credit_need, percentile, simulate

AS_OF = date(2025, 5, 5)


@pytest.fixture(scope="module")
def samples(sample_statement: StatementImport):
    history = [t for t in sample_statement.transactions if t.booking_date < AS_OF]
    return collect_day_samples(history, AS_OF, 8)


def june_closure(data: ForecastInput) -> ForecastInput:
    event = PlannedEvent(id=uuid4(), kind=PlannedEventKind.CLOSURE,
                         date_from=date(2025, 6, 1), date_to=date(2025, 6, 30))
    return replace(data, planned_events=(event,))


def test_samples_cover_every_weekday_with_eight_days(samples):
    assert {d: len(v) for d, v in samples.items()} == {d: 8 for d in range(7)}


def test_same_seed_same_result(demo_input, samples):
    a = simulate(demo_input, samples, runs=200, seed=7)
    b = simulate(demo_input, samples, runs=200, seed=7)
    assert (a.lowest_p10, a.lowest_p50, a.lowest_p90) == (b.lowest_p10, b.lowest_p50, b.lowest_p90)


def test_bands_are_ordered(demo_input, samples):
    r = simulate(june_closure(demo_input), samples, runs=300)
    assert r.lowest_p10 <= r.lowest_p50 <= r.lowest_p90
    assert all(d.p10 <= d.p50 <= d.p90 for d in r.days)
    assert len(r.days) == demo_input.horizon_days


def test_june_closure_makes_a_breach_very_likely(demo_input, samples):
    closed = simulate(june_closure(demo_input), samples, runs=500)
    assert closed.breach_probability_bp >= 9_000
    assert closed.lowest_p50 < demo_input.cash_floor


def test_median_is_close_to_the_deterministic_forecast(demo_input, samples):
    data = june_closure(demo_input)
    r = simulate(data, samples, runs=1000)
    det = compute_forecast(data).summary.lowest.closing
    assert abs(r.lowest_p50 - det) < 300_000  # within 3 000 €


def test_closed_days_have_no_spread_from_revenue(demo_input, samples):
    r = simulate(june_closure(demo_input), samples, runs=200)
    june_first = next(d for d in r.days if d.date == date(2025, 6, 1))
    june_second = next(d for d in r.days if d.date == date(2025, 6, 2))
    assert june_second.p90 - june_second.p10 == june_first.p90 - june_first.p10


def test_percentile_and_credit_need():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10) == 1
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert credit_need(-2_209_760, -500_000) == 2_000_000
    assert credit_need(-400_000, -500_000) == 0
    assert credit_need(-1_000_000, -500_000) == 500_000


def test_rejects_bad_input(demo_input, samples):
    with pytest.raises(ValueError):
        simulate(demo_input, samples, runs=0)
    with pytest.raises(ValueError):
        simulate(demo_input, {d: [] for d in range(7)})
