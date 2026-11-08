"""Source-basis arithmetic, real reporting dates, and published-data regressions."""

import calendar
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from models.financial_values import CpiSeries, calculate_values
from services import postgres_financial_pipeline as pipeline
from services import repair_financials
from services.financial_report_context import infer_context, store_context
from services.financial_values import calculate_value_changes


def fact(
    q,
    amount,
    *,
    start=None,
    end=None,
    cumulative=True,
    basis="inflation_adjusted",
    **kwargs,
):
    end = end or date(2024, q * 3, calendar.monthrange(2024, q * 3)[1])
    row = {
        "company_id": 1,
        "statement_type": "income_statement",
        "fiscal_year": 2024,
        "fiscal_quarter": q,
        "line_item_path": "profit or loss > hasılat",
        "item": "hasılat",
        "source_column_id": None,
        "source_row_id": "sales:1",
        "filing_key": q,
        "period_start": start or date(2024, 1 if cumulative else q * 3 - 2, 1),
        "period_end": end,
        "amount": Decimal(amount),
        "is_cumulative": cumulative,
        "measurement_basis": basis,
        "purchasing_power_date": end if basis == "inflation_adjusted" else None,
        "report_kind": "Konsolide",
    }
    row.update(kwargs)
    return row


def prices(rate=0):
    return CpiSeries(
        [
            {"observation_date": date(2024, m, 1), "value": Decimal(rate)}
            for m in range(1, 13)
        ]
    )


def values(rows, cpi=None, target=None):
    return calculate_values(
        rows, cpi or prices(), target or date(2024, 12, 1), pipeline.CASH_BALANCE_ITEMS
    )


def test_compatible_ytd_is_rebased_before_quarter_subtraction():
    result = values([fact(1, 100), fact(2, 300)], prices(10))[-1]
    assert result["value_ytd"] == 300
    assert result["value_quarter"] == Decimal(300) - 100 * Decimal("1.1") ** 3
    assert (
        result["calculation_details"]["quarter_method"]
        == "difference_of_compatible_ytd"
    )


def test_reported_ytd_and_quarter_take_priority_over_cross_report_sums():
    result = values([fact(1, 100), fact(2, 300), fact(2, 140, cumulative=False)])[-1]
    assert result["value_ytd"] == 300
    assert result["value_quarter"] == 140


@pytest.mark.parametrize(
    "left,right",
    [
        ("nominal", "inflation_adjusted"),
        ("unknown", "unknown"),
        ("inflation_adjusted", "unknown"),
    ],
)
def test_unverified_or_changed_basis_preserves_ytd_without_inventing_quarter(
    left, right
):
    result = values([fact(1, 100, basis=left), fact(2, 300, basis=right)])[-1]
    assert result["value_ytd"] == 300
    assert result["value_quarter"] is None


def test_nominal_ytd_is_not_inflated_before_nominal_quarter_subtraction():
    result = values(
        [fact(1, 100, basis="nominal"), fact(2, 300, basis="nominal")], prices(10)
    )[-1]
    assert result["value_quarter"] == 200


def test_missing_intermediate_cpi_prevents_a_false_conversion():
    cpi = CpiSeries(
        [{"observation_date": date(2024, m, 1), "value": 10} for m in (3, 6, 12)]
    )
    result = values([fact(1, 100), fact(2, 300)], cpi)[-1]
    assert result["value_quarter"] is None
    assert result["value_real"] is None


def test_reported_quarter_survives_unknown_basis_but_real_value_does_not():
    result = values([fact(2, 140, cumulative=False, basis="unknown")])[-1]
    assert result["value_quarter"] == 140
    assert result["value_real"] is None


def test_reported_ytd_start_must_match_the_previous_report():
    result = values([fact(1, 100), fact(2, 300, start=date(2024, 2, 1))])[-1]
    assert result["value_quarter"] is None


def test_different_consolidation_scopes_are_not_subtracted():
    result = values([fact(1, 100, report_kind="Konsolide Olmayan"), fact(2, 300)])[-1]
    assert result["value_quarter"] is None


def test_ytd_sum_converts_prior_quarters_to_current_report_basis():
    result = values(
        [fact(1, 100, cumulative=False), fact(2, 200, cumulative=False)], prices(10)
    )[-1]
    assert result["value_ytd"] == 100 * Decimal("1.1") ** 3 + 200


def test_noncalendar_cross_year_fiscal_period_uses_actual_month():
    result = values([fact(4, 400, start=date(2023, 4, 1), end=date(2024, 3, 31))])[-1]
    assert result["period_start"] == date(2024, 1, 1)
    assert result["period_end"] == date(2024, 3, 31)


def test_context_comes_from_report_headers_instead_of_a_year_cutoff():
    adjusted = [
        "30 Eylül 2024\n(Tutarlar Türk Lirasının 30 Eylül 2024 tarihindeki satın alma gücü cinsinden bin Türk Lirası olarak gösterilmiştir.)"
    ]
    assert (
        infer_context(adjusted, date(2024, 9, 30))["measurement_basis"]
        == "inflation_adjusted"
    )
    nominal = [
        "31 Aralık 2024\n(Tutarlar aksi belirtilmedikçe milyon Türk Lirası olarak gösterilmiştir.)"
    ]
    assert infer_context(nominal, date(2024, 12, 31))["measurement_basis"] == "nominal"
    assert infer_context(nominal, date(2024, 9, 30))["measurement_basis"] == "unknown"


def test_conflicting_purchasing_power_date_is_not_overridden_by_cover_date():
    pages = [
        "31 Aralık 2024",
        "(Tutarlar 30 Eylül 2024 tarihindeki satın alma gücü cinsinden bin Türk Lirası olarak gösterilmiştir.)",
    ]
    assert infer_context(pages, date(2024, 12, 31))["measurement_basis"] == "unknown"


def test_future_note_dates_do_not_change_the_presentation_header_basis():
    pages = [
        "30 Eylül 2024\n(Tutarlar 30 Eylül 2024 tarihi itibarıyla satın alma gücü cinsinden bin Türk Lirası olarak ifade edilmiştir.)\nYeni standartlar 1 Ocak 2027 tarihinde uygulanacaktır."
    ]
    assert (
        infer_context(pages, date(2024, 9, 30))["measurement_basis"]
        == "inflation_adjusted"
    )


def test_pdf_font_spacing_in_presentation_units_is_supported():
    pages = [
        "30 Eylül 2022\n(T utarlar aksi belirtilmedikçe bin T ürk Lirası (T L) olarak gösterilmiştir.)"
    ]
    assert infer_context(pages, date(2022, 9, 30))["measurement_basis"] == "nominal"


def test_full_year_report_provides_ttm_ratios_without_fabricating_q4(database):
    from services.financial_ratios import calculate_financial_ratios

    conn, company, run = database
    for item, amount in [("hasılat", 400), ("satışların maliyeti", -300)]:
        conn.execute(
            """INSERT INTO gold.financial_values
            (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,
             value_ytd,value_real_ytd,period_start,period_end,calculation_details)
            VALUES (%s,%s,'income_statement',2024,4,%s,%s,%s,%s,'2024-10-01','2024-12-31',
                    '{"ytd_method":"reported","ytd_days":366}'::jsonb)""",
            (run, company, "profit or loss > " + item, item, amount, amount),
        )
    calculate_financial_ratios(conn, run)
    margin = conn.execute(
        "SELECT * FROM gold.financial_ratios WHERE ratio_code='gross_margin'"
    ).fetchone()
    assert margin["value_ttm"] == Decimal("0.25")
    assert margin["value_quarter"] is None


def test_transition_keeps_reported_annual_real_ytd_without_a_quarter():
    result = values([fact(3, 300, basis="nominal"), fact(4, 500)])[-1]
    assert result["value_quarter"] is None
    assert result["value_real_ytd"] == 500


def test_cleaner_retains_quarter_ytd_and_cross_year_current_period():
    raw = []
    for start, end, amount in [
        ("01.04.2023", "31.03.2024", 400),
        ("01.01.2024", "31.03.2024", 100),
        ("01.04.2022", "31.03.2023", 300),
    ]:
        raw.append(
            dict(
                company="TEST",
                year=2024,
                quarter=4,
                date=f"{start} - {end}",
                table="Income Statement",
                item="hasılat",
                line_item_path="profit or loss > hasılat",
                value=amount,
                **{"reporting unit": 1},
            )
        )
    clean = pipeline._clean_frame(pd.DataFrame(raw))
    assert clean["value"].tolist() == [400, 100]
    assert clean["quarter"].tolist() == [4, 4]


def test_monthly_fx_matches_a_trading_date_after_the_first_and_noncalendar_end(
    database,
):
    conn, company, run = database
    end = date(2024, 6, 30)
    filing = conn.execute(
        """INSERT INTO bronze.statement_filings
        (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256)
        VALUES (%s,2024,1,'income_statement','test.xls','test.zip/test.xls','test') RETURNING filing_key""",
        (company,),
    ).fetchone()["filing_key"]
    store_context(
        conn,
        filing,
        run,
        {
            "measurement_basis": "inflation_adjusted",
            "purchasing_power_date": end,
            "evidence": {},
        },
    )
    frame = pd.DataFrame(
        [
            {
                "year": 2024,
                "quarter": 1,
                "date": "01.04.2024 - 30.06.2024",
                "table": "Income Statement",
                "section": "profit or loss",
                "line_item_path": "profit or loss > hasılat",
                "item": "hasılat",
                "value": 300,
                "report_kind": "Konsolide",
                "source_row_id": "sales:1",
                "source_column_id": None,
            }
        ]
    )
    pipeline._store_silver_rows(conn, company, {"Income Statement": filing}, frame, run)
    conn.execute(
        "INSERT INTO ops.active_filing_versions VALUES (%s,'income_statement',2024,1,%s,%s,now())",
        (company, filing, run),
    )
    conn.execute(
        "INSERT INTO silver.monthly_macro_data VALUES ('USDTRY_MONTHLY_OPEN',2024,6,'2024-06-03',30,'test',now())"
    )
    conn.execute(
        "UPDATE silver.monthly_macro_data SET value=100 WHERE observation_date='2024-06-01'"
    )
    conn.commit()
    pipeline.calculate_gold_financials_postgres(
        2024, 6, companies=[SimpleNamespace(symbol="TEST")]
    )
    result = conn.execute("SELECT * FROM gold.current_financial_values").fetchone()
    assert result["value_usd"] == 10
    assert (
        result["value_real"] == 300
    )  # June's report is already at the June price level.
    assert result["value_ytd"] == 300


def test_financial_growth_never_bridges_missing_periods(database):
    conn, company, run = database
    for year, q, end, amount in [
        (2022, 1, "2022-03-31", 100),
        (2024, 1, "2024-03-31", 200),
        (2024, 3, "2024-09-30", 300),
        (2024, 4, "2024-12-31", 600),
    ]:
        conn.execute(
            """INSERT INTO gold.financial_values
            (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_real,period_end,measurement_basis)
            VALUES (%s,%s,'balance_sheet',%s,%s,'assets','assets',%s,%s,'nominal')""",
            (run, company, year, q, amount, end),
        )
    calculate_value_changes(conn, run)
    result = conn.execute(
        "SELECT * FROM gold.financial_values ORDER BY fiscal_year,fiscal_quarter"
    ).fetchall()
    assert result[1]["value_real_yoy_change"] is None
    assert result[2]["value_real_qoq_change"] is None
    assert result[3]["value_real_qoq_change"] == 1
    conn.execute(
        "UPDATE gold.financial_values SET measurement_basis='inflation_adjusted' WHERE fiscal_year=2024 AND fiscal_quarter=4"
    )
    calculate_value_changes(conn, run)
    assert (
        conn.execute(
            "SELECT value_real_qoq_change FROM gold.financial_values WHERE fiscal_year=2024 AND fiscal_quarter=4"
        ).fetchone()["value_real_qoq_change"]
        is None
    )


def test_repair_restores_reported_quarter_and_ytd_and_is_atomic(database, monkeypatch):
    conn, company, run = database
    filing = conn.execute(
        """INSERT INTO bronze.statement_filings
        (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256)
        VALUES (%s,2024,3,'income_statement','test.xls','test.zip/test.xls','test') RETURNING filing_key""",
        (company,),
    ).fetchone()["filing_key"]
    for rownum, start, amount in [(0, "01.01.2024", 367), (1, "01.07.2024", 126)]:
        conn.execute(
            """INSERT INTO bronze.statement_rows
            (filing_key,source_row_number,source_row_id,source_date,section,line_item_path,item,raw_value,reporting_unit)
            VALUES (%s,%s,'sales:1',%s,'profit or loss','profit or loss > hasılat','hasılat',%s,1)""",
            (filing, rownum, start + " - 30.09.2024", amount),
        )
    conn.execute(
        "INSERT INTO ops.active_filing_versions VALUES (%s,'income_statement',2024,3,%s,%s,now())",
        (company, filing, run),
    )
    conn.commit()
    monkeypatch.setattr(
        repair_financials,
        "read_report_context",
        lambda *args: {
            "measurement_basis": "inflation_adjusted",
            "purchasing_power_date": date(2024, 9, 30),
            "evidence": {"source": "fixture"},
        },
    )
    result = repair_financials.repair_financials_postgres(rebuild_gold=False)
    assert result["filings"] == 1
    assert result["gold_runs"] == []
    restored = conn.execute(
        "SELECT amount,is_cumulative FROM silver.current_financial_observations ORDER BY amount"
    ).fetchall()
    assert restored == [
        {"amount": 126, "is_cumulative": False},
        {"amount": 367, "is_cumulative": True},
    ]
    previous = conn.execute("SELECT * FROM ops.active_filing_versions").fetchall()
    conn.commit()

    def fail(*args):
        raise RuntimeError("simulated source failure")

    monkeypatch.setattr(repair_financials, "read_report_context", fail)
    with pytest.raises(RuntimeError, match="simulated source failure"):
        repair_financials.repair_financials_postgres(rebuild_gold=False)
    assert (
        conn.execute("SELECT * FROM ops.active_filing_versions").fetchall() == previous
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM silver.financial_observations"
        ).fetchone()["n"]
        == 2
    )
