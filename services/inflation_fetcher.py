import sqlite3

import pandas as pd
import requests
from bs4 import BeautifulSoup

from paths import DATABASE_PATH


def get_inflation():
    # Call this function to obtain monthly inflation rates from tcmb website and write them to sql table: inflation_table. Each run replaces the table
    session = requests.Session()
    tcmb_url = r"https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari"
    r = session.get(tcmb_url)

    soup = BeautifulSoup(r.text, "html.parser")
    #table = soup.find("table", summary = "Fiyat Endeksi (Tüketici Fiyatları)(2003=100)")
    table = soup.find("tbody")
    rows = table.find_all("tr")
    data = []
    for row in rows:
        cols = [td.text.strip() for td in row.find_all("td")]
        data.append(cols)

    df_inflation = pd.DataFrame(data, columns=["date", "dropit", "rate"])
    df_inflation = df_inflation.drop(columns=["dropit"])
    df_inflation["date"] = pd.to_datetime(df_inflation["date"], format="%m-%Y")
    df_inflation["year"] = df_inflation["date"].dt.year
    df_inflation["month"] = df_inflation["date"].dt.month
    df_inflation["date"] = df_inflation["date"].dt.strftime("%Y-%m")
    df_inflation = df_inflation[["date", "year", "month", "rate"]]
    df_inflation["rate"] = df_inflation["rate"].str.replace(",", ".").astype("float")
    df_inflation["multiplier"] = df_inflation["rate"]/100+1

    df_inflation["cumulative_multiplier"] = df_inflation["multiplier"].cumprod()

    with sqlite3.connect(DATABASE_PATH, timeout=30) as conn:  # waits up to 30s
        df_inflation.to_sql("inflation_table", con=conn, if_exists="replace", index=False)
