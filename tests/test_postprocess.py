import sqlite3

import pandas as pd

from database_processing.postprocess import decumulate, fix_cumulative_q4


def test_fix_cumulative_q4_turns_full_year_value_into_single_q4_value():
    frame = pd.DataFrame(
        [
            {"company": "ABC", "year": 2024, "quarter": 1, "item": "sales", "value": 10.0},
            {"company": "ABC", "year": 2024, "quarter": 2, "item": "sales", "value": 20.0},
            {"company": "ABC", "year": 2024, "quarter": 3, "item": "sales", "value": 30.0},
            {"company": "ABC", "year": 2024, "quarter": 1234, "item": "sales", "value": 100.0},
        ]
    )

    result = fix_cumulative_q4(frame)
    q4 = result[result["quarter"] == 4].iloc[0]

    assert q4["value"] == 40.0


def test_decumulate_works_before_inflation_columns_exist():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE income_statement (
            company TEXT,
            year INTEGER,
            quarter INTEGER,
            date TEXT,
            item TEXT,
            value REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO income_statement VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("ABC", 2024, 1, "01.01.2024 - 31.03.2024", "sales", 10.0),
            ("ABC", 2024, 2, "01.04.2024 - 30.06.2024", "sales", 20.0),
            ("ABC", 2024, 3, "01.07.2024 - 30.09.2024", "sales", 30.0),
            ("ABC", 2024, 1234, "01.01.2024 - 31.12.2024", "sales", 100.0),
        ],
    )

    decumulate(conn)

    q4_value = conn.execute(
        """
        SELECT value
        FROM income_statement
        WHERE company = 'ABC'
          AND year = 2024
          AND quarter = 4
          AND item = 'sales'
        """
    ).fetchone()[0]

    assert q4_value == 40.0
