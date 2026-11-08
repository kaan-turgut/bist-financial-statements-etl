"""Restore reported periods and measurement contexts from retained source files."""

import logging

import pandas as pd

from services import postgres_financial_pipeline as financials
from services.earnings_per_share import _rebuild, _refresh_published
from services.financial_report_context import read_report_context, store_context

logger = logging.getLogger(__name__)


def repair_financials_postgres(companies=None, *, rebuild_gold=True):
    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        condition, params = "", ()
        if companies is not None:
            condition, params = (
                " AND c.symbol=ANY(%s)",
                ([c.symbol for c in companies],),
            )
        selected = conn.execute(
            """SELECT DISTINCT c.company_id,c.symbol FROM metadata.companies c
            JOIN ops.active_filing_versions a USING(company_id) WHERE true"""
            + condition
            + " ORDER BY c.symbol",
            params,
        ).fetchall()
        ids = [c["company_id"] for c in selected]
        if not ids:
            raise ValueError("No active financial filings to repair.")
        published = _refresh_published(conn, ids) if rebuild_gold else []
        run_id = financials._run_start(
            conn, "financial_measurement_repair", {"company_ids": ids}
        )
        conn.commit()
        total, unknown = 0, 0
        try:
            for index, company in enumerate(selected, 1):
                rows = conn.execute(
                    """SELECT f.company_id AS _company_id,c.symbol AS company,
                        f.filing_key AS _filing_key,f.fiscal_year AS year,f.fiscal_quarter AS quarter,
                        f.statement_type,f.report_kind,f.filing_id,f.source_path AS file_path,
                        r.source_date AS date,r.section,r.line_item_path,r.item,r.raw_value AS value,
                        COALESCE(r.reporting_unit,1) AS "reporting unit",r.source_row_id,r.source_column_id,r.column_label
                    FROM ops.active_filing_versions a JOIN bronze.statement_filings f USING(filing_key)
                    JOIN metadata.companies c ON c.company_id=f.company_id
                    JOIN bronze.statement_rows r USING(filing_key)
                    WHERE a.company_id=%s ORDER BY f.filing_key,r.source_row_number""",
                    (company["company_id"],),
                ).fetchall()
                frame = pd.DataFrame(rows)
                frame["table"] = frame["statement_type"].map(
                    {v: k for k, v in financials.TABLE_TYPES.items()}
                )
                clean = financials._clean_frame(frame)
                contexts, versions = {}, []
                for key, part in clean.groupby("_filing_key", sort=False):
                    first = part.iloc[0]
                    end = (
                        pd.to_datetime(
                            part["date"].str.split(" - ").str[-1], format="%d.%m.%Y"
                        )
                        .max()
                        .date()
                    )
                    cache_key = (first["file_path"], int(first["quarter"]), end)
                    if cache_key not in contexts:
                        contexts[cache_key] = read_report_context(*cache_key)
                    context = contexts[cache_key]
                    financials._store_silver_rows(
                        conn,
                        company["company_id"],
                        {first["table"]: int(key)},
                        part,
                        run_id,
                    )
                    store_context(conn, int(key), run_id, context)
                    unknown += context["measurement_basis"] == "unknown"
                    versions.append(
                        {
                            "company_id": company["company_id"],
                            "statement_type": first["statement_type"],
                            "fiscal_year": int(first["year"]),
                            "fiscal_quarter": int(first["quarter"]),
                            "filing_key": int(key),
                            "filing_id": first["filing_id"],
                            "report_kind": first["report_kind"],
                            "processing_run_id": run_id,
                        }
                    )
                financials._activate_filing_versions(conn, versions, run_id)
                total += len(versions)
                if index % 10 == 0 or index == len(selected):
                    logger.info(
                        "Financial context repair %s/%s companies; %s filings, %s unknown bases",
                        index,
                        len(selected),
                        total,
                        unknown,
                    )
            conn.execute(
                """INSERT INTO ops.data_quality_results(run_id,check_name,severity,affected_rows)
                VALUES (%s,'financial_report_basis_unknown','warning',%s)""",
                (run_id, unknown),
            )
            financials._run_finish(conn, run_id)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            financials._run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return {
        "run_id": run_id,
        "filings": total,
        "unknown_bases": unknown,
        "gold_runs": _rebuild(published),
    }
