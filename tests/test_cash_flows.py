"""Cash-flow period, publication, and Bronze recovery regressions."""

import calendar
import hashlib
import zipfile
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from services import postgres_financial_pipeline as pipeline
from services.repair_cash_flows import repair_cash_flows_postgres

OPERATING = "işletme faaliyetlerinden nakit akışları"
OPENING = "dönem başı nakit ve nakit benzerleri"
CLOSING = "dönem sonu nakit ve nakit benzerleri"


def cash_frame(quarter, *, start=None, end=None, item=OPERATING):
    start = start or date(2023, 1, 1)
    month = quarter*3
    end = end or date(2023, month, calendar.monthrange(2023, month)[1])
    return pd.DataFrame([{
        "year": 2023, "quarter": quarter,
        "date": f"{start:%d.%m.%Y} - {end:%d.%m.%Y}",
        "table": "Statement of Cash Flows", "section": "cash flow statement",
        "line_item_path": f"cash flow statement > {item}", "item": item,
        "value": 100, "report_kind": "Konsolide", "source_row_id": "cash:1",
        "source_column_id": None,
    }])


class RecordingConnection:
    def __init__(self):
        self.rows = []

    def execute(self, query, params):
        self.rows.append(params)


@pytest.mark.parametrize("quarter", [1, 2, 3, 4])
def test_cumulative_cash_flows_keep_filing_quarter(quarter):
    conn = RecordingConnection()
    pipeline._store_silver_rows(conn, 1, {"Statement of Cash Flows": 10}, cash_frame(quarter), 20)
    row = conn.rows[0]
    assert row[5] == quarter
    assert row[13] is True


def test_standalone_q2_cash_flow_is_not_marked_cumulative():
    conn = RecordingConnection()
    pipeline._store_silver_rows(conn, 1, {"Statement of Cash Flows": 10},
                               cash_frame(2, start=date(2023, 4, 1)), 20)
    assert conn.rows[0][5] == 2
    assert conn.rows[0][13] is False


@pytest.mark.parametrize("item", pipeline.CASH_BALANCE_ITEMS)
def test_opening_and_closing_cash_are_balances_not_cumulative_flows(item):
    conn = RecordingConnection()
    pipeline._store_silver_rows(conn, 1, {"Statement of Cash Flows": 10}, cash_frame(2, item=item), 20)
    assert conn.rows[0][5] == 2
    assert conn.rows[0][13] is False


def test_fiscal_quarter_is_not_inferred_from_calendar_month():
    conn = RecordingConnection()
    pipeline._store_silver_rows(conn, 1, {"Statement of Cash Flows": 10},
        cash_frame(2, start=date(2023, 4, 1), end=date(2023, 9, 30)), 20)
    assert conn.rows[0][5] == 2
    assert conn.rows[0][13] is True


def test_annual_income_statement_marker_still_maps_to_q4():
    frame = cash_frame(4)
    frame["table"] = "Income Statement"
    frame["quarter"] = 1234
    conn = RecordingConnection()
    pipeline._store_silver_rows(conn, 1, {"Income Statement": 10}, frame, 20)
    assert conn.rows[0][5] == 4
    assert conn.rows[0][13] is True


def seed_sources(database, quarters=(1, 2, 3, 4), *, old_q4_pointer=False):
    conn, company, run = database
    filings = {}
    # Operating values are from THYAO's original 2023 KAP exports, in TL million.
    # Cash balances below are simple synthetic values for stock/flow checks.
    for quarter in quarters:
        key = conn.execute("""INSERT INTO bronze.statement_filings
            (company_id,filing_id,fiscal_year,fiscal_quarter,statement_type,report_kind,
             source_filename,source_path,source_sha256,run_id)
            VALUES (%s,%s,2023,%s,'cash_flow','Konsolide',%s,%s,%s,%s) RETURNING filing_key""",
            (company, 100+quarter, quarter, f'TEST_2023_{quarter}.xls', f'test.zip/TEST_2023_{quarter}.xls',
             hashlib.sha256(str(quarter).encode()).hexdigest(), run)).fetchone()["filing_key"]
        filings[quarter] = key
        from services.financial_report_context import store_context

        store_context(conn, key, run, {'measurement_basis': 'nominal', 'purchasing_power_date': None,
                                      'evidence': {'source': 'synthetic nominal fixture'}})
        frame = cash_frame(quarter)
        period = frame["date"].iloc[0]
        for index, (item, amount) in enumerate(((OPERATING, (29689, 75881, 113353, 145032)[quarter-1]),
                                               (OPENING, 200), (CLOSING, 200+quarter*10))):
            conn.execute("""INSERT INTO bronze.statement_rows
                (filing_key,source_row_number,source_row_id,source_date,section,line_item_path,
                 item,raw_value,reporting_unit)
                VALUES (%s,%s,%s,%s,'cash flow statement',%s,%s,%s,1000000)""",
                (key, index, f'cash:{index}', period, f'cash flow statement > {item}', item, amount))
        if quarter in (1, 4) or old_q4_pointer:
            old = frame.copy()
            old["quarter"] = 4 if old_q4_pointer else quarter
            pipeline._store_silver_rows(conn, company, {"Statement of Cash Flows": key}, old, run)
            conn.execute("""INSERT INTO ops.active_filing_versions
                (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
                VALUES (%s,'cash_flow',2023,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (company, 4 if old_q4_pointer else quarter, key, run))
    conn.commit()
    return filings


def active_flows(conn):
    return conn.execute("""SELECT fiscal_quarter,item,amount,is_cumulative
        FROM silver.current_financial_observations WHERE statement_type='cash_flow'
        ORDER BY fiscal_quarter,item""").fetchall()


def test_repair_restores_q2_q3_and_rebuilds_published_gold(database):
    conn, _, _ = database
    seed_sources(database)
    pipeline.calculate_gold_financials_postgres(2024, 3, companies=[SimpleNamespace(symbol="TEST")])
    result = repair_cash_flows_postgres()
    assert result["filings"] == 4
    assert result["companies"] == 1
    assert len(result["gold_runs"]) == 1
    rows = conn.execute("""SELECT fiscal_quarter,value_quarter,value_ytd
        FROM gold.current_financial_values WHERE statement_type='cash_flow' AND item=%s
        ORDER BY fiscal_quarter""", (OPERATING,)).fetchall()
    assert [r["fiscal_quarter"] for r in rows] == [1, 2, 3, 4]
    assert [r["value_quarter"] for r in rows] == [Decimal(v)*1000000 for v in (29689, 46192, 37472, 31679)]
    assert [r["value_ytd"] for r in rows] == [Decimal(v)*1000000 for v in (29689, 75881, 113353, 145032)]
    balances = conn.execute("""SELECT value_quarter,value_ytd FROM gold.current_financial_values
        WHERE statement_type='cash_flow' AND item=%s ORDER BY fiscal_quarter""", (OPENING,)).fetchall()
    assert all(r["value_quarter"] == r["value_ytd"] == 200000000 for r in balances)
    assert conn.execute("SELECT COUNT(*) AS n FROM bronze.statement_filings").fetchone()["n"] == 4
    before = active_flows(conn)
    conn.commit()
    repair_cash_flows_postgres()
    assert active_flows(conn) == before


def test_missing_prior_quarter_preserves_reported_ytd_without_inventing_quarter(database):
    conn, _, _ = database
    seed_sources(database, quarters=(1, 3, 4))
    result = repair_cash_flows_postgres()
    assert result["gold_runs"] == []  # Do not publish previously unpublished companies.
    pipeline.calculate_gold_financials_postgres(2024, 3, companies=[SimpleNamespace(symbol="TEST")])
    q3 = conn.execute("""SELECT value_quarter,value_ytd FROM gold.current_financial_values
        WHERE statement_type='cash_flow' AND fiscal_quarter=3 AND item=%s""", (OPERATING,)).fetchone()
    assert q3["value_quarter"] is None
    assert q3["value_ytd"] == 113353000000
    q4 = conn.execute("""SELECT value_quarter,value_ytd FROM gold.current_financial_values
        WHERE statement_type='cash_flow' AND fiscal_quarter=4 AND item=%s""", (OPERATING,)).fetchone()
    assert q4["value_quarter"] == 31679000000
    assert q4["value_ytd"] == 145032000000


def test_repair_removes_old_false_q4_pointer_and_keeps_processing_history(database):
    conn, _, _ = database
    seed_sources(database, quarters=(2,), old_q4_pointer=True)
    old_count = conn.execute("SELECT COUNT(*) AS n FROM silver.financial_observations").fetchone()["n"]
    conn.commit()
    repair_cash_flows_postgres()
    assert {r["fiscal_quarter"] for r in active_flows(conn)} == {2}
    assert conn.execute("SELECT COUNT(*) AS n FROM silver.financial_observations").fetchone()["n"] > old_count


def test_failed_repair_preserves_active_data(database, monkeypatch):
    conn, _, _ = database
    seed_sources(database)
    before = active_flows(conn)
    before_count = conn.execute("SELECT COUNT(*) AS n FROM silver.financial_observations").fetchone()["n"]
    conn.commit()
    original = pipeline._store_silver_rows
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        original(*args)
        if calls == 2:
            raise RuntimeError("simulated write failure")

    monkeypatch.setattr(pipeline, "_store_silver_rows", fail_second)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        repair_cash_flows_postgres()
    assert active_flows(conn) == before
    assert conn.execute("SELECT COUNT(*) AS n FROM silver.financial_observations").fetchone()["n"] == before_count
    assert conn.execute("SELECT status FROM ops.pipeline_runs WHERE pipeline_name='cash_flow_repair'").fetchone()["status"] == "failed"


def test_normal_ingestion_keeps_interim_periods_and_source_revisions(database, tmp_path, monkeypatch):
    conn, _, _ = database
    folder = tmp_path / "TEST"
    folder.mkdir()
    archive = folder / "TEST_2023.zip"

    def write_archive(q2_amount):
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("TEST_102_2023_2.xls", str(q2_amount))
            zipped.writestr("TEST_103_2023_3.xls", "300")

    def parse(path, company):
        archive_path, member = path.split(".zip/", 1)
        quarter = int(member.removesuffix(".xls")[-1])
        with zipfile.ZipFile(archive_path + ".zip") as zipped:
            amount = int(zipped.read(member))
        frame = cash_frame(quarter)
        frame["company"] = company.symbol
        frame["reporting unit"] = 1
        frame["filing_id"] = 100 + quarter
        frame["file_path"] = path
        frame["source_filename"] = member
        frame["column_label"] = None
        frame["value"] = amount
        return SimpleNamespace(df=frame)

    monkeypatch.setattr(pipeline, "FINANCIAL_STATEMENTS_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "parse_statement", parse)
    company = SimpleNamespace(symbol="TEST")
    write_archive(100)
    assert pipeline.ingest_statements_postgres([company], 2023, 2, 2023, 3, parse_workers=1) == 2
    first = conn.execute("SELECT filing_key FROM ops.active_filing_versions WHERE fiscal_quarter=2").fetchone()["filing_key"]
    assert {r["fiscal_quarter"] for r in active_flows(conn)} == {2, 3}
    conn.commit()
    write_archive(200)
    pipeline.ingest_statements_postgres([company], 2023, 2, 2023, 3, parse_workers=1)
    assert conn.execute("SELECT COUNT(*) AS n FROM bronze.statement_filings").fetchone()["n"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM silver.financial_observations WHERE filing_key=%s", (first,)).fetchone()["n"] == 1
    conn.commit()
    repair_cash_flows_postgres([company])
    rows = active_flows(conn)
    assert [(r["fiscal_quarter"], r["amount"]) for r in rows] == [(2, 200), (3, 300)]


def test_scoped_repair_leaves_other_companies_unchanged(database):
    conn, _, run = database
    seed_sources(database, quarters=(2,), old_q4_pointer=True)
    other = conn.execute("""INSERT INTO metadata.companies (symbol,mkk_member_oid)
        VALUES ('OTHER','other-oid') RETURNING company_id""").fetchone()["company_id"]
    seed_sources((conn, other, run), quarters=(2,), old_q4_pointer=True)
    repair_cash_flows_postgres([SimpleNamespace(symbol="TEST")])
    rows = conn.execute("""SELECT c.symbol,s.fiscal_quarter FROM silver.current_financial_observations s
        JOIN metadata.companies c USING (company_id) WHERE item=%s ORDER BY c.symbol""", (OPERATING,)).fetchall()
    assert [(r["symbol"], r["fiscal_quarter"]) for r in rows] == [("OTHER", 4), ("TEST", 2)]


def test_gold_republication_prunes_superseded_rows_for_vanished_periods(database):
    conn, _, _ = database
    seed_sources(database, quarters=(2,), old_q4_pointer=True)
    company = [SimpleNamespace(symbol="TEST")]
    pipeline.calculate_gold_financials_postgres(2024, 3, companies=company)
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.financial_values WHERE fiscal_quarter=4").fetchone()["n"]
    conn.commit()
    repair_cash_flows_postgres()  # Moves the filing to Q2 and republishes Gold.
    runs = conn.execute("SELECT DISTINCT calculation_run_id FROM gold.financial_values").fetchall()
    assert len(runs) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.financial_values WHERE fiscal_quarter=4").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM gold.financial_ratios WHERE fiscal_quarter=4").fetchone()["n"] == 0


def test_activation_refuses_filing_outside_its_source_period(database):
    conn, company, run = database
    filings = seed_sources(database, quarters=(2,))
    candidate = {
        "company_id": company, "statement_type": "cash_flow", "fiscal_year": 2023,
        "fiscal_quarter": 4, "filing_key": filings[2], "processing_run_id": run,
        "report_kind": "Konsolide", "filing_id": 102,
    }
    with pytest.raises(ValueError, match="outside their source period"):
        pipeline._activate_filing_versions(conn, [candidate], run)
    conn.rollback()
    pipeline._activate_filing_versions(conn, [{**candidate, "fiscal_quarter": 2}], run)
    assert conn.execute(
        "SELECT fiscal_quarter FROM ops.active_filing_versions WHERE filing_key=%s", (filings[2],)
    ).fetchone()["fiscal_quarter"] == 2
