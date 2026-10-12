"""Download historical BIST prices from Is Yatirim into the project database."""

import logging
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

from paths import DATABASE_PATH

logger = logging.getLogger(__name__)

ISYATIRIM_PRICE_URL = (
    "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
)
PRICE_COLUMNS = {
    "HGDG_HS_KODU": "symbol",
    "HGDG_TARIH": "date",
    "HGDG_KAPANIS": "close_price",
    "HGDG_AOF": "weighted_average_price",
    "HGDG_MIN": "low_price",
    "HGDG_MAX": "high_price",
    "HGDG_HACIM": "volume",
    "DD_DOVIZ_KODU": "currency_code",
    "DD_DEGER": "usd_try",
    "DOLAR_BAZLI_FIYAT": "close_price_usd",
    "ENDEKS_BAZLI_FIYAT": "index_based_price",
    "DOLAR_HACIM": "volume_usd",
    "SERMAYE": "capital",
    "PD": "market_value",
    "PD_USD": "market_value_usd",
    "HG_KAPANIS": "at_the_date_close_price",
    "HG_AOF": "at_the_date_weighted_average_price",
    "HG_MIN": "at_the_date_low_price",
    "HG_MAX": "at_the_date_high_price",
    "HAO_PD": "free_float_market_value",
    "HAO_PD_USD": "free_float_market_value_usd",
    "DOLAR_BAZLI_MIN": "low_price_usd",
    "DOLAR_BAZLI_MAX": "high_price_usd",
    "DOLAR_BAZLI_AOF": "weighted_average_price_usd",
}
PRICE_INFLATION_COLUMNS = {
    "close_price_adj_inflation": "REAL",
    "price_inflation_multiplier": "REAL",
}


def parse_price_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for separator in ("-", "."):
        parts = text.split(separator)
        if len(parts) == 3:
            try:
                day, month, year = (int(part) for part in parts)
                return date(year, month, day)
            except ValueError:
                continue
    raise ValueError(f"Unsupported date format: {value}. Use YYYY-MM-DD or DD-MM-YYYY.")


def format_isyatirim_date(value):
    return parse_price_date(value).strftime("%d-%m-%Y")


def fetch_symbol_prices(
    symbol,
    begin_date,
    end_date,
    session=None,
    timeout=30,
    rate_limit_delay=120,
    max_attempts=5,
):
    session = session or requests.Session()
    params = {
        "hisse": symbol,
        "startdate": format_isyatirim_date(begin_date),
        "enddate": format_isyatirim_date(end_date),
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Tarihsel-Fiyat-Bilgileri.aspx",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }

    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(
                ISYATIRIM_PRICE_URL, params=params, headers=headers, timeout=timeout
            )
            if response.status_code == 429:
                if attempt == max_attempts:
                    response.raise_for_status()
                logger.warning(
                    "Is Yatirim rate-limited %s; waiting %.0f seconds (%s/%s)",
                    symbol, rate_limit_delay, attempt, max_attempts,
                )
                time.sleep(rate_limit_delay)
                continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok", False):
                raise ValueError(f"Is Yatirim returned an error for {symbol}: {payload}")
            return payload.get("value", [])
        except (requests.Timeout, requests.ConnectionError):
            if attempt == max_attempts:
                raise
            delay = min(10 * attempt, 60)
            logger.warning(
                "Temporary connection error for %s; retrying in %s seconds (%s/%s)",
                symbol, delay, attempt, max_attempts,
            )
            time.sleep(delay)
    return []


def prices_to_frame(rows):
    if not rows:
        return pd.DataFrame(columns=list(PRICE_COLUMNS.values()))
    frame = pd.DataFrame(rows)
    for column in PRICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[list(PRICE_COLUMNS)].rename(columns=PRICE_COLUMNS)
    frame["date"] = pd.to_datetime(
        frame["date"], format="%d-%m-%Y", errors="raise"
    ).dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    return frame.drop_duplicates(["symbol", "date"], keep="last")


def ensure_prices_table(conn):
    schema = {"symbol": "TEXT NOT NULL", "date": "TEXT NOT NULL"}
    schema.update(
        {
            name: ("TEXT" if name == "currency_code" else "REAL")
            for name in PRICE_COLUMNS.values()
            if name not in {"symbol", "date"}
        }
    )
    columns = ",\n".join(f"{name} {kind}" for name, kind in schema.items())
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS prices ({columns}, PRIMARY KEY (symbol, date))"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(prices)")}
    for column, kind in PRICE_INFLATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE prices ADD COLUMN {column} {kind}")


def build_price_inflation_multipliers(conn, year, month):
    year, month = int(year), int(month)
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    inflation = pd.read_sql_query(
        "SELECT year, month, multiplier FROM inflation_table ORDER BY year, month",
        conn,
    )
    if inflation.empty:
        raise ValueError("inflation_table is empty")
    inflation["period"] = inflation["year"].astype(int) * 12 + inflation["month"].astype(int)
    if inflation["period"].duplicated().any():
        raise ValueError("inflation_table contains duplicate year/month rows")
    inflation = inflation.sort_values("period").reset_index(drop=True)
    rates = inflation["multiplier"].astype(float)
    if rates.isna().any() or (rates <= 0).any():
        raise ValueError("inflation_table multipliers must be present and positive")
    inflation["price_index"] = rates.cumprod()
    index_by_period = inflation.set_index("period")["price_index"]
    target_period = year * 12 + month
    if target_period not in index_by_period.index:
        raise ValueError(f"inflation_table has no data for target month {year}-{month:02d}")
    available_periods = set(index_by_period.index)
    target_index = float(index_by_period.loc[target_period])
    inflation["price_inflation_multiplier"] = inflation["period"].map(
        lambda period: (
            target_index / float(index_by_period.loc[period])
            if set(range(min(period, target_period), max(period, target_period) + 1))
            .issubset(available_periods)
            else float("nan")
        )
    )
    return inflation[["year", "month", "price_inflation_multiplier"]]


def adjust_prices_for_inflation(conn, year, month, symbols=None):
    ensure_prices_table(conn)
    multipliers = build_price_inflation_multipliers(conn, year, month)
    clause, params = "", []
    if symbols:
        normalized = [symbol.upper() for symbol in symbols]
        clause = f"WHERE symbol IN ({','.join('?' for _ in normalized)})"
        params = normalized
    prices = pd.read_sql_query(
        f"SELECT rowid, symbol, date, close_price FROM prices {clause}", conn, params=params
    )
    if prices.empty:
        logger.info("No price rows found to inflation-adjust")
        return 0
    dates = pd.to_datetime(prices["date"], errors="coerce")
    prices["year"], prices["month"] = dates.dt.year, dates.dt.month
    adjusted = prices.merge(multipliers, on=["year", "month"], how="left")
    adjusted["real_price"] = adjusted["close_price"] * adjusted["price_inflation_multiplier"]
    conn.executemany(
        "UPDATE prices SET close_price_adj_inflation = ?, price_inflation_multiplier = ? WHERE rowid = ?",
        [
            (row.real_price, row.price_inflation_multiplier, int(row.rowid))
            for row in adjusted.itertuples(index=False)
        ],
    )
    conn.commit()
    missing = int(adjusted["price_inflation_multiplier"].isna().sum())
    if missing:
        logger.warning("%s price rows have no matching monthly inflation multiplier", missing)
    logger.info("Adjusted %s prices to %s-%02d price level", len(adjusted), year, month)
    return len(adjusted)


def adjust_price_table_for_inflation(year, month, db_path=DATABASE_PATH, symbols=None):
    with sqlite3.connect(Path(db_path), timeout=30) as conn:
        return adjust_prices_for_inflation(conn, year, month, symbols)


def replace_price_rows(conn, frame, symbols, begin_date, end_date):
    ensure_prices_table(conn)
    normalized = [symbol.upper() for symbol in symbols]
    if not normalized:
        return
    begin = parse_price_date(begin_date).isoformat()
    end = parse_price_date(end_date).isoformat()
    if begin > end:
        raise ValueError("begin_date must not be later than end_date")
    placeholders = ",".join("?" for _ in normalized)
    conn.execute(
        f"DELETE FROM prices WHERE symbol IN ({placeholders}) AND date BETWEEN ? AND ?",
        [*normalized, begin, end],
    )
    if not frame.empty:
        frame.to_sql("prices", conn, if_exists="append", index=False)
    conn.commit()


def update_prices(
    symbols, begin_date, end_date, db_path=DATABASE_PATH, sleep_seconds=1.5,
    rate_limit_delay=120,
):
    symbols = [symbol.upper() for symbol in symbols]
    if not symbols:
        raise ValueError("At least one company symbol is required")
    if parse_price_date(begin_date) > parse_price_date(end_date):
        raise ValueError("begin_date must not be later than end_date")
    session = requests.Session()
    frames, failed = [], []
    with sqlite3.connect(Path(db_path), timeout=30) as conn:
        ensure_prices_table(conn)
        conn.commit()
        for index, symbol in enumerate(symbols, start=1):
            logger.info("[%s/%s] Fetching prices for %s", index, len(symbols), symbol)
            try:
                rows = fetch_symbol_prices(
                    symbol, begin_date, end_date, session=session,
                    rate_limit_delay=rate_limit_delay,
                )
                frame = prices_to_frame(rows)
                replace_price_rows(conn, frame, [symbol], begin_date, end_date)
                frames.append(frame)
                logger.info("Saved %s rows for %s", len(frame), symbol)
            except Exception:
                failed.append(symbol)
                logger.exception("Failed to fetch or save prices for %s", symbol)
            if sleep_seconds and index < len(symbols):
                time.sleep(sleep_seconds)
    if failed:
        logger.warning("Failed symbols: %s", ", ".join(failed))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(PRICE_COLUMNS.values()))
