import sqlite3
from types import SimpleNamespace

import pytest

import services.update_database as database_service
from database_processing.helpers import resolve_inflation_period
from pipeline import main


def make_reference_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE inflation_table (year INTEGER, month INTEGER)")
        conn.executemany(
            "INSERT INTO inflation_table VALUES (?, ?)", [(2026, 8), (2026, 9)]
        )


def test_latest_inflation_period_is_used_and_explicit_period_is_checked(tmp_path):
    database = tmp_path / "bist_data.db"
    make_reference_database(database)

    assert resolve_inflation_period(db_path=database) == (2026, 9)
    assert resolve_inflation_period((2026, 8), database) == (2026, 8)
    with pytest.raises(ValueError, match="No inflation data"):
        resolve_inflation_period((2026, 10), database)


def test_clear_company_data_only_clears_periods_with_local_sources():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE balance_sheet (company TEXT, year INTEGER, quarter INTEGER, value REAL)"
    )
    conn.executemany(
        "INSERT INTO balance_sheet VALUES (?, ?, ?, ?)",
        [
            ("AAA", 2024, 1, 1),
            ("AAA", 2024, 2, 2),
            ("AAA", 2024, 4, 4),
            ("AAA", 2024, 1234, 40),
            ("BBB", 2024, 1, 10),
        ],
    )

    database_service.clear_company_data(
        conn,
        [SimpleNamespace(symbol="AAA")],
        2024,
        1,
        2024,
        4,
        periods_by_company={"AAA": {(2024, 1), (2024, 4)}},
    )

    rows = conn.execute(
        "SELECT company, quarter FROM balance_sheet ORDER BY company, quarter"
    ).fetchall()
    assert rows == [("AAA", 2), ("BBB", 1)]
    conn.close()


def test_archive_preflight_reports_and_returns_only_available_periods(monkeypatch, caplog):
    monkeypatch.setattr(
        database_service,
        "obtain_statement_paths",
        lambda *args: ["/tmp/AAA_2024.zip/AAA_123_2024_1.xls"],
    )

    paths, periods = database_service.preflight_statement_archives(
        [SimpleNamespace(symbol="AAA")], 2024, 1, 2024, 2
    )

    assert periods == {"AAA": {(2024, 1)}}
    assert len(paths["AAA"]) == 1
    assert "AAA 2024 Q2" in caplog.text
    assert "will be preserved" in caplog.text


def test_archive_preflight_fails_when_no_local_files_exist(monkeypatch):
    monkeypatch.setattr(database_service, "obtain_statement_paths", lambda *args: [])

    with pytest.raises(FileNotFoundError, match="Nothing was changed"):
        database_service.preflight_statement_archives(
            [SimpleNamespace(symbol="AAA")], 2024, 1, 2024, 1
        )


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
