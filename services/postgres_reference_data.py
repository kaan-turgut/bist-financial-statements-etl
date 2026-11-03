"""Fetch and store monthly macro reference series in PostgreSQL."""

import hashlib
import json
from datetime import UTC, date, datetime

import requests
from bs4 import BeautifulSoup

from services.metadata_store import connect_metadata_db
from services.postgres_financial_pipeline import _run_finish, _run_start

TCMB_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/"
    "Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari"
)
FRANKFURTER_URL = "https://api.frankfurter.app/2010-01-01..{}?from=USD&to=TRY"


def _parse_tcmb_rate(value):
    """Parse TCMB numeric text with either dot or comma decimal separators."""
    text = str(value).strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def _upsert_series(conn, code, observations, source, run_id):
    for observation_date, value, payload in observations:
        payload_json = json.dumps(payload, default=str, sort_keys=True)
        source_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO bronze.macro_observations
               (series_code, observation_date, raw_value, source, source_hash, run_id, source_payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (series_code, observation_date, source, source_hash) DO NOTHING""",
            (code, observation_date, str(value), source, source_hash, run_id, payload_json),
        )
        conn.execute(
            """INSERT INTO silver.monthly_macro_data
               (series_code, year, month, observation_date, value, source)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (series_code, observation_date)
               DO UPDATE SET value=EXCLUDED.value, source=EXCLUDED.source, loaded_at=now()""",
            (code, observation_date.year, observation_date.month, observation_date,
             float(value), source),
        )


def update_inflation_postgres():
    response = requests.get(TCMB_URL, timeout=60)
    response.raise_for_status()
    table_body = BeautifulSoup(response.text, "html.parser").find("tbody")
    if table_body is None:
        raise ValueError("TCMB inflation page did not contain the expected monthly table.")
    records = []
    for tr in table_body.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        rate = _parse_tcmb_rate(cells[2])
        month, year = map(int, cells[0].split("-"))
        month_date = date(year, month, 1)
        records.append((month_date, rate, {"source_row": cells}))
    if not records:
        raise ValueError("TCMB inflation response contained no monthly observations.")
    with connect_metadata_db() as conn:
        run_id = _run_start(conn, "cpi_reference_ingestion", {"source": "TCMB"})
        conn.commit()
        try:
            _upsert_series(conn, "CPI_MONTHLY_RATE", records, "TCMB", run_id)
            _run_finish(conn, run_id)
        except Exception as exc:
            conn.rollback()
            _run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return len(records)


def update_exchange_postgres():
    end = datetime.now(UTC).date().isoformat()
    response = requests.get(FRANKFURTER_URL.format(end), timeout=90)
    response.raise_for_status()
    rates = response.json().get("rates", {})
    daily = sorted((datetime.fromisoformat(day).date(), item["TRY"]) for day, item in rates.items())
    if not daily:
        raise ValueError("Frankfurter returned no USD/TRY observations.")
    grouped = {}
    for day, rate in daily:
        grouped.setdefault((day.year, day.month), []).append((day, rate))
    open_rows, close_rows = [], []
    for (year, month), values in grouped.items():
        first_date, first_value = values[0]
        last_date, last_value = values[-1]
        open_rows.append((first_date, first_value, {"year": year, "month": month, "point": "open"}))
        close_rows.append((last_date, last_value, {"year": year, "month": month, "point": "close"}))
    with connect_metadata_db() as conn:
        run_id = _run_start(conn, "usdtry_reference_ingestion", {"source": "Frankfurter"})
        conn.commit()
        try:
            _upsert_series(conn, "USDTRY_MONTHLY_OPEN", open_rows, "Frankfurter", run_id)
            _upsert_series(conn, "USDTRY_MONTHLY_CLOSE", close_rows, "Frankfurter", run_id)
            _run_finish(conn, run_id)
        except Exception as exc:
            conn.rollback()
            _run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return len(open_rows)


def update_reference_data_postgres():
    return update_inflation_postgres(), update_exchange_postgres()
