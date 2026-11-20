from datetime import date

import pytest

from api.dates import days, parse_german_date, window_end


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("31.12.25", date(2025, 12, 31)),
        ("01.01.25", date(2025, 1, 1)),
        ("05.05.2025", date(2025, 5, 5)),
        (" 15.06.25 ", date(2025, 6, 15)),
    ],
)
def test_parse_german_date(text: str, expected: date) -> None:
    assert parse_german_date(text) == expected


@pytest.mark.parametrize("text", ["", "2025-05-05", "31.13.25", "32.01.25", "1.1"])
def test_parse_german_date_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_german_date(text)


def test_forecast_window_is_91_calendar_days() -> None:
    window = list(days(date(2025, 5, 5), 91))

    assert len(window) == 91
    assert window[0] == date(2025, 5, 5)
    assert window[-1] == date(2025, 8, 3) == window_end(date(2025, 5, 5), 91)
    assert date(2025, 6, 1) in window  # Sundays included
