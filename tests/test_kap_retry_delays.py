"""KAP rate-limit defaults and isolation from price-provider retry settings."""

from types import SimpleNamespace

import requests

from pipeline import build_parser
from services import download_statements as statements
from services import price_fetcher as prices
from services import update_company_info as metadata


def response(status, *, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = b"no reports"
    return result


def test_statement_download_backs_off_from_300_seconds(monkeypatch, tmp_path):
    replies = iter([response(429), response(429), response(200)])
    waits = []
    monkeypatch.setattr(
        statements.requests, "get", lambda *args, **kwargs: next(replies)
    )
    monkeypatch.setattr(statements.time, "sleep", waits.append)
    # No response is written: the small success body represents an empty year.
    monkeypatch.setattr(statements, "FINANCIAL_STATEMENTS_DIR", tmp_path)
    statements.download_file(
        SimpleNamespace(symbol="TEST", oid="test"), 2026, request_sleep=0
    )
    assert waits == [300, 600]


def test_metadata_429_default_changes_without_changing_other_http_backoff(monkeypatch):
    replies = iter([response(429), response(503), response(200)])
    session = SimpleNamespace(get=lambda *args, **kwargs: next(replies))
    waits = []
    monkeypatch.setattr(metadata, "sleep", waits.append)
    assert metadata.fetch_html(session, "https://www.kap.org.tr/test") == "no reports"
    assert waits == [300, 10]


def test_metadata_keeps_honoring_retry_after(monkeypatch):
    replies = iter([response(429, headers={"Retry-After": "900"}), response(200)])
    waits = []
    monkeypatch.setattr(metadata, "sleep", waits.append)
    metadata.fetch_html(
        SimpleNamespace(get=lambda *args, **kwargs: next(replies)),
        "https://www.kap.org.tr/test",
    )
    assert waits == [900]


def test_kap_cli_defaults_and_price_defaults_remain_independent(monkeypatch):
    parser = build_parser()
    assert (
        parser.parse_args(
            ["download", "--begin-year", "2026", "--end-year", "2026"]
        ).rate_limit_delay
        == 300
    )
    assert (
        parser.parse_args(
            [
                "run",
                "--begin-year",
                "2026",
                "--begin-quarter",
                "1",
                "--end-year",
                "2026",
                "--end-quarter",
                "3",
            ]
        ).rate_limit_delay
        == 300
    )
    assert (
        parser.parse_args(
            ["update-prices", "--begin-date", "2026-01-01", "--end-date", "2026-11-06"]
        ).rate_limit_delay
        == 120
    )
    monkeypatch.setattr("sys.argv", ["update_company_info"])
    assert metadata.parse_args().rate_limit_backoff == 300
    last = response(200)
    last._content = b'{"ok":true,"value":[]}'
    replies = iter([response(429), last])
    waits = []
    monkeypatch.setattr(prices.time, "sleep", waits.append)
    prices.fetch_symbol_prices(
        "TEST",
        "2026-01-01",
        "2026-11-06",
        session=SimpleNamespace(get=lambda *args, **kwargs: next(replies)),
    )
    assert waits == [120]
