"""Fetch and normalize historical BIST prices from Is Yatirim."""

import logging
import time
from datetime import date, datetime

import pandas as pd
import requests

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
