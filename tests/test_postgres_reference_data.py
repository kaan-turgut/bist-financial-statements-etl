from datetime import date

import pytest

from services.postgres_reference_data import _parse_tcmb_rate, _upsert_series


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


def test_monthly_series_keeps_one_row_when_partial_month_close_moves(database):
    conn, _, run_id = database
    _upsert_series(conn, "USDTRY_MONTHLY_CLOSE", [(date(2026, 11, 5), 50.047, {})], "test", run_id)
    _upsert_series(conn, "USDTRY_MONTHLY_CLOSE", [(date(2026, 11, 6), 50.153, {})], "test", run_id)
    rows = conn.execute(
        """SELECT observation_date,value FROM silver.monthly_macro_data
           WHERE series_code='USDTRY_MONTHLY_CLOSE' AND year=2026 AND month=11"""
    ).fetchall()
    assert [(r["observation_date"], float(r["value"])) for r in rows] == [(date(2026, 11, 6), 50.153)]
    # Bronze keeps both source observations.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM bronze.macro_observations WHERE series_code='USDTRY_MONTHLY_CLOSE'"
    ).fetchone()["n"] == 2
