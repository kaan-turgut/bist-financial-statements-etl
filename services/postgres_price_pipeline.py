"""PostgreSQL Bronze/Silver/Gold path for historical share prices."""

import hashlib
import json
import logging
import math
import time
from contextvars import ContextVar
from decimal import Decimal
from numbers import Real

import pandas as pd

from services.metadata_store import connect_metadata_db
from services.postgres_financial_pipeline import (
    _ensure_instruments,
    _mark_run_interrupted,
    _run_finish,
    _run_start,
)
from services.price_fetcher import (
    fetch_symbol_prices,
    parse_price_date,
    prices_to_frame,
)

logger = logging.getLogger(__name__)
_PRICE_ADJUST_RUN_ID = ContextVar("price_adjust_run_id", default=None)


def _number_or_none(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe_value(value):
    """Convert pandas missing scalars and non-finite numbers to JSON null."""
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if pd.isna(value):
        return None
    if isinstance(value, (Real, Decimal)) and not math.isfinite(value):
        return None
    return value


def update_prices_postgres(symbols, begin_date, end_date, *, sleep_seconds=1.5, rate_limit_delay=120):
    symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not symbols:
        raise ValueError("At least one traded symbol is required.")
    if parse_price_date(begin_date) > parse_price_date(end_date):
        raise ValueError("begin_date must not be later than end_date.")
    with connect_metadata_db() as conn:
        _ensure_instruments(conn)
        instrument_rows = conn.execute(
            "SELECT instrument_id,symbol FROM metadata.instruments WHERE symbol=ANY(%s)",
            (symbols,),
        ).fetchall()
        instruments = {row["symbol"]: row["instrument_id"] for row in instrument_rows}
        missing = sorted(set(symbols) - set(instruments))
        if missing:
            raise ValueError("No PostgreSQL instruments found for: " + ", ".join(missing))
        run_id = _run_start(conn, "price_ingestion", {
            "symbols": symbols, "begin_date": begin_date, "end_date": end_date,
        })
        conn.commit()
        failed = []
        try:
            for index, symbol in enumerate(symbols, start=1):
                logger.info("[%s/%s] Fetching prices for %s", index, len(symbols), symbol)
                try:
                    rows = fetch_symbol_prices(
                        symbol, begin_date, end_date, rate_limit_delay=rate_limit_delay
                    )
                    frame = prices_to_frame(rows)
                    instrument_id = instruments[symbol]
                    for _, record in frame.iterrows():
                        price_date = record["date"]
                        values = {
                            "close": record.get("close_price"),
                            "high": record.get("high_price"),
                            "low": record.get("low_price"),
                            "volume": record.get("volume"),
                            "capital": record.get("capital"),
                        }
                        cleaned = {name: _number_or_none(value) for name, value in values.items()}
                        payload = _json_safe_value(record.to_dict())
                        payload_json = json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True, allow_nan=False)
                        source_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                        conn.execute(
                            """INSERT INTO bronze.price_observations
                               (instrument_id,price_date,high,low,close,volume,capital,source,
                                source_hash,run_id,source_payload)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,'IsYatirim',%s,%s,%s::jsonb)
                               ON CONFLICT (instrument_id,price_date,source,source_hash) DO NOTHING""",
                            (instrument_id, price_date, cleaned["high"], cleaned["low"],
                             cleaned["close"], cleaned["volume"], cleaned["capital"], source_hash,
                             run_id, payload_json),
                        )
                        conn.execute(
                            """INSERT INTO silver.daily_prices
                               (instrument_id,price_date,high,low,close,volume,capital,source)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,'IsYatirim')
                               ON CONFLICT (instrument_id,price_date,source) DO UPDATE SET
                                 high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,
                                 volume=EXCLUDED.volume,capital=EXCLUDED.capital""",
                            (instrument_id, price_date, cleaned["high"], cleaned["low"],
                             cleaned["close"], cleaned["volume"], cleaned["capital"]),
                        )
                    conn.commit()
                    logger.info("Stored %s price rows for %s", len(frame), symbol)
                except Exception:
                    conn.rollback()
                    failed.append(symbol)
                    logger.exception("Price download/storage failed for %s", symbol)
                if sleep_seconds and index < len(symbols):
                    time.sleep(sleep_seconds)
            if failed:
                raise RuntimeError("Price ingestion failed for: " + ", ".join(failed))
            _run_finish(conn, run_id)
            conn.commit()
        except KeyboardInterrupt:
            conn.rollback()
            _mark_run_interrupted(run_id)
            raise
        except Exception as exc:
            conn.rollback()
            _run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return run_id


def _adjust_prices_postgres(year, month, symbols=None):
    year, month = int(year), int(month)
    if not 1 <= month <= 12:
        raise ValueError("Inflation target month must be between 1 and 12.")
    with connect_metadata_db() as conn:
        target = conn.execute(
            """SELECT 1 FROM silver.monthly_macro_data
               WHERE series_code='CPI_MONTHLY_RATE' AND year=%s AND month=%s""",
            (year, month),
        ).fetchone()
        if target is None:
            raise ValueError(f"No CPI data for target month {year}-{month:02d}.")
        symbol_ids = None
        if symbols:
            normalized_symbols = [symbol.upper() for symbol in symbols]
            rows = conn.execute("""SELECT instrument_id,symbol FROM metadata.instruments
                WHERE symbol=ANY(%s)""", (normalized_symbols,)).fetchall()
            missing = sorted(set(normalized_symbols) - {row["symbol"] for row in rows})
            if missing:
                raise ValueError("Unknown PostgreSQL instrument symbols: " + ", ".join(missing))
            symbol_ids = [row["instrument_id"] for row in rows]
        run_id = _run_start(conn, "gold_price_adjustment", {
            "price_level": [year, month], "symbols": symbols,
        })
        _PRICE_ADJUST_RUN_ID.set(run_id)
        conn.commit()
        filter_sql = " AND p.instrument_id=ANY(%s)" if symbol_ids is not None else ""
        conn.execute("""WITH cpi_index AS (
            SELECT observation_date,
                   EXP(SUM(LN(1 + value / 100.0)) OVER (ORDER BY observation_date)) AS price_index
            FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'
        ), target AS (
            SELECT make_date(%s,%s,1) AS base_date,
                   (SELECT price_index FROM cpi_index
                    WHERE observation_date=make_date(%s,%s,1)) AS price_index
        )
        INSERT INTO gold.price_series
          (calculation_run_id,instrument_id,price_date,close_nominal,close_real,
           inflation_base_year,inflation_base_month)
        SELECT %s,p.instrument_id,p.price_date,p.close,
               p.close*t.price_index/source.price_index,
               EXTRACT(YEAR FROM t.base_date)::integer,
               EXTRACT(MONTH FROM t.base_date)::smallint
        FROM silver.daily_prices p CROSS JOIN target t
        LEFT JOIN cpi_index source
          ON source.observation_date=make_date(EXTRACT(YEAR FROM p.price_date)::integer,
                                               EXTRACT(MONTH FROM p.price_date)::integer,1)
        WHERE p.close IS NOT NULL""" + filter_sql + """
        ON CONFLICT (calculation_run_id,instrument_id,price_date) DO UPDATE
        SET close_nominal=EXCLUDED.close_nominal,close_real=EXCLUDED.close_real,
            inflation_base_year=EXCLUDED.inflation_base_year,
            inflation_base_month=EXCLUDED.inflation_base_month,calculated_at=now()""",
        [year, month, year, month, run_id, *([symbol_ids] if symbol_ids is not None else [])])
        conn.execute("""DELETE FROM gold.price_series old
            USING gold.price_series current
            WHERE current.calculation_run_id=%s
              AND old.calculation_run_id<>%s
              AND old.instrument_id=current.instrument_id
              AND old.price_date=current.price_date""", (run_id, run_id))
        conn.execute("""INSERT INTO ops.active_instrument_versions (dataset_name,instrument_id,run_id)
            SELECT 'prices',instrument_id,%s FROM gold.price_series
            WHERE calculation_run_id=%s GROUP BY instrument_id
            ON CONFLICT (dataset_name,instrument_id) DO UPDATE
            SET run_id=EXCLUDED.run_id,activated_at=now()""", (run_id, run_id))
        conn.execute("""INSERT INTO ops.data_quality_results
            (run_id,check_name,severity,affected_rows,details)
            SELECT %s,'price_rows_missing_cpi','warning',COUNT(*),
                   jsonb_build_object('price_level_year',%s,'price_level_month',%s)
            FROM gold.price_series WHERE calculation_run_id=%s AND close_real IS NULL""",
            (run_id, year, month, run_id))
        _run_finish(conn, run_id)
    _PRICE_ADJUST_RUN_ID.set(None)
    return run_id


def adjust_prices_postgres(year, month, symbols=None):
    try:
        return _adjust_prices_postgres(year, month, symbols)
    except KeyboardInterrupt:
        run_id = _PRICE_ADJUST_RUN_ID.get()
        if run_id is not None:
            _mark_run_interrupted(run_id)
        _PRICE_ADJUST_RUN_ID.set(None)
        raise
    except Exception as exc:
        run_id = _PRICE_ADJUST_RUN_ID.get()
        if run_id is not None:
            with connect_metadata_db() as conn:
                _run_finish(conn, run_id, exc)
        _PRICE_ADJUST_RUN_ID.set(None)
        raise
