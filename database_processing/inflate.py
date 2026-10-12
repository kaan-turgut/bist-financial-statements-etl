import pandas as pd

from database_processing.helpers import backup_sqlite


def inflate(conn, df, year, month):
    if df.empty:
        for column in ["value_adj_inflation", "value_usd", "inflation_multiplier", "usd_try"]:
            if column not in df.columns:
                df[column] = []
        return df

    calculated_columns = {"value_adj_inflation", "value_usd", "inflation_multiplier", "usd_try"}
    original_columns = [
        column for column in df.columns
        if column != "month" and column not in calculated_columns
    ]
    df = df[original_columns].copy()

    if not 1 <= int(month) <= 12:
        raise ValueError("month must be between 1 and 12")

    df_inflation = pd.read_sql_query(
        "SELECT year, month, multiplier FROM inflation_table ORDER BY year, month",
        conn,
    )
    df_inflation["period"] = df_inflation["year"].astype(int) * 12 + df_inflation["month"].astype(int)
    if df_inflation["period"].duplicated().any():
        raise ValueError("inflation_table contains duplicate year/month rows")

    df_inflation = df_inflation.sort_values("period").reset_index(drop=True)
    factors = df_inflation["multiplier"].astype(float)
    if factors.isna().any() or (factors <= 0).any():
        raise ValueError("inflation_table multipliers must be present and positive")

    df_inflation["price_index"] = factors.cumprod()
    price_index = df_inflation.set_index("period")["price_index"]
    available_periods = set(price_index.index)
    target_period = int(year) * 12 + int(month)

    inflation_by_period = {}
    for source_period in (df["year"].astype(int) * 12 + (df["quarter"].astype(int) * 3)).unique():
        source_period = int(source_period)
        low, high = sorted((source_period, target_period))
        if not set(range(low, high + 1)).issubset(available_periods):
            inflation_by_period[source_period] = float("nan")
            continue
        inflation_by_period[source_period] = (
            float(price_index.loc[target_period]) / float(price_index.loc[source_period])
        )

    cursor = conn.cursor()
    data_kur = cursor.execute("""
        SELECT * FROM usd_try_table
        """)

    # extract column names from cursor.description
    columns = [desc[0] for desc in cursor.description]
    # create dataframe with both data + column names
    df_kur = pd.DataFrame(data_kur.fetchall(), columns=columns)

    # Map each statement period to its quarter-end month.
    df["month"] = df["quarter"] * 3
    source_periods = df["year"].astype(int) * 12 + df["month"].astype(int)
    df["inflation_multiplier"] = source_periods.map(inflation_by_period)

    # Step 2: merge USD rates
    merged = df.merge(
        df_kur[["year", "month", "open"]],
        on=["year", "month"],
        how="left"
    )

    # adjust values
    merged["value_adj_inflation"] = merged["value"] * merged["inflation_multiplier"]
    merged["value_usd"] = merged["value"] / merged["open"]
    merged = merged.rename(columns={"open": "usd_try"})

    df = merged[
        original_columns
        + ["value_adj_inflation", "value_usd", "inflation_multiplier", "usd_try"]
    ].copy()

    return df
    

def inflate_all(conn, year, month, *, create_backup=True):
    # Call this function to compound all values to the year-month specified, and to obtain the usd value column.

    cursor = conn.cursor()

    data = cursor.execute("SELECT * FROM income_statement")
    columns = [desc[0] for desc in cursor.description]

    df_income_statements = pd.DataFrame(data.fetchall(), columns=columns)
    df_income_statements = df_income_statements.astype({
        "year": "int",
        "quarter": "int",
        "value": "float"
        })

    df_income_statements = inflate(conn, df_income_statements, year=year, month=month)

    data = cursor.execute("SELECT * FROM balance_sheet")
    columns = [desc[0] for desc in cursor.description]

    df_balance_sheets = pd.DataFrame(data.fetchall(), columns=columns)
    df_balance_sheets = df_balance_sheets.astype({
        "year": "int",
        "quarter": "int",
        "value": "float"
        })

    df_balance_sheets = inflate(conn, df_balance_sheets, year=year, month=month)

    data = cursor.execute("SELECT * FROM cash_flow")
    columns = [desc[0] for desc in cursor.description]

    df_cash_flow = pd.DataFrame(data.fetchall(), columns=columns)
    df_cash_flow = df_cash_flow.astype({
        "year": "int",
        "quarter": "int",
        "value": "float"
        })

    df_cash_flow = inflate(conn, df_cash_flow, year=year, month=month)


    data = cursor.execute("SELECT * FROM equity_change")
    columns = [desc[0] for desc in cursor.description]

    df_equity_change = pd.DataFrame(data.fetchall(), columns=columns)
    df_equity_change = df_equity_change.astype({
        "year": "int",
        "quarter": "int",
        "value": "float"
        })

    df_equity_change = inflate(conn, df_equity_change, year=year, month=month)

    if create_backup:
        backup_sqlite(connection=conn)

    with conn:  # ensures commit at the end
        df_income_statements.to_sql("income_statement", conn, if_exists="replace", index=False)
        df_balance_sheets.to_sql("balance_sheet", conn, if_exists="replace", index=False)
        df_cash_flow.to_sql("cash_flow", conn, if_exists="replace", index=False)
        df_equity_change.to_sql("equity_change", conn, if_exists="replace", index=False)

    conn.commit()
