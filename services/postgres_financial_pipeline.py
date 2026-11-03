"""PostgreSQL medallion ingestion for KAP financial statements."""

import hashlib
import json
import logging
import time
import zipfile
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from paths import FINANCIAL_STATEMENTS_DIR, PROJECT_ROOT
from read_statements.obtain_paths import obtain_statement_paths
from read_statements.parse_statement import parse_statement
from services.metadata_store import connect_metadata_db

logger = logging.getLogger(__name__)
_GOLD_RUN_ID = ContextVar("gold_run_id", default=None)

MIGRATION = PROJECT_ROOT / "database" / "migrations" / "007_create_medallion_architecture.sql"
TABLE_TYPES = {
    "Income Statement": "income_statement",
    "Balance Sheet": "balance_sheet",
    "Statement of Cash Flows": "cash_flow",
    "Statement of Change in Equity": "equity_change",
}


def _null_if_missing(value):
    return None if pd.isna(value) else value


def apply_medallion_migration():
    """Apply the versioned medallion DDL once, in a single transaction."""
    sql = MIGRATION.read_text(encoding="utf-8")
    with connect_metadata_db() as conn:
        migration_table = conn.execute(
            "SELECT to_regclass('ops.schema_migrations') AS table_name"
        ).fetchone()["table_name"]
        if migration_table and conn.execute(
            "SELECT 1 FROM ops.schema_migrations WHERE version = '007'"
        ).fetchone():
            logger.info("Medallion migration 007 is already applied")
            return False
        conn.execute(sql)
        conn.execute("INSERT INTO ops.schema_migrations (version) VALUES ('007') ON CONFLICT DO NOTHING")
    logger.info("Applied medallion migration 007")
    return True


def _mark_run_interrupted(run_id):
    with connect_metadata_db() as conn:
        _run_finish(conn, run_id, "Interrupted by keyboard interrupt")


def _run_start(conn, pipeline_name, parameters):
    row = conn.execute(
        """INSERT INTO ops.pipeline_runs (pipeline_name, status, parameters)
           VALUES (%s, 'running', %s::jsonb) RETURNING run_id""",
        (pipeline_name, json.dumps(parameters, default=str)),
    ).fetchone()
    return row["run_id"]


def _run_finish(conn, run_id, error=None):
    conn.execute(
        """UPDATE ops.pipeline_runs
           SET status = %s, finished_at = now(), error_text = %s WHERE run_id = %s""",
        ("failed" if error else "succeeded", str(error) if error else None, run_id),
    )


def _choose_active_filing_versions(candidates):
    """Keep one filing per company, statement, and period, matching parser priority."""
    report_kind_rank = {"Konsolide": 0, "Konsolide Olmayan": 1}
    selected = {}
    for row in candidates:
        key = (
            row["company_id"], row["statement_type"],
            row["fiscal_year"], row["fiscal_quarter"],
        )
        filing_id = row["filing_id"]
        rank = (
            report_kind_rank.get(row["report_kind"], 2),
            -(int(filing_id) if filing_id is not None else -1),
            -int(row["filing_key"]),
        )
        previous = selected.get(key)
        if previous is None or rank < previous[0]:
            selected[key] = (rank, row)
    return [row for _, row in selected.values()]


def _ensure_instruments(conn):
    conn.execute(
        """INSERT INTO metadata.instruments (company_id, symbol, is_primary)
           SELECT company_id, symbol, true FROM metadata.companies
           ON CONFLICT (symbol) DO UPDATE SET company_id = EXCLUDED.company_id"""
    )
    conn.execute(
        """INSERT INTO metadata.instruments (company_id, symbol, is_primary)
           SELECT c.company_id, s.symbol, false
           FROM metadata.share_info s JOIN metadata.companies c USING (company_id)
           ON CONFLICT (symbol) DO NOTHING"""
    )


def _sha256_member(path):
    archive, inner = _split_archive_path(path)
    digest = hashlib.sha256()
    with zipfile.ZipFile(archive) as zip_file, zip_file.open(inner) as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_archive_path(member_path):
    archive, inner = str(member_path).split(".zip/", 1)
    return Path(archive + ".zip"), inner


def _store_bronze_filing(conn, company_id, frame, path, run_id):
    digest = _sha256_member(path)
    filing_id = frame["filing_id"].dropna().iloc[0] if frame["filing_id"].notna().any() else None
    year = int(frame["year"].iloc[0])
    quarter = int(frame["quarter"].iloc[0])
    source_name = Path(path.split(".zip/", 1)[1]).name
    keys = {}
    for table_name, type_name in TABLE_TYPES.items():
        rows = frame.loc[frame["table"] == table_name]
        if rows.empty:
            continue
        kind = rows["report_kind"].dropna().iloc[0] if rows["report_kind"].notna().any() else None
        filing = conn.execute(
            """INSERT INTO bronze.statement_filings
               (company_id, filing_id, fiscal_year, fiscal_quarter, statement_type,
                report_kind, source_filename, source_path, source_sha256, run_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, fiscal_year, fiscal_quarter, statement_type, source_sha256)
               DO UPDATE SET filing_id = EXCLUDED.filing_id
               RETURNING filing_key""",
            (company_id, filing_id, year, quarter, type_name, kind, source_name,
             str(path), digest, run_id),
        ).fetchone()
        keys[table_name] = filing["filing_key"]
        for row_number, (_, row) in enumerate(rows.iterrows()):
            reporting_unit = row["reporting unit"]
            reporting_unit = 1.0 if pd.isna(reporting_unit) else float(reporting_unit)
            payload = {
                "company": row["company"], "year": int(row["year"]), "quarter": int(row["quarter"]),
                "table": row["table"], "section": _null_if_missing(row["section"]),
                "line_item_path": _null_if_missing(row["line_item_path"]),
                "item": _null_if_missing(row["item"]),
                "value": _null_if_missing(row["value"]),
                "reporting_unit": reporting_unit,
                "report_kind": _null_if_missing(row["report_kind"]),
            }
            conn.execute(
                """INSERT INTO bronze.statement_rows
                   (filing_key, source_row_number, source_row_id, source_column_id,
                    column_label, source_date, section, line_item_path, item, raw_value,
                    reporting_unit, raw_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT DO NOTHING""",
                (filing["filing_key"], row_number, _null_if_missing(row["source_row_id"]),
                 _null_if_missing(row["source_column_id"]), _null_if_missing(row["column_label"]),
                 row["date"], _null_if_missing(row["section"]), _null_if_missing(row["line_item_path"]), row["item"],
                 _null_if_missing(row["value"]), payload["reporting_unit"],
                 json.dumps(payload, ensure_ascii=False, allow_nan=False)),
            )
    return keys


def _store_silver_rows(conn, company_id, filing_keys, clean_frame, run_id):
    for table_name, type_name in TABLE_TYPES.items():
        filing_key = filing_keys.get(table_name)
        if not filing_key:
            continue
        rows = clean_frame.loc[clean_frame["table"] == table_name]
        for _, row in rows.iterrows():
            dates = str(row["date"]).split(" - ")
            start = datetime.strptime(dates[0], "%d.%m.%Y").replace(tzinfo=UTC).date()
            end = datetime.strptime(dates[-1], "%d.%m.%Y").replace(tzinfo=UTC).date()
            quarter = int(row["quarter"])
            is_cumulative = quarter == 1234 or (
                type_name == "cash_flow" and (end - start).days > 100
            )
            if is_cumulative:
                quarter = 4
            path_parts = [part.strip() for part in str(row["line_item_path"] or "").split(" > ") if part.strip()]
            subsection = path_parts[-2] if len(path_parts) > 2 else None
            line_item_path = row["line_item_path"]
            if pd.isna(line_item_path) or not str(line_item_path).strip():
                line_item_path = row["item"]
            conn.execute(
                """INSERT INTO silver.financial_observations
                   (company_id, filing_key, processing_run_id, statement_type, fiscal_year, fiscal_quarter,
                    period_start, period_end, section, subsection, line_item_path, item,
                    amount, is_cumulative, report_kind, source_row_id, source_column_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (company_id, filing_key, run_id, type_name, int(row["year"]), quarter, start, end,
                 _null_if_missing(row["section"]), subsection, line_item_path, row["item"], float(row["value"]),
                 is_cumulative, _null_if_missing(row["report_kind"]),
                 _null_if_missing(row["source_row_id"]), _null_if_missing(row["source_column_id"])),
            )


def _clean_frame(frame):
    """Apply the established parser cleaning rules without mutating the source frame."""
    from models.financial_statement import FinancialStatement

    copy = frame.copy()
    # `clean` is currently the authoritative cleaning logic in this project.
    # Use a minimal holder to share it while keeping the parsed Bronze frame intact.
    holder = object.__new__(FinancialStatement)
    holder.df = copy
    holder.clean()
    return holder.df


def ingest_statements_postgres(companies, begin_year, begin_quarter, end_year, end_quarter):
    """Parse local KAP archives into immutable Bronze and canonical Silver rows."""
    paths_by_symbol = {
        company.symbol: obtain_statement_paths(
            FINANCIAL_STATEMENTS_DIR / company.symbol,
            begin_year, begin_quarter, end_year, end_quarter,
        ) for company in companies
    }
    paths = [(company, path) for company in companies for path in paths_by_symbol[company.symbol]]
    if not paths:
        raise FileNotFoundError("No local KAP statement archives found for the requested period.")
    companies_with_files = sum(bool(company_paths) for company_paths in paths_by_symbol.values())
    logger.info(
        "Found %s local statement files across %s of %s companies; parsing begins",
        len(paths), companies_with_files, len(companies),
    )

    parsed = []
    for company, path in paths:
        logger.info("Parsing %s", path)
        statement = parse_statement(path, company)
        parsed.append((company, path, statement.df.copy()))

    logger.info("Cleaning and selecting canonical rows from %s parsed files", len(parsed))
    canonical_parts = []
    from read_statements.canonical import choose_canonical_rows
    for company, _, frame in parsed:
        cleaned = _clean_frame(frame)
        if not cleaned.empty:
            canonical_parts.append(cleaned)
    canonical = choose_canonical_rows(
        pd.concat(canonical_parts, ignore_index=True) if canonical_parts else pd.DataFrame()
    )
    logger.info("Canonical row selection complete: %s rows", len(canonical))

    with connect_metadata_db() as conn:
        _ensure_instruments(conn)
        run_id = _run_start(conn, "financial_statement_ingestion", {
            "symbols": [company.symbol for company in companies],
            "begin": [begin_year, begin_quarter], "end": [end_year, end_quarter],
        })
        try:
            company_ids = {
                row["symbol"]: row["company_id"]
                for row in conn.execute(
                    "SELECT symbol, company_id FROM metadata.companies WHERE symbol = ANY(%s)",
                    ([company.symbol for company in companies],),
                ).fetchall()
            }
            conn.commit()
            logger.info("Writing Bronze and Silver records for %s parsed files", len(parsed))
            for index, (company, path, raw) in enumerate(parsed, start=1):
                try:
                    company_id = company_ids.get(company.symbol)
                    if company_id is None:
                        raise ValueError(f"No PostgreSQL metadata company for {company.symbol}")
                    keys = _store_bronze_filing(conn, company_id, raw, path, run_id)
                    selected = canonical.loc[
                        (canonical["company"] == company.symbol)
                        & (canonical["file_path"] == path)
                    ] if not canonical.empty else canonical
                    selected = selected.drop_duplicates()
                    _store_silver_rows(conn, company_id, keys, selected, run_id)
                    if index % 25 == 0 or index == len(parsed):
                        logger.info("Stored Bronze/Silver records for %s/%s files", index, len(parsed))
                except Exception:
                    conn.rollback()
                    raise
            candidates = conn.execute(
                """SELECT DISTINCT s.company_id, s.statement_type, s.fiscal_year,
                          s.fiscal_quarter, s.filing_key, s.processing_run_id,
                          f.report_kind, f.filing_id
                   FROM silver.financial_observations s
                   JOIN bronze.statement_filings f USING (filing_key)
                   WHERE s.processing_run_id=%s""",
                (run_id,),
            ).fetchall()
            active_versions = _choose_active_filing_versions(candidates)
            logger.info("Activating %s selected filing versions", len(active_versions))
            for row in active_versions:
                conn.execute(
                    """INSERT INTO ops.active_filing_versions
                       (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_id,statement_type,fiscal_year,fiscal_quarter)
                       DO UPDATE SET filing_key=EXCLUDED.filing_key,
                                     processing_run_id=EXCLUDED.processing_run_id,activated_at=now()""",
                    (row["company_id"], row["statement_type"], row["fiscal_year"],
                     row["fiscal_quarter"], row["filing_key"], row["processing_run_id"]),
                )
            conn.execute(
                """DELETE FROM silver.financial_observations s
                   USING ops.active_filing_versions a
                   WHERE a.processing_run_id=%s
                     AND s.company_id=a.company_id
                     AND s.statement_type=a.statement_type
                     AND s.fiscal_year=a.fiscal_year
                     AND s.fiscal_quarter=a.fiscal_quarter
                     AND (s.processing_run_id<>a.processing_run_id OR s.filing_key<>a.filing_key)""",
                (run_id,),
            )
            conn.execute(
                """DELETE FROM bronze.statement_filings f
                   USING ops.active_filing_versions a
                   WHERE a.processing_run_id=%s
                     AND f.company_id=a.company_id
                     AND f.statement_type=a.statement_type
                     AND f.fiscal_year=a.fiscal_year
                     AND f.fiscal_quarter=a.fiscal_quarter
                     AND f.filing_key<>a.filing_key""",
                (run_id,),
            )
            _run_finish(conn, run_id)
            conn.commit()
        except KeyboardInterrupt:
            conn.rollback()
            _mark_run_interrupted(run_id)
            raise
        except Exception as exc:
            conn.rollback()
            _run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return len(parsed)


def _calculate_gold_financials_postgres(price_level_year, price_level_month, *, companies=None):
    """Build quarterly values and rolling TTM totals from Silver observations."""
    with connect_metadata_db() as conn:
        calculation_started = time.perf_counter()
        run_id = _run_start(conn, "gold_financial_calculation", {
            "price_level": [price_level_year, price_level_month],
            "symbols": [company.symbol for company in companies] if companies else None,
        })
        _GOLD_RUN_ID.set(run_id)
        conn.commit()
        logger.info(
            "Gold calculation run %s started (price level %s-%02d)",
            run_id, price_level_year, price_level_month,
        )
        company_filter = ""
        params = []
        if companies:
            company_filter = " AND s.company_id = ANY(%s)"
            symbols = [company.symbol for company in companies]
            company_ids = [row["company_id"] for row in conn.execute(
                "SELECT company_id FROM metadata.companies WHERE symbol = ANY(%s)", (symbols,)
            ).fetchall()]
            params.append(company_ids)
        params.extend((price_level_year, price_level_month))
        stage_started = time.perf_counter()
        logger.info("Gold stage 1/6: calculating quarterly, YTD, inflation-adjusted, and USD values")
        conn.execute("""WITH silver_candidates AS (
            SELECT s.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY company_id,statement_type,fiscal_year,fiscal_quarter,
                                  line_item_path,item,source_column_id
                     ORDER BY is_cumulative,period_start DESC,source_row_id NULLS LAST,filing_key
                   ) AS candidate_rank
            FROM silver.current_financial_observations s WHERE true""" + company_filter + """
        ), normalized AS (
            SELECT s.*,
                   CASE WHEN s.is_cumulative THEN
                       s.amount - COALESCE(
                         (SELECT p.amount FROM silver_candidates p
                          WHERE p.company_id=s.company_id AND p.statement_type=s.statement_type
                            AND p.fiscal_year=s.fiscal_year AND p.fiscal_quarter < s.fiscal_quarter
                            AND p.is_cumulative AND p.line_item_path=s.line_item_path AND p.item=s.item
                            AND p.candidate_rank=1
                            AND p.source_column_id IS NOT DISTINCT FROM s.source_column_id
                          ORDER BY p.fiscal_quarter DESC LIMIT 1),
                         (SELECT CASE WHEN COUNT(DISTINCT p.fiscal_quarter)=s.fiscal_quarter-1
                                      THEN SUM(p.amount) END
                          FROM silver_candidates p
                          WHERE p.company_id=s.company_id AND p.statement_type=s.statement_type
                            AND p.fiscal_year=s.fiscal_year AND p.fiscal_quarter < s.fiscal_quarter
                            AND p.candidate_rank=1
                            AND p.line_item_path=s.line_item_path AND p.item=s.item
                            AND p.source_column_id IS NOT DISTINCT FROM s.source_column_id),
                         CASE WHEN s.fiscal_quarter=1 THEN 0 END)
                       ELSE s.amount END AS quarter_amount
            FROM silver_candidates s WHERE s.candidate_rank=1
        ), with_ytd AS (
            SELECT n.*,
                   CASE WHEN statement_type IN ('income_statement','cash_flow') THEN
                     SUM(quarter_amount) OVER (PARTITION BY company_id, statement_type, fiscal_year,
                       line_item_path, item, source_column_id ORDER BY fiscal_quarter)
                     ELSE quarter_amount END AS ytd_amount
            FROM normalized n
        ), cpi_index AS (
            SELECT observation_date,
                   EXP(SUM(LN(1 + value / 100.0)) OVER (ORDER BY observation_date)) AS price_index
            FROM silver.monthly_macro_data WHERE series_code = 'CPI_MONTHLY_RATE'
        ), target_index AS (
            SELECT target.base_date,
                   (SELECT price_index FROM cpi_index
                     WHERE observation_date = target.base_date) AS price_index
            FROM (SELECT make_date(%s, %s, 1) AS base_date) target
        ), enriched AS (
            SELECT w.*,
                   source_label.column_label,
                   t.price_index / source_cpi.price_index AS inflation_factor,
                   fx.value AS usd_try,
                   t.base_date
            FROM with_ytd w CROSS JOIN target_index t
            LEFT JOIN LATERAL (
                SELECT br.column_label
                FROM bronze.statement_rows br
                WHERE (w.source_row_id IS NOT NULL OR w.source_column_id IS NOT NULL)
                  AND br.filing_key = w.filing_key
                  AND br.source_row_id IS NOT DISTINCT FROM w.source_row_id
                  AND br.source_column_id IS NOT DISTINCT FROM w.source_column_id
                  AND br.column_label IS NOT NULL
                ORDER BY br.source_row_number
                LIMIT 1
            ) source_label ON TRUE
            LEFT JOIN cpi_index source_cpi
              ON source_cpi.observation_date = make_date(w.fiscal_year, w.fiscal_quarter * 3, 1)
            LEFT JOIN silver.monthly_macro_data fx
              ON fx.series_code = 'USDTRY_MONTHLY_OPEN'
             AND fx.observation_date = make_date(w.fiscal_year, w.fiscal_quarter * 3, 1)
        )
        INSERT INTO gold.financial_values
            (calculation_run_id, company_id, statement_type, fiscal_year, fiscal_quarter, line_item_path, item,
             source_column_id, column_label, value_quarter, value_ytd, value_ttm, value_real, value_real_ttm,
             value_usd, inflation_base_year, inflation_base_month)
            SELECT %s, company_id, statement_type, fiscal_year, fiscal_quarter, line_item_path, item,
                   source_column_id, column_label, quarter_amount, ytd_amount, NULL,
                   quarter_amount * inflation_factor, NULL,
                   CASE WHEN usd_try > 0 THEN quarter_amount / usd_try END,
                   EXTRACT(YEAR FROM base_date)::integer,
                   EXTRACT(MONTH FROM base_date)::smallint
            FROM enriched
            ON CONFLICT (calculation_run_id, company_id, statement_type, fiscal_year, fiscal_quarter,
                         line_item_path, item, source_column_id)
            DO UPDATE SET value_quarter=EXCLUDED.value_quarter, value_ytd=EXCLUDED.value_ytd,
                          value_real=EXCLUDED.value_real, value_usd=EXCLUDED.value_usd,
                          inflation_base_year=EXCLUDED.inflation_base_year,
                          inflation_base_month=EXCLUDED.inflation_base_month,
                          calculated_at=now()""", [*params, run_id])
        logger.info("Gold stage 1/6 complete in %.1f seconds", time.perf_counter() - stage_started)
        # No sums are published as TTM until all four consecutive quarters exist.
        stage_started = time.perf_counter()
        logger.info("Gold stage 2/6: calculating TTM values")
        conn.execute("""WITH rolling AS (
            SELECT g.calculation_run_id, g.company_id, g.statement_type, g.fiscal_year, g.fiscal_quarter,
                   g.line_item_path, g.item, g.source_column_id,
                   CASE WHEN COUNT(p.value_quarter)=4 THEN SUM(p.value_quarter) END AS ttm,
                   CASE WHEN COUNT(p.value_real)=4 THEN SUM(p.value_real) END AS real_ttm
            FROM gold.financial_values g
            LEFT JOIN gold.financial_values p
              ON p.calculation_run_id=g.calculation_run_id
             AND p.company_id=g.company_id AND p.statement_type=g.statement_type
             AND p.line_item_path=g.line_item_path AND p.item=g.item
             AND p.source_column_id IS NOT DISTINCT FROM g.source_column_id
             AND (p.fiscal_year*4+p.fiscal_quarter) BETWEEN
                 (g.fiscal_year*4+g.fiscal_quarter-3) AND (g.fiscal_year*4+g.fiscal_quarter)
            WHERE g.calculation_run_id=%s AND g.statement_type='income_statement'
            GROUP BY g.calculation_run_id,g.company_id,g.statement_type,g.fiscal_year,g.fiscal_quarter,
                     g.line_item_path,g.item,g.source_column_id
        ) UPDATE gold.financial_values g SET value_ttm=r.ttm, value_real_ttm=r.real_ttm,
                                               calculated_at=now()
          FROM rolling r WHERE g.calculation_run_id=r.calculation_run_id
            AND (g.company_id,g.statement_type,g.fiscal_year,g.fiscal_quarter,
                                g.line_item_path,g.item,g.source_column_id) =
                               (r.company_id,r.statement_type,r.fiscal_year,r.fiscal_quarter,
                                r.line_item_path,r.item,r.source_column_id)""", (run_id,))
        logger.info("Gold stage 2/6 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 3/6: calculating real-value year-over-year and quarter-over-quarter changes")
        conn.execute("""WITH changes AS (
            SELECT financial_value_id,
                   value_real / NULLIF(LAG(value_real) OVER (
                     PARTITION BY company_id,statement_type,line_item_path,item,source_column_id,fiscal_quarter
                     ORDER BY fiscal_year),0)-1 AS yoy,
                   value_real / NULLIF(LAG(value_real) OVER (
                     PARTITION BY company_id,statement_type,line_item_path,item,source_column_id
                     ORDER BY fiscal_year,fiscal_quarter),0)-1 AS qoq,
                   value_real_ttm / NULLIF(LAG(value_real_ttm) OVER (
                     PARTITION BY company_id,statement_type,line_item_path,item,source_column_id
                     ORDER BY fiscal_year,fiscal_quarter),0)-1 AS ttm_qoq
            FROM gold.financial_values WHERE calculation_run_id=%s
        ) UPDATE gold.financial_values g SET value_real_yoy_change=c.yoy,
             value_real_qoq_change=c.qoq,
             value_real_ttm_qoq_change=CASE WHEN g.statement_type='income_statement' THEN c.ttm_qoq END,
             calculated_at=now()
          FROM changes c WHERE g.calculation_run_id=%s
            AND g.financial_value_id=c.financial_value_id""", (run_id, run_id))
        logger.info("Gold stage 3/6 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 4/6: calculating financial ratios")
        conn.execute("""WITH facts AS (
            SELECT company_id, fiscal_year, fiscal_quarter,
                SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_q,
                SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_ytd,
                SUM(value_ttm) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_ttm,
                SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_q,
                SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_ytd,
                SUM(value_ttm) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_ttm,
                SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_q,
                SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_ytd,
                SUM(value_ttm) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_ttm,
                SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_q,
                SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_ytd,
                SUM(value_ttm) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_ttm,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam dönen varlıklar') AS current_assets,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam kısa vadeli yükümlülükler') AS current_liabilities,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='nakit ve nakit benzerleri') AS cash,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam yükümlülükler') AS liabilities,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam varlıklar') AS assets,
                SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam özkaynaklar') AS equity
            FROM gold.financial_values WHERE calculation_run_id=%s
            GROUP BY company_id, fiscal_year, fiscal_quarter
        ), shares AS (
            SELECT c.company_id, COALESCE(NULLIF(SUM(s.total_shares),0), c.paid_in_capital) AS shares
            FROM metadata.companies c LEFT JOIN metadata.share_info s USING (company_id)
            GROUP BY c.company_id, c.paid_in_capital
        ), ratios AS (
            SELECT f.company_id, f.fiscal_year, f.fiscal_quarter, v.ratio_code,
                   v.q_value, v.ytd_value, v.ttm_value
            FROM facts f LEFT JOIN shares sh USING (company_id)
            CROSS JOIN LATERAL (VALUES
              ('gross_margin', (sales_q-ABS(cogs_q))/NULLIF(sales_q,0), (sales_ytd-ABS(cogs_ytd))/NULLIF(sales_ytd,0), (sales_ttm-ABS(cogs_ttm))/NULLIF(sales_ttm,0)),
              ('operating_margin', op_q/NULLIF(sales_q,0), op_ytd/NULLIF(sales_ytd,0), op_ttm/NULLIF(sales_ttm,0)),
              ('return_on_assets', profit_q/NULLIF(assets,0), profit_ytd/NULLIF(assets,0), profit_ttm/NULLIF(assets,0)),
              ('return_on_equity', profit_q/NULLIF(equity,0), profit_ytd/NULLIF(equity,0), profit_ttm/NULLIF(equity,0)),
              ('current_ratio', current_assets/NULLIF(current_liabilities,0), NULL::numeric, NULL::numeric),
              ('cash_ratio', cash/NULLIF(current_liabilities,0), NULL::numeric, NULL::numeric),
              ('debt_ratio', liabilities/NULLIF(assets,0), NULL::numeric, NULL::numeric),
              ('debt_to_equity_ratio', liabilities/NULLIF(equity,0), NULL::numeric, NULL::numeric),
              ('asset_turnover_ratio', sales_q/NULLIF(assets,0), sales_ytd/NULLIF(assets,0), sales_ttm/NULLIF(assets,0)),
              ('eps', profit_q/NULLIF(sh.shares,0), profit_ytd/NULLIF(sh.shares,0), profit_ttm/NULLIF(sh.shares,0))
            ) AS v(ratio_code,q_value,ytd_value,ttm_value)
        )
        INSERT INTO gold.financial_ratios
          (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_quarter,value_ytd,value_ttm)
        SELECT %s,company_id,fiscal_year,fiscal_quarter,ratio_code,q_value,ytd_value,ttm_value FROM ratios
        ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
        DO UPDATE SET value_quarter=EXCLUDED.value_quarter,value_ytd=EXCLUDED.value_ytd,
                      value_ttm=EXCLUDED.value_ttm,calculated_at=now()""", (run_id, run_id))
        logger.info("Gold stage 4/6 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 5/6: calculating ratio changes")
        conn.execute("""WITH changes AS (
            SELECT company_id,fiscal_year,fiscal_quarter,ratio_code,
                   value_quarter/NULLIF(LAG(value_quarter) OVER
                     (PARTITION BY company_id,ratio_code,fiscal_quarter ORDER BY fiscal_year),0)-1 AS q_yoy,
                   value_quarter/NULLIF(LAG(value_quarter) OVER
                     (PARTITION BY company_id,ratio_code ORDER BY fiscal_year,fiscal_quarter),0)-1 AS q_qoq,
                   value_ytd/NULLIF(LAG(value_ytd) OVER
                     (PARTITION BY company_id,ratio_code,fiscal_quarter ORDER BY fiscal_year),0)-1 AS ytd_yoy,
                   value_ttm/NULLIF(LAG(value_ttm) OVER
                     (PARTITION BY company_id,ratio_code ORDER BY fiscal_year,fiscal_quarter),0)-1 AS ttm_qoq
            FROM gold.financial_ratios WHERE calculation_run_id=%s
        ) UPDATE gold.financial_ratios g SET quarter_yoy_change=c.q_yoy,
             quarter_qoq_change=c.q_qoq,ytd_yoy_change=c.ytd_yoy,ttm_qoq_change=c.ttm_qoq
          FROM changes c WHERE g.calculation_run_id=%s AND
                             (g.company_id,g.fiscal_year,g.fiscal_quarter,g.ratio_code)=
                               (c.company_id,c.fiscal_year,c.fiscal_quarter,c.ratio_code)""",
            (run_id, run_id))
        logger.info("Gold stage 5/6 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 6/6: recording data-quality results and publishing active calculation run")
        conn.execute("""INSERT INTO ops.data_quality_results
            (run_id,check_name,severity,affected_rows,details)
            SELECT %s,'financial_rows_missing_cpi','warning',COUNT(*),
                   jsonb_build_object('price_level_year',%s,'price_level_month',%s)
            FROM gold.financial_values WHERE calculation_run_id=%s AND value_real IS NULL""",
            (run_id, price_level_year, price_level_month, run_id))
        conn.execute("""DELETE FROM gold.financial_ratios old
            WHERE old.calculation_run_id<>%s
              AND EXISTS (
                SELECT 1 FROM gold.financial_ratios current
                WHERE current.calculation_run_id=%s
                  AND current.company_id=old.company_id
                  AND current.fiscal_year=old.fiscal_year
                  AND current.fiscal_quarter=old.fiscal_quarter
              )""", (run_id, run_id))
        conn.execute("""DELETE FROM gold.financial_values old
            WHERE old.calculation_run_id<>%s
              AND EXISTS (
                SELECT 1 FROM gold.financial_values current
                WHERE current.calculation_run_id=%s
                  AND current.company_id=old.company_id
                  AND current.fiscal_year=old.fiscal_year
                  AND current.fiscal_quarter=old.fiscal_quarter
              )""", (run_id, run_id))
        conn.execute("""INSERT INTO ops.active_company_versions (dataset_name,company_id,run_id)
            SELECT 'financials',company_id,%s FROM gold.financial_values
            WHERE calculation_run_id=%s GROUP BY company_id
            ON CONFLICT (dataset_name,company_id) DO UPDATE
            SET run_id=EXCLUDED.run_id,activated_at=now()""", (run_id, run_id))
        _run_finish(conn, run_id)
        logger.info("Gold stage 6/6 complete in %.1f seconds", time.perf_counter() - stage_started)
    _GOLD_RUN_ID.set(None)
    logger.info(
        "Gold calculation run %s completed in %.1f seconds",
        run_id, time.perf_counter() - calculation_started,
    )
    return run_id


def calculate_gold_financials_postgres(price_level_year, price_level_month, *, companies=None):
    try:
        return _calculate_gold_financials_postgres(
            price_level_year, price_level_month, companies=companies
        )
    except KeyboardInterrupt:
        run_id = _GOLD_RUN_ID.get()
        if run_id is not None:
            logger.warning(
                "Gold calculation run %s interrupted; the in-progress Gold transaction will be rolled back",
                run_id,
            )
            _mark_run_interrupted(run_id)
        _GOLD_RUN_ID.set(None)
        raise
    except Exception as exc:
        run_id = _GOLD_RUN_ID.get()
        if run_id is not None:
            with connect_metadata_db() as conn:
                _run_finish(conn, run_id, exc)
        _GOLD_RUN_ID.set(None)
        raise


def resolve_postgres_inflation_period(requested=None):
    if requested is not None:
        year, month = map(int, requested)
    else:
        with connect_metadata_db() as conn:
            row = conn.execute(
                """SELECT year,month FROM silver.monthly_macro_data
                   WHERE series_code='CPI_MONTHLY_RATE'
                   ORDER BY observation_date DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            raise ValueError("No CPI data found in PostgreSQL. Run update-reference-data-postgres first.")
        year, month = row["year"], row["month"]
    if not 1 <= month <= 12:
        raise ValueError("Inflation target month must be between 1 and 12.")
    with connect_metadata_db() as conn:
        exists = conn.execute(
            """SELECT 1 FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'
               AND year=%s AND month=%s""", (year, month)
        ).fetchone()
    if not exists:
        raise ValueError(f"No CPI data for target month {year}-{month:02d}.")
    return year, month


def update_database_postgres(companies, begin_year, begin_quarter, end_year, end_quarter,
                              price_level=None):
    if (begin_year * 4 + begin_quarter) > (end_year * 4 + end_quarter):
        raise ValueError("The begin period must not be later than the end period.")
    target = resolve_postgres_inflation_period(price_level)
    logger.info("Starting local statement ingestion; no downloads will be attempted")
    parsed_count = ingest_statements_postgres(
        companies, begin_year, begin_quarter, end_year, end_quarter
    )
    logger.info("Statement ingestion committed: %s files parsed", parsed_count)
    logger.info("Starting Gold financial calculations")
    run_id = calculate_gold_financials_postgres(*target, companies=companies)
    logger.info("Gold financial calculations committed under run %s", run_id)
    return parsed_count, run_id
