from datetime import date, timedelta
from uuid import uuid4

import pytest

from api.config import get_settings
from api.domain.importer import load_statement
from api.domain.models import Category, Transaction
from api.domain.revenue import MODEL_VERSION, build_revenue_model

AS_OF = date(2025, 5, 5)


def tx(day: date, amount: int, category: Category) -> Transaction:
    return Transaction(
        id=uuid4(),
        account="DE00",
        booking_date=day,
        value_date=day,
        type="TEST",
        counterparty="Someone",
        counterparty_account="",
        reference="",
        amount=amount,
        category=category,
    )


def test_window_is_the_full_weeks_before_as_of() -> None:
    model = build_revenue_model([], AS_OF, weeks=8)

    assert (model.window_from, model.window_to) == (date(2025, 3, 10), date(2025, 5, 4))
    assert model.window_from.weekday() == AS_OF.weekday()
    assert model.method == "WEEKDAY_AVERAGE"
    assert model.model_version == MODEL_VERSION
    assert [w.name for w in model.by_weekday] == ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def test_averages_revenue_and_costs_separately_per_weekday() -> None:
    mondays = [date(2025, 4, 28) - timedelta(weeks=i) for i in range(2)]
    items = [tx(d, 1000, Category.CARD_REVENUE) for d in mondays]
    items += [tx(d, -300, Category.VARIABLE_COST) for d in mondays]
    items.append(tx(date(2025, 5, 2), 700, Category.CARD_REVENUE))
    model = build_revenue_model(items, AS_OF, weeks=2)

    assert (model.by_weekday[0].revenue, model.by_weekday[0].variable_costs) == (1000, -300)
    assert (model.by_weekday[4].revenue, model.by_weekday[4].variable_costs) == (350, 0)
    assert model.expected(date(2025, 6, 2)) is model.by_weekday[0]


def test_floor_division_rounds_toward_minus_infinity() -> None:
    items = [
        tx(date(2025, 4, 28), 1001, Category.CARD_REVENUE),
        tx(date(2025, 4, 28), -1001, Category.VARIABLE_COST),
    ]
    monday = build_revenue_model(items, AS_OF, weeks=2).by_weekday[0]

    assert (monday.revenue, monday.variable_costs) == (500, -501)


def test_ignores_other_categories_and_days_outside_window() -> None:
    items = [
        tx(date(2025, 4, 28), -380000, Category.RENT),
        tx(date(2025, 5, 5), 99999, Category.CARD_REVENUE),
        tx(date(2025, 4, 20), 99999, Category.CARD_REVENUE),
    ]
    model = build_revenue_model(items, AS_OF, weeks=2)

    assert all(w.revenue == 0 and w.variable_costs == 0 for w in model.by_weekday)


def test_weeks_must_be_positive() -> None:
    with pytest.raises(ValueError):
        build_revenue_model([], AS_OF, weeks=0)


SAMPLE = get_settings().statement_path
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason=f"sample CSV not found at {SAMPLE}")


@needs_sample
def test_sample_matches_design_revenue_model() -> None:
    model = build_revenue_model(
        load_statement(SAMPLE).transactions, AS_OF, get_settings().revenue_model_weeks
    )

    assert [(w.name, w.revenue, w.variable_costs) for w in model.by_weekday] == [
        ("MON", 404472, -112471),
        ("TUE", 127778, -85206),
        ("WED", 25989, -24026),
        ("THU", 25704, -89696),
        ("FRI", 385297, -115877),
        ("SAT", 34974, -79097),
        ("SUN", 33850, -23189),
    ]


@needs_sample
def test_sample_june_closure_loses_expected_revenue() -> None:
    model = build_revenue_model(load_statement(SAMPLE).transactions, AS_OF, 8)
    june = [date(2025, 6, 1) + timedelta(days=i) for i in range(30)]

    assert sum(model.expected(d).revenue for d in june) == 4590578
    assert (
        sum(model.expected(d).revenue + model.expected(d).variable_costs for d in june) == 2336670
    )
