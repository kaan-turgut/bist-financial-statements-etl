import sqlite3

import pytest

import services.price_fetcher as prices


def price_row(symbol, day, close):
    return {
        "HGDG_HS_KODU": symbol,
        "HGDG_TARIH": day,
        "HGDG_KAPANIS": close,
    }


def test_prices_to_frame_normalizes_dates_and_deduplicates():
    frame = prices.prices_to_frame(
        [
            price_row("thyao", "01-01-2025", 10),
            price_row("THYAO", "01-01-2025", 11),
        ]
    )

    assert len(frame) == 1
    assert frame.iloc[0]["symbol"] == "THYAO"
    assert frame.iloc[0]["date"] == "2025-01-01"
    assert frame.iloc[0]["close_price"] == 11


def test_update_prices_replaces_requested_symbol_and_date_range(monkeypatch, tmp_path):
    database = tmp_path / "prices.db"
    calls = iter(
        [
            [price_row("THYAO", "02-01-2025", 10)],
            [price_row("THYAO", "02-01-2025", 12)],
        ]
    )
    monkeypatch.setattr(prices, "fetch_symbol_prices", lambda *args, **kwargs: next(calls))

    for _ in range(2):
        prices.update_prices(
            ["THYAO"], "2025-01-01", "2025-01-31", db_path=database, sleep_seconds=0
        )

    with sqlite3.connect(database) as conn:
        rows = conn.execute("SELECT symbol, date, close_price FROM prices").fetchall()
    assert rows == [("THYAO", "2025-01-02", 12)]


def test_price_inflation_multipliers_use_target_cpi_index():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE inflation_table (year INTEGER, month INTEGER, multiplier REAL)")
        conn.executemany(
            "INSERT INTO inflation_table VALUES (?, ?, ?)",
            [(2026, 7, 1.02), (2026, 8, 1.03), (2026, 9, 1.04)],
        )

        result = prices.build_price_inflation_multipliers(conn, 2026, 9)

    factors = {(row.year, row.month): row.price_inflation_multiplier for row in result.itertuples()}
    assert factors[(2026, 9)] == 1
    assert factors[(2026, 8)] == pytest.approx(1.04)
    assert factors[(2026, 7)] == pytest.approx(1.04 * 1.03)
