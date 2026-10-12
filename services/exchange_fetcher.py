import sqlite3
from datetime import UTC, datetime

import pandas as pd
import requests

from paths import DATABASE_PATH


def get_exchange():

    # -----------------------------
    # Configuration
    # -----------------------------
    START = "2010-01-01"
    END = datetime.now(UTC).date().strftime("%Y-%m-%d")

    url = (
        f"https://api.frankfurter.app/{START}..{END}"
        "?from=USD&to=TRY"
    )

    # -----------------------------
    # Download data
    # -----------------------------
    response = requests.get(url)
    response.raise_for_status()

    data = response.json()["rates"]

    # -----------------------------
    # Convert to DataFrame
    # -----------------------------
    rows = []

    for rate_date, rate_dict in data.items():

        rows.append({

            "date": pd.to_datetime(rate_date),

            "USDTRY": rate_dict["TRY"]

        })

    df = pd.DataFrame(rows)

    df = df.sort_values("date")

    # -----------------------------
    # Monthly Open / Close
    # -----------------------------
    df["year_month"] = df["date"].dt.to_period("M")
    df["year_month"] = df["year_month"].astype(str)

    df_kur = (
        df.groupby("year_month")
        .agg(
            open_date=("date", "first"),
            open=("USDTRY", "first"),
            close_date=("date", "last"),
            close=("USDTRY", "last")
        )
        .reset_index()
    )

    df_kur["year"] = df_kur["open_date"].dt.year
    df_kur["month"] = df_kur["open_date"].dt.month

    with sqlite3.connect(DATABASE_PATH, timeout=30) as conn:  # waits up to 30s
        df_kur.to_sql("usd_try_table", con=conn, if_exists="replace", index=False)
