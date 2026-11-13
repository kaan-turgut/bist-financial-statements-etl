from datetime import date
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from models.earnings_per_share import normalize_observation
from models.financial_values import CpiSeries
from services.earnings_per_share import calculate_eps_ratios, store_eps_source
from services.eps_inflation import _source_date, adjust_eps, publish_eps_inflation_ratio


def cpi():
    return CpiSeries(
        [
            {"observation_date": date(2025, month, 1), "value": Decimal(rate)}
            for month, rate in ((5, 0), (6, 10), (7, 20))
        ]
    )


@pytest.mark.parametrize("value", [Decimal(10), Decimal(-10), Decimal(0)])
def test_eps_rebasing_preserves_losses_zero_and_supports_backward_targets(value):
    adjusted, evidence = adjust_eps(
        value, date(2025, 5, 31), date(2025, 7, 1), cpi(), "estimated"
    )
    assert adjusted == value * Decimal("1.32")
    assert evidence["quality"] == "estimated"
    assert Decimal(evidence["cpi_factor"]) == Decimal("1.32")
    backward, _ = adjust_eps(
        adjusted, date(2025, 7, 31), date(2025, 5, 1), cpi(), "estimated"
    )
    assert backward == value


def test_eps_rebasing_requires_source_dates_and_complete_cpi():
    value, evidence = adjust_eps(Decimal(2), None, date(2025, 7, 1), cpi(), "verified")
    assert (
        value is None
        and evidence["unavailable_reason"] == "missing_or_ambiguous_inflation_basis"
    )
    value, evidence = adjust_eps(
        Decimal(2), date(2025, 4, 30), date(2025, 7, 1), cpi(), "verified"
    )
    assert value is None and evidence["unavailable_reason"] == "incomplete_cpi"
    missing_june = CpiSeries(
        [{"observation_date": date(2025, month, 1), "value": 0} for month in (5, 7)]
    )
    assert (
        adjust_eps(
            Decimal(2), date(2025, 5, 31), date(2025, 7, 1), missing_june, "verified"
        )[0]
        is None
    )


def test_verified_ttm_starts_at_its_existing_cpi_base_and_never_borrows_estimate_dates():
    row = {
        "verified_ttm": Decimal(4),
        "verified_ytd": Decimal(2),
        "fiscal_quarter": 2,
        "calculation_details": {"purchasing_power_date_ttm": "2025-06-30"},
        "assessment": {
            "evidence": {
                "ytd": {"measurement_basis": "nominal", "period_end": "2025-05-31"}
            }
        },
    }
    source, _ = _source_date(row, "ttm", [])
    assert adjust_eps(Decimal(4), source, date(2025, 7, 1), cpi(), "verified")[
        0
    ] == Decimal("4.8")
    # The YTD value was verified, so an unrelated estimate cannot establish its basis.
    assert _source_date(row, "ytd", [])[0] is None


def test_gold_and_refresh_rebase_each_selected_eps_basis_without_changing_original(
    database,
):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,
         line_item_path,item,period_end,inflation_base_year,inflation_base_month)
        VALUES (%s,%s,2025,2,'income_statement','test','test','2025-05-31',2025,7)""",
        (run, company),
    )
    # Quarter and YTD have independent disclosed purchasing-power dates.
    observations = [
        normalize_observation(
            period_start=date(2025, 3, 1),
            period_end=date(2025, 5, 31),
            period_kind="quarter",
            reported_eps=2,
            purchasing_power_date=date(2025, 6, 30),
        ),
        normalize_observation(
            period_start=date(2024, 12, 1),
            period_end=date(2025, 5, 31),
            period_kind="ytd",
            reported_eps=5,
            purchasing_power_date=date(2025, 5, 31),
        ),
    ]
    store_eps_source(
        conn,
        company_id=company,
        year=2025,
        quarter=2,
        source_path="verified.json",
        source_hash="inflation",
        source_kind="json",
        payload={},
        observations=observations,
        run_id=run,
    )
    conn.execute(
        "UPDATE silver.monthly_macro_data SET value=10 WHERE year=2025 AND month=6"
    )
    conn.execute(
        "UPDATE silver.monthly_macro_data SET value=20 WHERE year=2025 AND month=7"
    )
    calculate_eps_ratios(conn, run)
    adjusted = conn.execute(
        "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps_inflation_adj'"
    ).fetchone()
    assert adjusted["value_quarter"] == Decimal("2.4")
    assert adjusted["value_ytd"] == Decimal("6.6")

    # TTM can select a capital estimate while quarter/YTD retain verified values.
    assessment = {
        "evidence": {
            "ttm": {
                "measurement_basis": "inflation_adjusted",
                "purchasing_power_date": "2025-05-31",
            }
        }
    }
    conn.execute(
        "UPDATE gold.eps_coverage SET assessment=%s,estimated_value_ttm=10",
        (Jsonb(assessment),),
    )
    conn.execute("UPDATE gold.financial_ratios SET value_ttm=10 WHERE ratio_code='eps'")
    original = conn.execute(
        "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'"
    ).fetchall()
    verified = conn.execute(
        "SELECT * FROM gold.eps_values ORDER BY eps_kind,earnings_scope"
    ).fetchall()
    publish_eps_inflation_ratio(conn, run)
    adjusted = conn.execute(
        "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps_inflation_adj'"
    ).fetchone()
    assert adjusted["value_ttm"] == Decimal("13.2")
    metadata = conn.execute(
        "SELECT inflation_adjustment FROM gold.eps_coverage"
    ).fetchone()["inflation_adjustment"]
    assert metadata["quarter"]["source_date"] == "2025-06-30"
    assert metadata["quarter"]["quality"] == "verified"
    assert metadata["ttm"]["quality"] == "estimated"
    assert Decimal(metadata["ttm"]["cpi_factor"]) == Decimal("1.32")
    assert (
        conn.execute(
            "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'"
        ).fetchall()
        == original
    )
    assert (
        conn.execute(
            "SELECT * FROM gold.eps_values ORDER BY eps_kind,earnings_scope"
        ).fetchall()
        == verified
    )

    # Missing CPI clears old adjusted results on a repeat run; the original survives.
    conn.execute("DELETE FROM silver.monthly_macro_data WHERE year=2025 AND month=6")
    publish_eps_inflation_ratio(conn, run)
    adjusted = conn.execute(
        "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps_inflation_adj'"
    ).fetchone()
    assert adjusted["value_ytd"] is None and adjusted["value_ttm"] is None
    assert (
        conn.execute(
            "SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'"
        ).fetchall()
        == original
    )


def test_nominal_estimate_uses_actual_fiscal_end_and_target_changes(database):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,line_item_path,item,
         value_quarter,value_ytd,period_start,period_end,inflation_base_year,inflation_base_month,
         measurement_basis,calculation_details)
        VALUES (%s,%s,2025,1,'income_statement',
          'distribution of profit (loss) for the period > ana ortaklık payları','ana ortaklık payları',
          10,10,'2025-03-01','2025-05-31',2025,7,'nominal','{"ytd_days":92}')""",
        (run, company),
    )
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,line_item_path,item,
         value_quarter,period_end,inflation_base_year,inflation_base_month)
        VALUES (%s,%s,2025,1,'balance_sheet','capital','ödenmiş sermaye',100,'2025-05-31',2025,7)""",
        (run, company),
    )
    conn.execute(
        "UPDATE silver.monthly_macro_data SET value=10 WHERE year=2025 AND month=6"
    )
    conn.execute(
        "UPDATE silver.monthly_macro_data SET value=20 WHERE year=2025 AND month=7"
    )
    calculate_eps_ratios(conn, run)
    assert conn.execute(
        "SELECT value_ytd FROM gold.financial_ratios WHERE ratio_code='eps_inflation_adj'"
    ).fetchone()["value_ytd"] == Decimal(".132")
    before = conn.execute(
        "SELECT inflation_adjustment FROM gold.eps_coverage"
    ).fetchone()
    assert publish_eps_inflation_ratio(conn, run, []) == 0
    assert (
        conn.execute("SELECT inflation_adjustment FROM gold.eps_coverage").fetchone()
        == before
    )
    conn.execute("UPDATE gold.financial_values SET inflation_base_month=6")
    publish_eps_inflation_ratio(conn, run)
    assert conn.execute(
        "SELECT value_ytd FROM gold.financial_ratios WHERE ratio_code='eps_inflation_adj'"
    ).fetchone()["value_ytd"] == Decimal(".11")
    conn.execute(
        "INSERT INTO ops.active_company_versions(dataset_name,company_id,run_id) VALUES ('financials',%s,%s)",
        (company, run),
    )
    assert (
        conn.execute(
            "SELECT inflation_adjustment FROM gold.current_eps_coverage"
        ).fetchone()["inflation_adjustment"]["inflation_base_month"]
        == 6
    )
