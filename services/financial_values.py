"""Load versioned Silver facts and publish period-aware Gold calculations."""

from datetime import date

from psycopg.types.json import Jsonb

from models.financial_values import CpiSeries, calculate_values
from read_statements.borrowing_paths import BORROWING_PATH_ITEMS, borrowing_path_overrides


def restore_borrowing_paths(conn, observations):
    """Restore legacy leaf hierarchy in memory; retain Bronze/Silver history."""
    filings = sorted({r["filing_key"] for r in observations})
    if not filings:
        return 0
    rows = conn.execute(
        """SELECT filing_key,source_row_id,item,line_item_path
        FROM bronze.statement_rows
        WHERE filing_key=ANY(%s) AND translate(lower(item), 'ıİşŞğĞüÜöÖçÇ', 'iissgguuoocc')=ANY(%s)
        ORDER BY filing_key,source_row_number""",
        (filings, list(BORROWING_PATH_ITEMS)),
    ).fetchall()
    overrides = borrowing_path_overrides(rows)
    changed = 0
    for row in observations:
        path = overrides.get((row["filing_key"], row["source_row_id"]))
        if path and path != row["line_item_path"]:
            row["legacy_line_item_path"] = row["line_item_path"]
            row["line_item_path"] = path
            changed += 1
    return changed


def calculate_financial_values(conn, run_id, year, month, companies, cash_balances):
    target = date(year, month, 1)
    cpi = CpiSeries(
        conn.execute(
            "SELECT observation_date,value FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'"
        ).fetchall()
    )
    if cpi.factor(target, target) is None:
        raise ValueError(f"No CPI data for target month {year}-{month:02d}.")
    condition, params = "", ()
    if companies is not None:
        condition = " AND s.company_id IN (SELECT company_id FROM metadata.companies WHERE symbol=ANY(%s))"
        params = ([c.symbol for c in companies],)
    observations = conn.execute(
        """SELECT s.*,COALESCE(c.measurement_basis,'unknown') AS measurement_basis,
            c.purchasing_power_date
        FROM silver.current_financial_observations s LEFT JOIN silver.financial_report_context c
         ON c.filing_key=s.filing_key AND c.processing_run_id=s.processing_run_id
        WHERE true"""
        + condition,
        params,
    ).fetchall()
    restored = restore_borrowing_paths(conn, observations)
    if restored:
        conn.execute("""INSERT INTO ops.data_quality_results
            (run_id,check_name,severity,affected_rows,details)
            VALUES (%s,'borrowing_counterparty_paths_restored','info',%s,%s)""",
            (run_id, restored, Jsonb({"source": "ordered_bronze_parent_rows"})))
    values = calculate_values(observations, cpi, target, cash_balances)
    labels = {
        (r["filing_key"], r["source_row_id"], r["source_column_id"]): r["column_label"]
        for r in conn.execute("""SELECT DISTINCT ON (filing_key,source_row_id,source_column_id)
                    filing_key,source_row_id,source_column_id,column_label
                FROM bronze.statement_rows WHERE source_column_id IS NOT NULL
                ORDER BY filing_key,source_row_id,source_column_id,source_row_number""").fetchall()
    }
    fx = {
        (r["year"], r["month"]): r["value"]
        for r in conn.execute("""
        SELECT DISTINCT ON (year,month) year,month,value FROM silver.monthly_macro_data
        WHERE series_code='USDTRY_MONTHLY_OPEN' ORDER BY year,month,loaded_at DESC,observation_date""").fetchall()
    }
    with (
        conn.cursor().copy("""COPY gold.financial_values
        (calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,
         source_column_id,column_label,value_quarter,value_ytd,value_real,value_usd,
         inflation_base_year,inflation_base_month,period_start,period_end,measurement_basis,
         purchasing_power_date,calculation_details,value_real_ytd) FROM STDIN""") as copied
    ):
        for row in values:
            end = row["period_end"]
            rate = fx.get((end.year, end.month))
            usd = (
                row["value_quarter"] / rate
                if row["value_quarter"] is not None and rate and rate > 0
                else None
            )
            details = row["calculation_details"]
            if "legacy_line_item_path" in row:
                details["source_line_item_path"] = row["legacy_line_item_path"]
                details["line_item_path_method"] = "restored_counterparty_branch_from_bronze"
            details["usd_method"] = "report_end_month_open_reference_rate"
            if usd is None and row["value_quarter"] is not None:
                details["usd_unavailable_reason"] = "missing_or_invalid_monthly_fx"
            label = labels.get(
                (row["filing_key"], row["source_row_id"], row["source_column_id"])
            )
            copied.write_row(
                (
                    run_id,
                    row["company_id"],
                    row["statement_type"],
                    row["fiscal_year"],
                    row["fiscal_quarter"],
                    row["line_item_path"],
                    row["item"],
                    row["source_column_id"],
                    label,
                    row["value_quarter"],
                    row["value_ytd"],
                    row["value_real"],
                    usd,
                    year,
                    month,
                    row["period_start"],
                    end,
                    row["measurement_basis"],
                    row.get("purchasing_power_date"),
                    Jsonb(details),
                    row["value_real_ytd"],
                )
            )


def calculate_value_changes(conn, run_id):
    conn.execute(
        """WITH previous AS MATERIALIZED (
        SELECT financial_value_id,value_real,fiscal_year,fiscal_quarter,period_end,measurement_basis,
            LAG(measurement_basis) OVER annual AS annual_basis,
            LAG(measurement_basis) OVER sequential AS preceding_basis,
            LAG(value_real) OVER annual AS annual_value,LAG(fiscal_year) OVER annual AS annual_year,
            LAG(period_end) OVER annual AS annual_end,
            LAG(value_real) OVER sequential AS preceding_value,
            LAG(fiscal_year*4+fiscal_quarter) OVER sequential AS preceding_period,
            LAG(period_end) OVER sequential AS preceding_end
        FROM gold.financial_values WHERE calculation_run_id=%s
        WINDOW annual AS (PARTITION BY company_id,statement_type,line_item_path,item,source_column_id,fiscal_quarter ORDER BY fiscal_year),
               sequential AS (PARTITION BY company_id,statement_type,line_item_path,item,source_column_id ORDER BY fiscal_year,fiscal_quarter)
    ) UPDATE gold.financial_values g SET
        value_real_yoy_change=CASE WHEN p.annual_year=p.fiscal_year-1 AND p.period_end-p.annual_end BETWEEN 350 AND 380
                                  AND p.measurement_basis=p.annual_basis
                                  THEN p.value_real/NULLIF(p.annual_value,0)-1 END,
        value_real_qoq_change=CASE WHEN p.preceding_period=p.fiscal_year*4+p.fiscal_quarter-1
                                       AND p.period_end-p.preceding_end BETWEEN 75 AND 100
                                       AND p.measurement_basis=p.preceding_basis
                                  THEN p.value_real/NULLIF(p.preceding_value,0)-1 END
        FROM previous p WHERE g.financial_value_id=p.financial_value_id AND g.calculation_run_id=%s""",
        (run_id, run_id),
    )
