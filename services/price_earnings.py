"""Publish P/E independently or alongside normal Gold financial calculations."""

import logging
from collections import Counter, defaultdict

from psycopg.types.json import Jsonb

from models.price_earnings import price_earnings

logger = logging.getLogger(__name__)


def calculate_price_earnings(
    conn, valuation_run_id, *, financial_run_id=None, companies=None
):
    source = (
        "gold.current_eps_values" if financial_run_id is None else "gold.eps_values"
    )
    condition, params = "", []
    if financial_run_id is not None:
        condition += " AND e.calculation_run_id=%s"
        params.append(financial_run_id)
    if companies is not None:
        condition += " AND e.company_id IN (SELECT company_id FROM metadata.companies WHERE symbol=ANY(%s))"
        params.append([c.symbol for c in companies])
    rows = conn.execute(
        f"""SELECT e.*,f.period_end,f.period_end_count,
            i.instrument_id,i.currency,
            (SELECT COUNT(*) FROM metadata.instruments n WHERE n.company_id=e.company_id) AS instrument_count,
            p.price_date,b.price_row_id,b.source_payload
        FROM {source} e
        LEFT JOIN LATERAL (
            SELECT MAX(period_end) AS period_end,COUNT(DISTINCT period_end) AS period_end_count
            FROM gold.financial_values g WHERE g.calculation_run_id=e.calculation_run_id
            AND g.company_id=e.company_id AND g.fiscal_year=e.fiscal_year AND g.fiscal_quarter=e.fiscal_quarter
            AND g.statement_type='income_statement'
        ) f ON true
        LEFT JOIN metadata.instruments i ON i.company_id=e.company_id AND i.is_primary
        LEFT JOIN LATERAL (
            SELECT price_date,source FROM silver.daily_prices d
            WHERE e.value_ttm>0 AND d.instrument_id=i.instrument_id
              AND d.source='IsYatirim' AND d.price_date BETWEEN f.period_end-10 AND f.period_end
            ORDER BY price_date DESC LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
            SELECT price_row_id,source_payload FROM bronze.price_observations b
            WHERE b.instrument_id=i.instrument_id AND b.price_date=p.price_date AND b.source=p.source
            ORDER BY b.run_id DESC,b.price_row_id DESC LIMIT 1
        ) b ON true
        WHERE e.eps_kind='basic' AND e.share_class='ordinary' AND e.earnings_scope='total'
        {condition} ORDER BY e.company_id,e.fiscal_year,e.fiscal_quarter""",
        params,
    ).fetchall()
    source_ids = sorted(
        {s for row in rows for s in row["calculation_details"].get("source_ids", [])}
    )
    source_rows = conn.execute(
        """SELECT s.*,f.company_id,f.fiscal_year,f.fiscal_quarter,
            COALESCE(s.purchasing_power_date,CASE WHEN c.measurement_basis='inflation_adjusted'
                THEN c.purchasing_power_date END) AS purchasing_power_date
        FROM silver.eps_observations s JOIN bronze.eps_sources f USING(source_id)
        LEFT JOIN ops.active_filing_versions a ON a.filing_key=f.filing_key AND a.statement_type='income_statement'
        LEFT JOIN silver.financial_report_context c ON c.filing_key=a.filing_key AND c.processing_run_id=a.processing_run_id
        WHERE s.source_id=ANY(%s) AND s.eps_kind='basic' AND s.share_class='ordinary' AND s.earnings_scope='total'
        ORDER BY s.source_id,s.processing_run_id DESC""",
        (source_ids,),
    ).fetchall()
    sources = defaultdict(list)
    for s in source_rows:
        sources[s["source_id"]].append(s)
    reasons = Counter()
    available = 0
    # Recalculation within the same run replaces that run's own rows. Separate
    # standalone refreshes use new run IDs and retain their valuation history.
    conn.execute(
        "DELETE FROM gold.price_earnings_values WHERE valuation_run_id=%s AND company_id=ANY(%s)",
        (valuation_run_id, sorted({r["company_id"] for r in rows})),
    )
    with conn.cursor().copy("""COPY gold.price_earnings_values
        (valuation_run_id,financial_run_id,company_id,instrument_id,fiscal_year,fiscal_quarter,
         period_end,price_date,original_close_try,basic_eps_ttm,pe_ratio,price_row_id,calculation_details)
        FROM STDIN""") as copied:
        for row in rows:
            selected = []
            for source_id in row["calculation_details"].get("source_ids", []):
                eligible = [
                    s
                    for s in sources[source_id]
                    if s["processing_run_id"] <= valuation_run_id
                    and s["company_id"] == row["company_id"]
                ]
                if eligible:
                    version = eligible[0]["processing_run_id"]
                    selected.extend(
                        s for s in eligible if s["processing_run_id"] == version
                    )
            value = price_earnings(row, selected)
            reason = value["calculation_details"]["unavailable_reason"]
            if reason:
                reasons[reason] += 1
            else:
                available += 1
            copied.write_row(
                (
                    valuation_run_id,
                    row["calculation_run_id"],
                    row["company_id"],
                    row["instrument_id"],
                    row["fiscal_year"],
                    row["fiscal_quarter"],
                    row["period_end"],
                    row["price_date"],
                    value["original_close_try"],
                    value["basic_eps_ttm"],
                    value["pe_ratio"],
                    row["price_row_id"],
                    Jsonb(value["calculation_details"]),
                )
            )
    conn.execute(
        """INSERT INTO gold.financial_ratios
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_ttm)
        SELECT financial_run_id,company_id,fiscal_year,fiscal_quarter,'price_earnings',pe_ratio
        FROM gold.price_earnings_values WHERE valuation_run_id=%s
        ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
        DO UPDATE SET value_quarter=NULL,value_ytd=NULL,value_ttm=EXCLUDED.value_ttm,
          quarter_yoy_change=NULL,quarter_qoq_change=NULL,ytd_yoy_change=NULL,ttm_qoq_change=NULL,
          calculated_at=now()""",
        (valuation_run_id,),
    )
    conn.execute(
        """INSERT INTO ops.data_quality_results(run_id,check_name,severity,affected_rows,details)
        VALUES (%s,'price_earnings_unavailable','warning',%s,%s)""",
        (valuation_run_id, sum(reasons.values()), Jsonb(dict(reasons))),
    )
    logger.info(
        "P/E calculation: %s available of %s company-periods; unavailable reasons: %s",
        available,
        len(rows),
        dict(reasons),
    )
    return {
        "run_id": valuation_run_id,
        "periods": len(rows),
        "available": available,
        "unavailable": dict(reasons),
    }


def calculate_price_earnings_postgres(companies=None):
    from services import postgres_financial_pipeline as financials

    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        # Serialize standalone valuation refreshes and financial publication.
        conn.execute("SELECT pg_advisory_lock(hashtext('bist_finance_price_earnings'))")
        run_id = financials._run_start(
            conn,
            "price_earnings_calculation",
            {
                "symbols": [c.symbol for c in companies]
                if companies is not None
                else None,
                "price_policy": "original_close_on_or_before_actual_fiscal_quarter_end",
            },
        )
        conn.commit()
        try:
            result = calculate_price_earnings(conn, run_id, companies=companies)
            financials._run_finish(conn, run_id)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            financials._run_finish(conn, run_id, exc)
            conn.commit()
            raise
        finally:
            conn.execute(
                "SELECT pg_advisory_unlock(hashtext('bist_finance_price_earnings'))"
            )
    return result
