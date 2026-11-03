import pytest

from services.postgres_reference_data import _parse_tcmb_rate


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("4.18", 4.18),
        ("-1.43", -1.43),
        ("4,18", 4.18),
        ("1.234,56", 1234.56),
    ],
)
def test_parse_tcmb_rate_handles_dot_and_comma_formats(source, expected):
    assert _parse_tcmb_rate(source) == pytest.approx(expected)
