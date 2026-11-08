from types import SimpleNamespace

import pytest

import pipeline
from pipeline import build_parser, main


@pytest.mark.parametrize("command", ["run", "run-postgres", "update-database", "update-database-postgres"])
def test_parse_workers_defaults_overrides_and_validation(command):
    parser = build_parser()
    args = [command, "--begin-year", "2023", "--begin-quarter", "1",
            "--end-year", "2023", "--end-quarter", "4"]
    assert parser.parse_args(args).parse_workers == 2
    assert parser.parse_args(args + ["--parse-workers", "4"]).parse_workers == 4
    assert parser.parse_args(args + ["--parse-workers", "1"]).parse_workers == 1
    for invalid in ["0", "-1", "1.5"]:
        with pytest.raises(SystemExit) as error:
            parser.parse_args(args + ["--parse-workers", invalid])
        assert error.value.code == 2


def test_database_cli_forwards_parse_workers(monkeypatch):
    companies = [SimpleNamespace(symbol="TEST")]
    monkeypatch.setattr(pipeline, "resolve_companies", lambda symbols: companies)
    monkeypatch.setattr(pipeline, "resolve_postgres_inflation_period", lambda target: (2026, 9))
    calls = []

    def update(*args, **kwargs):
        calls.append((args, kwargs))
        return 3, 123

    monkeypatch.setattr(pipeline, "update_database_postgres", update)
    main(["update-database", "--begin-year", "2023", "--begin-quarter", "1",
          "--end-year", "2023", "--end-quarter", "4", "--parse-workers", "4"])
    assert calls == [((companies, 2023, 1, 2023, 4), {"price_level": (2026, 9), "parse_workers": 4})]


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            [
                "update-database",
                "--begin-year",
                "2027",
                "--begin-quarter",
                "1",
                "--end-year",
                "2026",
                "--end-quarter",
                "4",
            ],
            "begin year/quarter",
        ),
        (
            [
                "update-database",
                "--begin-year",
                "2024",
                "--begin-quarter",
                "1",
                "--end-year",
                "2024",
                "--end-quarter",
                "4",
                "--price-level-year",
                "2026",
            ],
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
    args = parser.parse_args(
        [
            "update-database-postgres",
            "--symbols",
            "THYAO",
            "--begin-year",
            "2025",
            "--begin-quarter",
            "1",
            "--end-year",
            "2025",
            "--end-quarter",
            "4",
        ]
    )
    assert args.command == "update-database-postgres"


@pytest.mark.parametrize("marker", ["ARSAN", "arsan"])
def test_download_resumes_exclusively_after_company_in_default_order(
    monkeypatch, marker
):
    companies = [
        SimpleNamespace(symbol=s) for s in ["ARCLK", "ARSAN", "ARTMS", "ASELS"]
    ]
    monkeypatch.setattr(pipeline, "resolve_companies", lambda symbols: companies)
    monkeypatch.setattr(
        pipeline, "resolve_symbols", lambda symbols: [s.upper() for s in symbols]
    )
    calls = []
    monkeypatch.setattr(
        pipeline, "download_statements", lambda *args: calls.append(args)
    )
    main(
        [
            "download",
            "--begin-year",
            "2026",
            "--end-year",
            "2026",
            "--start-after",
            marker,
        ]
    )
    assert len(calls) == 1
    selected, begin, end, wait, backoff = calls[0]
    assert [c.symbol for c in selected] == ["ARTMS", "ASELS"]
    assert (begin, end, wait, backoff) == (2026, 2026, 10, 300)


@pytest.mark.parametrize("marker", ["UNKNOWN", "ASELS"])
def test_download_resume_never_restarts_at_the_beginning_for_missing_or_last_marker(
    monkeypatch, marker
):
    companies = [SimpleNamespace(symbol=s) for s in ["ARSAN", "ASELS"]]
    monkeypatch.setattr(pipeline, "resolve_companies", lambda symbols: companies)
    monkeypatch.setattr(pipeline, "resolve_symbols", lambda symbols: symbols)
    calls = []
    monkeypatch.setattr(
        pipeline, "download_statements", lambda *args: calls.append(args)
    )
    args = [
        "download",
        "--begin-year",
        "2026",
        "--end-year",
        "2026",
        "--start-after",
        marker,
    ]
    if marker == "UNKNOWN":
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2
    else:
        main(args)
    assert calls == []
