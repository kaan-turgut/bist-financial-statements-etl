import sqlite3

import pandas as pd
import pytest

from database_processing.inflate import inflate


@pytest.fixture
def inflation_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE inflation_table (date TEXT, year INTEGER, month INTEGER, rate REAL, multiplier REAL, cumulative_multiplier REAL)"
    )
    conn.executemany(
        "INSERT INTO inflation_table VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2026-06", 2026, 6, 0.99, 1.0099, 1.0),
            ("2026-07", 2026, 7, 1.78, 1.0178, 1.0178),
        ],
    )
    conn.execute("CREATE TABLE usd_try_table (year INTEGER, month INTEGER, open REAL)")
    conn.executemany(
        "INSERT INTO usd_try_table VALUES (?, ?, ?)",
        [(2026, 6, 40.0)],
    )
    yield conn
    conn.close()


def test_inflate_uses_target_to_source_monthly_index_ratio(inflation_db):
    frame = pd.DataFrame(
        [
            {"company": "ABC", "year": 2026, "quarter": 2, "value": 100.0},
        ]
    )

    july_level = inflate(inflation_db, frame, year=2026, month=7)
    june_level = inflate(inflation_db, frame, year=2026, month=6)

    assert july_level.loc[0, "inflation_multiplier"] == pytest.approx(1.0178)
    assert july_level.loc[0, "value_adj_inflation"] == pytest.approx(101.78)
    assert june_level.loc[0, "inflation_multiplier"] == pytest.approx(1.0)


def test_inflate_leaves_rows_unadjusted_when_month_coverage_has_a_gap(inflation_db):
    inflation_db.execute("DELETE FROM inflation_table WHERE year=2026 AND month=6")
    frame = pd.DataFrame(
        [{"company": "ABC", "year": 2026, "quarter": 2, "value": 100.0}]
    )

    result = inflate(inflation_db, frame, year=2026, month=7)

    assert pd.isna(result.loc[0, "inflation_multiplier"])
    assert pd.isna(result.loc[0, "value_adj_inflation"])
