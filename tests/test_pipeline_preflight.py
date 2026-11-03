import pytest

from pipeline import build_parser, main


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            ["update-database", "--begin-year", "2027", "--begin-quarter", "1",
             "--end-year", "2026", "--end-quarter", "4"],
            "begin year/quarter",
        ),
        (
            ["update-database", "--begin-year", "2024", "--begin-quarter", "1",
             "--end-year", "2024", "--end-quarter", "4", "--price-level-year", "2026"],
            "provide both",
        ),
    ],
)
def test_cli_rejects_invalid_ranges_and_partial_price_level(arguments, message):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_postgres_command_names_remain_supported_as_aliases():
    parser = build_parser()
    args = parser.parse_args([
        "update-database-postgres", "--symbols", "THYAO",
        "--begin-year", "2025", "--begin-quarter", "1",
        "--end-year", "2025", "--end-quarter", "4",
    ])
    assert args.command == "update-database-postgres"
