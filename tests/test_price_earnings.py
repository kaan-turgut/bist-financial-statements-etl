"""Price/EPS basis, real fiscal dates, publication and atomic refresh tests."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

import pipeline as cli
from models.price_earnings import price_earnings
from services import postgres_financial_pipeline as financials
from services.earnings_per_share import store_eps_source
from models.earnings_per_share import normalize_observation
from services.price_earnings import calculate_price_earnings, calculate_price_earnings_postgres


def example():
    end = date(2024, 3, 31)
    row = dict(value_ttm=Decimal(2),period_end=end,period_end_count=1,
        price_date=date(2024,3,29),instrument_id=1,instrument_count=1,currency="TRY",
        source_payload={"at_the_date_close_price": "20", "close_price": "5"},
        calculation_details={"unit":"TRY_per_1_TRY_nominal","source_ids":[1],"ttm_method":"annual_report"})
    source = dict(source_id=1,period_end=end,purchasing_power_date=end,
                  normalization={"split_adjustment_factor":"1"})
    return row, [source]


def test_pe_uses_original_close_over_ttm_and_records_retrospective_policy():
    row, sources = example()
    result = price_earnings(row,sources)
    assert result["pe_ratio"] == 10
    assert result["original_close_try"] == 20
    assert result["calculation_details"]["publication_aware"] is False
    assert result["calculation_details"]["retrospective"] is True


@pytest.mark.parametrize("change,reason", [
    ({"value_ttm":None},"missing_verified_ttm_eps"),
    ({"value_ttm":0},"nonpositive_ttm_eps"),
    ({"value_ttm":-2},"nonpositive_ttm_eps"),
    ({"period_end":None},"missing_or_ambiguous_fiscal_period_end"),
    ({"period_end_count":2},"missing_or_ambiguous_fiscal_period_end"),
    ({"instrument_count":2},"multiple_share_classes_require_class_specific_eps"),
    ({"currency":"USD"},"missing_try_instrument"),
    ({"price_date":date(2024,4,1)},"future_or_stale_price"),
    ({"price_date":date(2024,3,20)},"future_or_stale_price"),
    ({"price_date":None},"no_price_near_fiscal_period_end"),
    ({"source_payload":{"close_price":5}},"missing_or_invalid_original_close"),
    ({"source_payload":{"at_the_date_close_price":"NaN"}},"missing_or_invalid_original_close"),
    ({"source_payload":{"at_the_date_close_price":0}},"missing_or_invalid_original_close"),
])
def test_unsupported_pe_is_null_with_reason(change,reason):
    row,sources = example()
    row.update(change)
    result = price_earnings(row,sources)
    assert result["pe_ratio"] is None
    assert result["calculation_details"]["unavailable_reason"] == reason


def test_no_quarterly_eps_annualization_or_guessing_missing_basis():
    row,sources = example()
    row["value_ttm"] = None
    row["value_quarter"] = 1
    assert price_earnings(row,sources)["pe_ratio"] is None
    row["value_ttm"] = 2
    sources[0]["normalization"]["split_adjustment_factor"] = "4"
    assert price_earnings(row,sources)["calculation_details"]["unavailable_reason"] == "eps_split_basis_requires_price_reconciliation"
    sources[0]["normalization"]["split_adjustment_factor"] = "1"
    sources[0]["purchasing_power_date"] = date(2025,3,31)
    assert price_earnings(row,sources)["pe_ratio"] is None
    sources[0]["purchasing_power_date"] = None
    row["calculation_details"]["source_ids"] = [1,2]
    assert price_earnings(row,sources)["pe_ratio"] is None


@pytest.fixture
def published_period(database):
    conn,company,run = database
    instrument = conn.execute("INSERT INTO metadata.instruments(company_id,symbol,is_primary) VALUES (%s,'TEST',true) RETURNING instrument_id",(company,)).fetchone()["instrument_id"]
    # Fiscal 2023 Q4 ends in March 2024, not December 2023.
    conn.execute("""INSERT INTO gold.financial_values
        (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,period_end)
        VALUES (%s,%s,'income_statement',2023,4,'profit or loss > test','test','2024-03-31')""",(run,company))
    obs = normalize_observation(period_start=date(2023,4,1),period_end=date(2024,3,31),period_kind="ytd",reported_eps=2)
    store_eps_source(conn,company_id=company,year=2023,quarter=4,source_path="verified.json",source_hash="eps-test",
                     source_kind="json",payload={},observations=[obs],run_id=run)
    source = conn.execute("SELECT source_id FROM bronze.eps_sources").fetchone()["source_id"]
    details = {"unit":"TRY_per_1_TRY_nominal","ttm_method":"annual_report","source_ids":[source]}
    conn.execute("""INSERT INTO gold.eps_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,value_ttm,calculation_details)
        VALUES (%s,%s,2023,4,'basic','ordinary','total',2,%s)""",(run,company,Jsonb(details)))
    conn.execute("INSERT INTO ops.active_company_versions(dataset_name,company_id,run_id) VALUES ('financials',%s,%s)",(company,run))
    for day, original in [(date(2024,3,28),18),(date(2024,3,29),20),(date(2024,4,1),100)]:
        conn.execute("""INSERT INTO silver.daily_prices(instrument_id,price_date,close,source)
            VALUES (%s,%s,5,'IsYatirim')""",(instrument,day))
        conn.execute("""INSERT INTO bronze.price_observations
            (instrument_id,price_date,close,source,source_hash,run_id,source_payload)
            VALUES (%s,%s,5,'IsYatirim',%s,%s,%s)""",
            (instrument,day,str(day),run,Jsonb({"at_the_date_close_price":original,"close_price":5})))
    conn.commit()
    return conn,company,run


def test_database_refresh_uses_fiscal_end_and_publishes_auditable_ratio(published_period):
    conn,company,run = published_period
    result = calculate_price_earnings_postgres()
    assert result["available"] == 1
    row = conn.execute("SELECT * FROM gold.current_price_earnings").fetchone()
    assert row["period_end"] == date(2024,3,31)
    assert row["price_date"] == date(2024,3,29)
    assert row["pe_ratio"] == 10
    assert row["original_close_try"] == 20
    assert row["basic_eps_ttm"] == 2
    ratio = conn.execute("SELECT * FROM gold.current_financial_ratios WHERE ratio_code='price_earnings'").fetchone()
    assert ratio["value_ttm"] == 10
    assert ratio["value_quarter"] is None and ratio["value_ytd"] is None
    assert ratio["calculation_run_id"] == run
    conn.commit()
    again = calculate_price_earnings_postgres([SimpleNamespace(symbol="TEST")])
    assert again["available"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.current_price_earnings").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.price_earnings_values").fetchone()["n"] == 2


def test_valuation_failure_rolls_back_publication(published_period,monkeypatch):
    from services import price_earnings as service
    conn,company,run = published_period
    calculate_price_earnings_postgres()
    before = conn.execute("SELECT * FROM gold.current_price_earnings").fetchone()
    conn.commit()
    original = service.calculate_price_earnings

    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError("simulated valuation failure")

    monkeypatch.setattr(service,"calculate_price_earnings",fail)
    with pytest.raises(RuntimeError,match="simulated valuation failure"):
        calculate_price_earnings_postgres()
    assert conn.execute("SELECT * FROM gold.current_price_earnings").fetchone() == before


def test_new_financial_version_hides_old_valuation(published_period):
    conn,company,run = published_period
    calculate_price_earnings_postgres()
    newer = financials._run_start(conn,"test",{})
    conn.execute("UPDATE ops.active_company_versions SET run_id=%s WHERE company_id=%s",(newer,company))
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.current_price_earnings").fetchone()["n"] == 0


def test_automatic_gold_valuation_can_be_repeated_within_same_run(published_period):
    conn,company,run = published_period
    for _ in range(2):
        assert calculate_price_earnings(conn,run,financial_run_id=run)["available"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.price_earnings_values").fetchone()["n"] == 1


def test_calculate_pe_cli_passes_company_scope(monkeypatch):
    companies=[SimpleNamespace(symbol="TEST")]
    calls=[]
    monkeypatch.setattr(cli,"resolve_companies",lambda symbols:companies)
    monkeypatch.setattr(cli,"calculate_price_earnings_postgres",lambda selected:calls.append(selected))
    cli.main(["calculate-pe","--symbols","TEST"])
    cli.main(["calculate-pe"])
    assert calls == [companies,None]
