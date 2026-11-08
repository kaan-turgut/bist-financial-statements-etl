"""Restore cash-flow observations from retained Bronze filings, without downloads."""

import logging
from collections import defaultdict
from types import SimpleNamespace

import pandas as pd

from services import postgres_financial_pipeline as financials
from services.financial_report_context import read_report_context, store_context

logger = logging.getLogger(__name__)


def repair_cash_flows_postgres(companies=None):
    """Rebuild cash-flow Silver, then refresh already-published Gold companies.

    Fiscal periods come from the original filing, never the calendar quarter of
    its end date. Other statement types and unpublished Gold companies are left
    alone. Old Bronze/Silver versions remain available for audit and recovery.
    """
    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        company_filter = " AND c.symbol=ANY(%s)" if companies is not None else ""
        params = ([company.symbol for company in companies],) if companies is not None else ()
        rows = conn.execute("""SELECT c.symbol AS company,f.company_id AS _company_id,
                f.filing_key AS _filing_key,f.fiscal_year AS year,f.fiscal_quarter AS quarter,
                f.report_kind,f.filing_id,f.source_path AS file_path,
                r.source_date AS date,r.section,r.line_item_path,r.item,
                r.raw_value AS value,COALESCE(r.reporting_unit,1) AS "reporting unit",
                r.source_row_id,r.source_column_id,r.column_label,
                'Statement of Cash Flows' AS "table"
            FROM bronze.statement_filings f
            JOIN metadata.companies c USING (company_id)
            JOIN bronze.statement_rows r USING (filing_key)
            WHERE f.statement_type='cash_flow'""" + company_filter + """
            ORDER BY f.filing_key,r.source_row_number""", params).fetchall()
        if not rows:
            raise ValueError("No Bronze cash-flow source rows found for the requested companies.")
        logger.info("Cleaning %s retained Bronze cash-flow rows", len(rows))
        clean = financials._clean_frame(pd.DataFrame(rows))
        if clean.empty:
            raise ValueError("Bronze cash-flow records contained no usable current-period observations.")
        candidates = clean[["_company_id", "_filing_key", "year", "quarter",
                            "report_kind", "filing_id"]].drop_duplicates().rename(columns={
            "_company_id": "company_id", "_filing_key": "filing_key",
            "year": "fiscal_year", "quarter": "fiscal_quarter",
        }).to_dict("records")
        for candidate in candidates:
            candidate["statement_type"] = "cash_flow"
        versions = financials._choose_active_filing_versions(candidates)
        company_ids = sorted({row["company_id"] for row in versions})
        previous = conn.execute("""SELECT * FROM ops.active_filing_versions
            WHERE statement_type='cash_flow' AND company_id=ANY(%s)""", (company_ids,)).fetchall()
        published = conn.execute("""SELECT DISTINCT c.symbol,g.inflation_base_year,g.inflation_base_month
            FROM gold.current_financial_values g JOIN metadata.companies c USING (company_id)
            WHERE g.company_id=ANY(%s)""", (company_ids,)).fetchall()
        run_id = financials._run_start(conn, "cash_flow_repair", {
            "source": "bronze.statement_rows", "company_ids": company_ids,
            "previous_filing_versions": previous,
        })
        conn.commit()
        try:
            frames = {key: part for key, part in clean.groupby("_filing_key", sort=False)}
            for index, version in enumerate(versions, 1):
                version["processing_run_id"] = run_id
                financials._store_silver_rows(conn, version["company_id"],
                    {"Statement of Cash Flows": version["filing_key"]},
                    frames[version["filing_key"]], run_id)
                context = conn.execute('''SELECT * FROM silver.financial_report_context
                    WHERE filing_key=%s ORDER BY processing_run_id DESC LIMIT 1''', (version['filing_key'],)).fetchone()
                if context is None:
                    part = frames[version['filing_key']]
                    end = pd.to_datetime(part['date'].str.split(' - ').str[-1], format='%d.%m.%Y').max().date()
                    context = read_report_context(part.iloc[0]['file_path'], version['fiscal_quarter'], end)
                store_context(conn, version['filing_key'], run_id, context)
                if index % 100 == 0 or index == len(versions):
                    logger.info("Restored cash-flow filing %s/%s", index, len(versions))
            financials._activate_filing_versions(conn, versions, run_id)
            mismatches = conn.execute("""SELECT COUNT(*) AS count
                FROM silver.current_financial_observations s
                JOIN bronze.statement_filings f USING (filing_key)
                WHERE s.statement_type='cash_flow' AND s.company_id=ANY(%s)
                  AND (s.fiscal_year,s.fiscal_quarter)<>(f.fiscal_year,f.fiscal_quarter)""",
                (company_ids,)).fetchone()["count"]
            if mismatches:
                raise ValueError(f"Cash-flow repair left {mismatches} observations under incorrect filing periods.")
            conn.execute("""INSERT INTO ops.data_quality_results
                (run_id,check_name,severity,affected_rows,details)
                VALUES (%s,'cash_flow_filing_period_mismatch','error',0,'{}')""", (run_id,))
            financials._run_finish(conn, run_id)
            conn.commit()
        except KeyboardInterrupt:
            conn.rollback()
            financials._mark_run_interrupted(run_id)
            raise
        except Exception as exc:
            conn.rollback()
            financials._run_finish(conn, run_id, exc)
            conn.commit()
            raise

    # Preserve each published company's existing inflation target and avoid
    # publishing companies whose Gold calculations have not yet been requested.
    targets = defaultdict(list)
    for row in published:
        targets[(row["inflation_base_year"], row["inflation_base_month"])].append(
            SimpleNamespace(symbol=row["symbol"]))
    gold_runs = []
    for (year, month), published_companies in targets.items():
        gold_runs.append(financials.calculate_gold_financials_postgres(
            year, month, companies=published_companies))
    logger.info("Cash-flow repair run %s restored %s filings for %s companies; Gold runs %s",
                run_id, len(versions), len(company_ids), gold_runs)
    return {"run_id": run_id, "filings": len(versions), "companies": len(company_ids), "gold_runs": gold_runs}
