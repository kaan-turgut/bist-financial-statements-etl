"""Price payload regressions, including PostgreSQL's strict JSON validation."""

import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from services import postgres_price_pipeline as prices


@pytest.mark.parametrize(
    "value",
    [
        None,
        float("nan"),
        float("inf"),
        -float("inf"),
        pd.NA,
        pd.NaT,
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_missing_and_nonfinite_values_become_json_null(value):
    payload = prices._json_safe_value({"optional": value, "nested": [{"value": value}]})
    assert json.loads(json.dumps(payload, allow_nan=False)) == {
        "optional": None,
        "nested": [{"value": None}],
    }


def test_payload_preserves_finite_values_zero_booleans_and_text():
    original = {
        "price": 3.25,
        "volume": 100,
        "zero": 0,
        "flag": False,
        "note": "NaN",
        "date": date(2026, 2, 6),
        "missing": None,
    }
    payload = prices._json_safe_value(original)
    assert payload == original
    assert original["missing"] is None
    assert (
        json.loads(json.dumps(payload, default=str, allow_nan=False))["date"]
        == "2026-02-06"
    )


def test_partially_populated_optional_fields_load_and_rerun_without_duplicates(
    database, legacy_database, monkeypatch
):
    conn, _, _ = database
    rows = [
        {
            "HGDG_HS_KODU": "TEST",
            "HGDG_TARIH": "03-09-2019",
            "HGDG_KAPANIS": 10,
            "HAO_PD": 1000,
            "HAO_PD_USD": 100,
            "HGDG_HACIM": 0,
        },
        {
            "HGDG_HS_KODU": "TEST",
            "HGDG_TARIH": "06-02-2026",
            "HGDG_KAPANIS": None,
            "HAO_PD_USD": float("inf"),
            "HGDG_MAX": -float("inf"),
        },
    ]
    monkeypatch.setattr(prices, "connect_metadata_db", legacy_database)
    monkeypatch.setattr(prices, "fetch_symbol_prices", lambda *args, **kwargs: rows)
    for _ in range(2):
        run = prices.update_prices_postgres(
            ["TEST"], "2017-01-01", "2026-11-06", sleep_seconds=0
        )
        assert (
            conn.execute(
                "SELECT status FROM ops.pipeline_runs WHERE run_id=%s", (run,)
            ).fetchone()["status"]
            == "succeeded"
        )
    bronze = conn.execute(
        "SELECT * FROM bronze.price_observations ORDER BY price_date"
    ).fetchall()
    silver = conn.execute(
        "SELECT * FROM silver.daily_prices ORDER BY price_date"
    ).fetchall()
    assert len(bronze) == len(silver) == 2
    assert bronze[0]["source_payload"]["free_float_market_value"] == 1000
    assert bronze[0]["source_payload"]["volume"] == silver[0]["volume"] == 0
    payload = bronze[1]["source_payload"]
    assert (
        payload["free_float_market_value"] is None
    )  # NaN introduced by pandas for the missing field.
    assert payload["free_float_market_value_usd"] is None  # Explicit positive infinity.
    assert payload["high_price"] is None  # Explicit negative infinity.
    assert payload["close_price"] is None
    assert silver[1]["close"] is None and silver[1]["high"] is None
