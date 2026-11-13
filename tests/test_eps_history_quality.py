"""Verified TTM recovery and preservation of direct quarterly disclosures."""

from datetime import date
from decimal import Decimal

import pytest

from models.earnings_per_share import EpsObservation, calculate_history
from models.financial_values import CpiSeries


def obs(start, end, profit, shares, **kwargs):
    return EpsObservation(
        period_start=date.fromisoformat(start),
        period_end=date.fromisoformat(end),
        period_kind=kwargs.pop("period_kind", "ytd"),
        attributable_profit=profit,
        weighted_average_shares=shares,
        share_basis_id=kwargs.pop("share_basis_id", "verified"),
        **kwargs,
    )


def bridge_history():
    return {
        (2023, 2): [obs("2023-01-01", "2023-06-30", 200, 100, source_id=1)],
        (2023, 4): [obs("2023-01-01", "2023-12-31", 600, 150, source_id=2)],
        (2024, 2): [obs("2024-01-01", "2024-06-30", 300, 200, source_id=3)],
    }


def result(history, cpi=None, period=(2024, 2)):
    return calculate_history(history, cpi)[(*period, "basic", "ordinary", "total")]


def test_ttm_bridge_recovers_without_individual_quarters_and_weights_share_days():
    row = result(bridge_history())
    shares = (Decimal(150) * 365 - Decimal(100) * 181 + Decimal(200) * 182) / 366
    assert row["value_ttm"] == Decimal(700) / shares
    assert row["value_quarter"] is None
    assert row["details"]["ttm_method"] == "verified_annual_ytd_bridge"
    assert row["details"]["source_ids"] == [1, 2, 3]
    assert row["details"]["ttm_unavailable_reason"] is None
    assert (
        row["value_ttm"] != Decimal(600) / 150 - Decimal(200) / 100 + Decimal(300) / 200
    )


@pytest.mark.parametrize(
    "problem",
    ["unknown_basis", "split", "missing_inputs", "date_gap", "bad_residual", "power"],
)
def test_bridge_rejects_unverified_or_inconsistent_inputs(problem):
    history = bridge_history()
    previous = history[(2023, 2)][0]
    changes = {
        "unknown_basis": {"share_basis_id": None},
        "split": {"share_basis_id": "split"},
        "missing_inputs": {"weighted_average_shares": None, "reported_eps": 2},
        "date_gap": {"period_start": date(2023, 1, 2)},
        "bad_residual": {"weighted_average_shares": 1000},
        "power": {"purchasing_power_date": date(2023, 6, 30)},
    }[problem]
    history[(2023, 2)] = [EpsObservation(**{**vars(previous), **changes})]
    assert result(history)["value_ttm"] is None


def test_bridge_cpi_converts_each_profit_to_current_power_and_requires_complete_cpi():
    history = bridge_history()
    for key, values in history.items():
        o = values[0]
        history[key] = [
            EpsObservation(**{**vars(o), "purchasing_power_date": o.period_end})
        ]
    cpi = CpiSeries(
        [
            {"observation_date": date(y, m, 1), "value": Decimal(1)}
            for y in (2023, 2024)
            for m in range(1, 13)
        ]
    )
    row = result(history, cpi)
    shares = (Decimal(150) * 365 - Decimal(100) * 181 + Decimal(200) * 182) / 366
    assert (
        row["value_ttm"]
        == (
            600 * cpi.factor(date(2023, 12, 31), date(2024, 6, 30))
            - 200 * cpi.factor(date(2023, 6, 30), date(2024, 6, 30))
            + 300
        )
        / shares
    )
    assert row["details"]["ttm_method"] == "verified_annual_ytd_bridge_cpi"
    del cpi.rates[2023 * 12 + 8]
    assert result(history, cpi)["value_ttm"] is None


def test_q1_quarter_only_disclosure_also_supplies_ytd_and_bridge():
    history = {
        (2023, 1): [obs("2023-01-01", "2023-03-31", 100, 100, period_kind="quarter")],
        (2023, 4): [obs("2023-01-01", "2023-12-31", 400, 100)],
        (2024, 1): [obs("2024-01-01", "2024-03-31", 200, 100, period_kind="quarter")],
    }
    row = result(history, period=(2024, 1))
    assert row["value_quarter"] == row["value_ytd"] == 2
    assert row["value_ttm"] == 5


def test_bridge_works_for_noncalendar_fiscal_year_and_negative_earnings():
    history = {
        (2023, 2): [obs("2023-04-01", "2023-09-30", 200, 100)],
        (2023, 4): [obs("2023-04-01", "2024-03-31", 100, 100)],
        (2024, 2): [obs("2024-04-01", "2024-09-30", -100, 100)],
    }
    assert result(history)["value_ttm"] == -2


def test_derivation_keeps_direct_quarter_inputs_even_if_eps_agrees():
    history = {
        (2024, 1): [obs("2024-01-01", "2024-03-31", 100, 100)],
        (2024, 2): [
            obs("2024-01-01", "2024-06-30", 200, 100),
            obs(
                "2024-04-01", "2024-06-30", 200, 200, period_kind="quarter", source_id=9
            ),
        ],
        (2024, 3): [obs("2024-07-01", "2024-09-30", 100, 100, period_kind="quarter")],
        (2024, 4): [obs("2024-10-01", "2024-12-31", 100, 100, period_kind="quarter")],
    }
    row = result(history, period=(2024, 4))
    shares = (Decimal(100) * 275 + Decimal(200) * 91) / 366
    assert row["value_ttm"] == Decimal(500) / shares
    assert 9 in row["details"]["source_ids"]


def test_enriched_reported_quarter_retains_all_three_source_ids():
    history = {
        (2024, 1): [obs("2024-01-01", "2024-03-31", 100, 100, source_id=1)],
        (2024, 2): [
            obs("2024-01-01", "2024-06-30", 200, 100, source_id=2),
            obs(
                "2024-04-01",
                "2024-06-30",
                None,
                None,
                period_kind="quarter",
                reported_eps=1,
                source_id=3,
            ),
        ],
    }
    assert result(history)["details"]["source_ids"] == [1, 2, 3]
    history[(2024, 3)] = [
        obs("2024-07-01", "2024-09-30", 100, 100, period_kind="quarter", source_id=4)
    ]
    history[(2024, 4)] = [
        obs("2024-10-01", "2024-12-31", 100, 100, period_kind="quarter", source_id=5)
    ]
    row = result(history, period=(2024, 4))
    assert row["value_ttm"] == 4
    assert row["details"]["source_ids"] == [1, 2, 3, 4, 5]


def test_pipeline_publishes_bridge_with_verified_quality(database):
    from services.earnings_per_share import calculate_eps_ratios, store_eps_source

    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,line_item_path,item)
        VALUES (%s,%s,2024,2,'income_statement','test profit','test profit')""",
        (run, company),
    )
    for (year, quarter), observations in bridge_history().items():
        store_eps_source(
            conn,
            company_id=company,
            year=year,
            quarter=quarter,
            source_path="verified.json",
            source_hash=f"{year}-{quarter}",
            source_kind="json",
            payload={},
            observations=observations,
            run_id=run,
        )
    calculate_eps_ratios(conn, run)
    row = conn.execute(
        """SELECT value_ttm,calculation_details FROM gold.eps_values
        WHERE eps_kind='basic' AND earnings_scope='total'"""
    ).fetchone()
    expected = result(bridge_history())["value_ttm"]
    assert abs(row["value_ttm"] - expected) < Decimal("1e-12")
    assert row["calculation_details"]["ttm_method"] == "verified_annual_ytd_bridge"
    assert len(row["calculation_details"]["ttm_bridge_source_ids"]) == 3
    coverage = conn.execute("SELECT assessment FROM gold.eps_coverage").fetchone()
    assert coverage["assessment"]["quality"]["ttm"]["status"] == "verified"


def test_annual_ttm_dependencies_exclude_previous_ytd_used_only_for_q4():
    history = {
        (2024, 3): [obs("2024-01-01", "2024-09-30", 300, 100, source_id=1)],
        (2024, 4): [obs("2024-01-01", "2024-12-31", 600, 100, source_id=2)],
    }
    row = result(history, period=(2024, 4))
    assert row["value_quarter"] == 3
    assert row["details"]["source_ids"] == [1, 2]
    assert row["details"]["ttm_source_ids"] == [2]


def test_four_quarter_ttm_retains_cpi_evidence_from_derived_quarter_inputs():
    from dataclasses import replace

    history = {
        (2023, 1): [obs("2023-01-01", "2023-03-31", 100, 100, source_id=1)],
        (2023, 2): [obs("2023-01-01", "2023-06-30", 200, 100, source_id=2)],
        (2023, 3): [
            obs(
                "2023-07-01", "2023-09-30", 100, 100, source_id=3, period_kind="quarter"
            )
        ],
        (2023, 4): [
            obs(
                "2023-10-01", "2023-12-31", 100, 100, source_id=4, period_kind="quarter"
            )
        ],
        (2024, 1): [obs("2024-01-01", "2024-03-31", 100, 100, source_id=5)],
    }
    for period, values in history.items():
        history[period] = [
            replace(o, purchasing_power_date=o.period_end) for o in values
        ]
    cpi = CpiSeries(
        [
            {"observation_date": date(y, m, 1), "value": Decimal(1)}
            for y in (2023, 2024)
            for m in range(1, 13)
        ]
    )
    row = result(history, cpi, period=(2024, 1))
    assert row["details"]["ttm_source_ids"] == [1, 2, 3, 4, 5]
    assert any(
        c["source_date"] == "2023-03-31" and c["target_date"] == "2023-06-30"
        for c in row["details"]["cpi_conversions"]
    )
