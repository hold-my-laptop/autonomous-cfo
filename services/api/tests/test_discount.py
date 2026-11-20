import pytest

from api.domain.discount import CheaperOption, compare, financing_cost, suggest_discount_bp

EUR = 100


def test_financing_cost_simple_interest():
    # 10 000 EUR for 60 days at 12 %: 10 000 * 0.12 * 60 / 365 = 197.26 EUR
    assert financing_cost(10_000 * EUR, 60, 1200) == 19_726
    assert financing_cost(10_000 * EUR, 60, 1200, fees=5_000) == 24_726
    assert financing_cost(10_000 * EUR, 0, 1200) == 0


def test_discount_cheaper_than_credit():
    r = compare(10_000 * EUR, 60, 1200, 150)
    assert r.discount_cost == 15_000 and r.financing_cost == 19_726
    assert r.saving == 4_726 and r.cheaper is CheaperOption.DISCOUNT
    assert r.break_even_bp == 197


def test_credit_cheaper_than_big_discount():
    r = compare(10_000 * EUR, 60, 1200, 300)
    assert r.saving == -10_274 and r.cheaper is CheaperOption.CREDIT


def test_suggest_discount_stays_below_break_even():
    assert suggest_discount_bp(10_000 * EUR, 60, 1200) == 175
    assert compare(10_000 * EUR, 60, 1200, 175).saving > 0
    assert suggest_discount_bp(10_000 * EUR, 2, 1200) is None  # 0.07 % is not worth an offer


def test_validation():
    with pytest.raises(ValueError):
        compare(0, 60, 1200, 100)
    with pytest.raises(ValueError):
        compare(100, 60, 1200, 20_000)
    with pytest.raises(ValueError):
        financing_cost(-1, 1, 1)
