from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from api.money import Money, convert, format_eur, parse_german_amount


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("-4166,67", -416667),
        ("275,35", 27535),
        ("-3200,00", -320000),
        ("0,05", 5),
        ("1.234,56", 123456),
        ("1,5", 150),
        ("275", 27500),
        (" -650,00 ", -65000),
    ],
)
def test_parse_german_amount(text: str, cents: int) -> None:
    assert parse_german_amount(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "12.5", "1,234", "1.23,45", "--5,00", "5,"])
def test_parse_german_amount_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_german_amount(text)


def test_convert_chf_rounds_half_up() -> None:
    assert convert(416667, "1.047") == 436250
    assert convert(416667, Decimal("1.040")) == 433334
    assert convert(-416667, "1.045") == -435417


def test_convert_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        convert(100, "abc")


@pytest.mark.parametrize(
    ("cents", "text"),
    [(125050, "1 250,50 €"), (-2276082, "-22 760,82 €"), (5, "0,05 €"), (0, "0,00 €")],
)
def test_format_eur(cents: int, text: str) -> None:
    assert format_eur(cents) == text


class Amount(BaseModel):
    amount: Money


def test_money_serializes_as_string() -> None:
    assert Amount(amount=125050).model_dump(mode="json") == {"amount": "125050"}
    assert Amount(amount=-2276082).model_dump_json() == '{"amount":"-2276082"}'


def test_money_accepts_digit_strings_and_ints() -> None:
    assert Amount.model_validate({"amount": "-500000"}).amount == -500000
    assert Amount.model_validate_json('{"amount": "125050"}').amount == 125050
    assert Amount(amount=42).amount == 42
    assert Amount.model_validate({"amount": "-999999999999999"}).amount == -(10**15 - 1)


@pytest.mark.parametrize("value", ["1000000000000000", "-99999999999999999999", 10**15, -(10**15)])
def test_money_rejects_amounts_beyond_15_digits(value: object) -> None:
    with pytest.raises(ValidationError):
        Amount.model_validate({"amount": value})


@pytest.mark.parametrize("value", [12.5, "12.50", "1,5", "1e3", "", " 5", True])
def test_money_rejects_floats_and_invalid_strings(value: object) -> None:
    with pytest.raises(ValidationError):
        Amount.model_validate({"amount": value})


def test_money_json_schema_is_a_string() -> None:
    schema = Amount.model_json_schema()["properties"]["amount"]
    assert schema["type"] == "string"
    assert schema["pattern"] == "^-?[0-9]{1,15}$"
