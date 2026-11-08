"""PostgreSQL regressions; BIST_TEST_POSTGRES_DSN enables an isolated test database.

The supplied role must be able to create databases. Tests never migrate or
recalculate the supplied database: all writes go to a disposable database.
"""

import calendar
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from psycopg import sql

from services import postgres_financial_pipeline as pipeline
from services.financial_ratios import (
    calculate_financial_ratios,
    calculate_ratio_changes,
)


def add_period(conn, company_id, run_id, year, quarter, *, sales=100, cogs=-60,
               operating=20, profit=10, assets=1000, equity=500, factor=1,
               base_year=2024, base_month=3, flows=True):
    items = [("balance_sheet", "toplam varlıklar", assets),
             ("balance_sheet", "toplam özkaynaklar", equity),
             ("balance_sheet", "toplam dönen varlıklar", 400),
             ("balance_sheet", "toplam kısa vadeli yükümlülükler", 200),
             ("balance_sheet", "nakit ve nakit benzerleri", 100),
             ("balance_sheet", "toplam yükümlülükler", 500)]
    if flows:
        items.extend(("income_statement", item, value) for item, value in (
            ("hasılat", sales), ("satışların maliyeti", cogs),
            ("esas faaliyet karı (zararı)", operating), ("dönem karı (zararı)", profit)))
    for statement, item, value in items:
        path = "profit or loss > " + item if statement == "income_statement" else item
        conn.execute("""INSERT INTO gold.financial_values
            (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,
             line_item_path,item,value_quarter,value_ytd,value_real,
             inflation_base_year,inflation_base_month,period_start,period_end,measurement_basis)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'nominal')""",
            (run_id, company_id, statement, year, quarter, path, item, value, value,
             None if value is None else value*factor, base_year, base_month,
             date(year,quarter*3-2,1),date(year,quarter*3,calendar.monthrange(year,quarter*3)[1])))


def ratios_at(conn, run_id, year=2024, quarter=1):
    calculate_financial_ratios(conn, run_id)
    calculate_ratio_changes(conn, run_id)
    return {row["ratio_code"]: row for row in conn.execute("""SELECT * FROM gold.financial_ratios
        WHERE calculation_run_id=%s AND fiscal_year=%s AND fiscal_quarter=%s""",
        (run_id, year, quarter)).fetchall()}


def seed_four_quarters(database, *, missing=None):
    conn, company, run = database
    add_period(conn, company, run, 2023, 1, flows=False)
    for index, (year, quarter) in enumerate(((2023, 2), (2023, 3), (2023, 4), (2024, 1)), 1):
        if (year, quarter) == missing:
            continue
        add_period(conn, company, run, year, quarter, sales=index*100,
                   cogs=(-50, -120, -240, -200)[index-1], profit=index*10,
                   operating=index*20, assets=3000 if index == 4 else 1000,
                   equity=1500 if index == 4 else 500)
    return conn, company, run


def test_ttm_ratios_cross_year_with_null_source_columns(database):
    conn, _, run = seed_four_quarters(database)
    result = ratios_at(conn, run)
    assert result["gross_margin"]["value_ttm"] == Decimal("0.39")
    assert result["operating_margin"]["value_ttm"] == Decimal("0.2")
    assert result["return_on_assets"]["value_ttm"] == Decimal("0.05")
    assert result["return_on_equity"]["value_ttm"] == Decimal("0.1")
    assert result["asset_turnover_ratio"]["value_ttm"] == Decimal("0.5")
    assert result["eps"]["value_ttm"] is None  # Current metadata cannot establish historical EPS.
    assert result["gross_margin"]["value_quarter"] == Decimal("0.5")
    for code in ("current_ratio", "cash_ratio", "debt_ratio", "debt_to_equity_ratio"):
        assert result[code]["value_ttm"] is None
    earlier = ratios_at(conn, run, 2023, 4)
    assert all(row["value_ttm"] is None for row in earlier.values())


def test_missing_quarter_does_not_pull_in_older_period(database):
    conn, company, run = seed_four_quarters(database, missing=(2023, 3))
    add_period(conn, company, run, 2022, 4)
    assert all(row["value_ttm"] is None for row in ratios_at(conn, run).values())


def test_four_fiscal_labels_with_noncontiguous_dates_do_not_form_ttm(database):
    conn, _, run = seed_four_quarters(database)
    conn.execute("""UPDATE gold.financial_values SET period_start=period_start+1
        WHERE fiscal_year=2023 AND fiscal_quarter=3""")
    assert all(row['value_ttm'] is None for row in ratios_at(conn,run).values())


def test_overlapping_trailing_windows_and_ttm_changes(database):
    conn, company, run = seed_four_quarters(database)
    add_period(conn, company, run, 2024, 2, sales=500, cogs=-250,
               operating=100, profit=50, assets=3500, equity=1750)
    result = ratios_at(conn, run, 2024, 2)
    margin = result["gross_margin"]
    expected = Decimal(590)/Decimal(1400)
    assert margin["value_ttm"] == pytest.approx(expected)
    assert margin["ttm_qoq_change"] == pytest.approx(expected/Decimal("0.39")-1)
    assert result["operating_margin"]["ttm_qoq_change"] == 0


@pytest.mark.parametrize("item", ["satışların maliyeti", "hasılat"])
def test_missing_flow_input_suppresses_only_affected_ratios(database, item):
    conn, _, run = seed_four_quarters(database)
    conn.execute("""DELETE FROM gold.financial_values
        WHERE calculation_run_id=%s AND fiscal_year=2023 AND fiscal_quarter=3 AND item=%s""", (run, item))
    result = ratios_at(conn, run)
    assert result["gross_margin"]["value_ttm"] is None
    assert result["return_on_assets"]["value_ttm"] is not None
    assert (result["operating_margin"]["value_ttm"] is None) == (item == "hasılat")


def test_missing_cpi_does_not_publish_partial_trailing_sum(database):
    conn, _, run = seed_four_quarters(database)
    conn.execute("""UPDATE gold.financial_values SET value_real=NULL
        WHERE calculation_run_id=%s AND fiscal_year=2023 AND fiscal_quarter=3""", (run,))
    assert all(row["value_ttm"] is None for row in ratios_at(conn, run).values())


def test_missing_opening_balance_keeps_margins_but_suppresses_returns(database):
    conn, _, run = seed_four_quarters(database)
    conn.execute("""DELETE FROM gold.financial_values
        WHERE calculation_run_id=%s AND fiscal_year=2023 AND fiscal_quarter=1""", (run,))
    result = ratios_at(conn, run)
    assert result["gross_margin"]["value_ttm"] == Decimal("0.39")
    for code in ("return_on_assets", "return_on_equity", "asset_turnover_ratio"):
        assert result[code]["value_ttm"] is None


def test_opening_balance_on_another_measurement_basis_does_not_supply_returns(database):
    conn, _, run=seed_four_quarters(database)
    conn.execute("UPDATE gold.financial_values SET measurement_basis='inflation_adjusted' WHERE fiscal_year=2023 AND fiscal_quarter=1")
    result=ratios_at(conn,run)
    assert result['gross_margin']['value_ttm']==Decimal('0.39')
    assert result['return_on_assets']['value_ttm'] is None
    assert result['return_on_equity']['value_ttm'] is None


def test_four_quarters_on_different_measurement_bases_do_not_form_ttm(database):
    conn, _, run=seed_four_quarters(database)
    conn.execute("UPDATE gold.financial_values SET measurement_basis='inflation_adjusted' WHERE fiscal_year=2023 AND fiscal_quarter=3")
    assert all(row['value_ttm'] is None for row in ratios_at(conn,run).values())


def test_zero_denominators_are_null(database):
    conn, _, run = seed_four_quarters(database)
    conn.execute("""UPDATE gold.financial_values SET value_quarter=0,value_real=0
        WHERE calculation_run_id=%s AND item IN ('hasılat','toplam varlıklar','toplam özkaynaklar')""", (run,))
    result = ratios_at(conn, run)
    for code in ("gross_margin", "operating_margin", "return_on_assets", "return_on_equity", "asset_turnover_ratio"):
        assert result[code]["value_ttm"] is None


def test_ttm_uses_common_purchasing_power_and_is_independent_of_target(database):
    conn, company, run = database
    conn.execute("""UPDATE silver.monthly_macro_data SET value=100
        WHERE observation_date IN ('2023-06-01','2023-09-01','2023-12-01','2024-03-01','2024-06-01')""")
    results = []
    for run_id, scale, base_month in ((run, 1, 3), (pipeline._run_start(conn, "test", {}), 2, 6)):
        add_period(conn, company, run_id, 2023, 1, flows=False, factor=16*scale, base_month=base_month)
        for year, quarter, factor in ((2023, 2, 8), (2023, 3, 4), (2023, 4, 2), (2024, 1, 1)):
            add_period(conn, company, run_id, year, quarter, factor=factor*scale,
                       assets=3000 if year == 2024 else 1000, base_month=base_month)
        results.append(ratios_at(conn, run_id))
    assert {k: v["value_ttm"] for k, v in results[0].items()} == {
        k: v["value_ttm"] for k, v in results[1].items()}
    assert results[0]["eps"]["value_ttm"] is None
    assert results[0]["return_on_assets"]["value_ttm"] == pytest.approx(Decimal(150)/Decimal(9500))


def test_ratio_changes_do_not_bridge_missing_quarters_or_years(database):
    conn, company, run = database
    for year, quarter, value in ((2022, 1, 1), (2024, 1, 2), (2024, 3, 3), (2024, 4, 6)):
        conn.execute("""INSERT INTO gold.financial_ratios
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,
             value_quarter,value_ytd,value_ttm) VALUES (%s,%s,%s,%s,'operating_margin',%s,%s,%s)""",
            (run, company, year, quarter, value, value, value))
    calculate_ratio_changes(conn, run)
    rows = conn.execute("SELECT * FROM gold.financial_ratios ORDER BY fiscal_year,fiscal_quarter").fetchall()
    assert rows[1]["quarter_yoy_change"] is None
    assert rows[1]["ytd_yoy_change"] is None
    assert rows[2]["ttm_qoq_change"] is None
    assert rows[2]["quarter_qoq_change"] is None
    assert rows[3]["ttm_qoq_change"] == Decimal(1)


def test_company_histories_are_not_combined(database):
    conn, company, run = database
    other = conn.execute("""INSERT INTO metadata.companies (symbol,mkk_member_oid)
        VALUES ('OTHER','other-oid') RETURNING company_id""").fetchone()["company_id"]
    for index, (year, quarter) in enumerate(((2023, 2), (2023, 3), (2023, 4), (2024, 1))):
        add_period(conn, company if index < 2 else other, run, year, quarter)
    assert all(row["value_ttm"] is None for row in ratios_at(conn, run).values())


def test_migration_preserves_financial_values_and_ratios(legacy_database):
    with legacy_database() as conn:
        company = conn.execute("""INSERT INTO metadata.companies (symbol,mkk_member_oid)
            VALUES ('TEST','test-oid') RETURNING company_id""").fetchone()["company_id"]
        run = pipeline._run_start(conn, "test", {})
        conn.execute("""INSERT INTO gold.financial_values
            (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,
             line_item_path,item,value_quarter,value_ttm,value_real_ttm,value_real_ttm_qoq_change)
            VALUES (%s,%s,'income_statement',2024,1,'profit or loss > hasılat','hasılat',100,400,500,0.1)""", (run, company))
        conn.execute("""INSERT INTO gold.financial_ratios
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_ttm)
            VALUES (%s,%s,2024,1,'gross_margin',0.4)""", (run, company))
        conn.execute("INSERT INTO ops.active_company_versions VALUES ('financials',%s,%s,now())", (company, run))
    assert pipeline.apply_medallion_migration()
    assert not pipeline.apply_medallion_migration()
    with legacy_database() as conn:
        row = conn.execute("SELECT * FROM gold.current_financial_values").fetchone()
        assert row["value_quarter"] == 100
        assert not {"value_ttm", "value_real_ttm", "value_real_ttm_qoq_change"} & row.keys()
        assert conn.execute("SELECT value_ttm FROM gold.current_financial_ratios").fetchone()["value_ttm"] == Decimal("0.4")


def test_full_gold_pipeline_uses_ratio_only_ttm(database):
    conn, company, run = database
    for year, quarter in ((2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1)):
        for statement, item, amount in (("income_statement", "hasılat", 100),
                                        ("income_statement", "satışların maliyeti", -60),
                                        ("income_statement", "dönem karı (zararı)", 10),
                                        ("balance_sheet", "toplam varlıklar", 1000)):
            filing = conn.execute("""INSERT INTO bronze.statement_filings
                (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256)
                VALUES (%s,%s,%s,%s,'test.xls','test.zip/test.xls','test')
                ON CONFLICT (company_id,fiscal_year,fiscal_quarter,statement_type,source_sha256)
                DO UPDATE SET source_filename=EXCLUDED.source_filename RETURNING filing_key""",
                (company, year, quarter, statement)).fetchone()["filing_key"]
            path = "profit or loss > " + item if statement == "income_statement" else item
            from services.financial_report_context import store_context

            store_context(conn, filing, run, {'measurement_basis': 'nominal', 'purchasing_power_date': None,
                                            'evidence': {'source': 'synthetic nominal fixture'}})
            conn.execute("""INSERT INTO silver.financial_observations
                (company_id,filing_key,processing_run_id,statement_type,fiscal_year,fiscal_quarter,
                 period_start,period_end,line_item_path,item,amount)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (company, filing, run, statement, year, quarter, date(year, quarter*3-2, 1),
                 date(year, quarter*3, calendar.monthrange(year, quarter*3)[1]), path, item, amount))
            conn.execute("""INSERT INTO ops.active_filing_versions
                (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (company, statement, year, quarter, filing, run))
    conn.commit()
    new_run = pipeline.calculate_gold_financials_postgres(2024, 3, companies=[SimpleNamespace(symbol="TEST")])
    ratio = conn.execute("""SELECT value_ttm FROM gold.current_financial_ratios
        WHERE fiscal_year=2024 AND fiscal_quarter=1 AND ratio_code='gross_margin'""").fetchone()
    assert ratio["value_ttm"] == Decimal("0.4")
    assert conn.execute("SELECT status FROM ops.pipeline_runs WHERE run_id=%s", (new_run,)).fetchone()["status"] == "succeeded"


def test_fresh_medallion_install_applies_both_migrations(legacy_database):
    with legacy_database() as conn:
        for schema in ("gold", "silver", "bronze", "ops"):
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    assert pipeline.apply_medallion_migration()
    with legacy_database() as conn:
        versions = conn.execute("SELECT version FROM ops.schema_migrations ORDER BY version").fetchall()
        assert [r["version"] for r in versions] == ["007", "008", "009", "010", "011", "012", "013", "014"]
        columns = conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='gold' AND table_name='financial_values'""").fetchall()
        assert not any("ttm" in r["column_name"] for r in columns)


@pytest.mark.parametrize("include_q3", [False, True])
def test_cumulative_differences_require_immediate_previous_period(database, include_q3):
    conn, company, run = database
    periods = [(1, 100), (2, 300), (4, 1000)]
    if include_q3:
        periods.append((3, 600))
    for quarter, amount in periods:
        filing = conn.execute("""INSERT INTO bronze.statement_filings
            (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256)
            VALUES (%s,2024,%s,'income_statement','test.xls','test.zip/test.xls','test')
            RETURNING filing_key""", (company, quarter)).fetchone()["filing_key"]
        from services.financial_report_context import store_context

        store_context(conn, filing, run, {'measurement_basis': 'nominal', 'purchasing_power_date': None,
                                        'evidence': {'source': 'synthetic nominal fixture'}})
        conn.execute("""INSERT INTO silver.financial_observations
            (company_id,filing_key,processing_run_id,statement_type,fiscal_year,fiscal_quarter,
             period_start,period_end,line_item_path,item,amount,is_cumulative)
            VALUES (%s,%s,%s,'income_statement',2024,%s,'2024-01-01',%s,
                    'profit or loss > hasılat','hasılat',%s,true)""",
            (company, filing, run, quarter, date(2024, quarter*3, calendar.monthrange(2024,quarter*3)[1]), amount))
        conn.execute("""INSERT INTO ops.active_filing_versions
            (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
            VALUES (%s,'income_statement',2024,%s,%s,%s)""", (company, quarter, filing, run))
    conn.commit()
    pipeline.calculate_gold_financials_postgres(2024, 12, companies=[SimpleNamespace(symbol="TEST")])
    q4 = conn.execute("""SELECT value_quarter FROM gold.current_financial_values
        WHERE fiscal_year=2024 AND fiscal_quarter=4 AND item='hasılat'""").fetchone()
    assert q4["value_quarter"] == (400 if include_q3 else None)
