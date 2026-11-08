"""PostgreSQL medallion ingestion for KAP financial statements."""

import hashlib
import json
import logging
import time
import zipfile
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

import pandas as pd

from paths import FINANCIAL_STATEMENTS_DIR, PROJECT_ROOT
from read_statements.obtain_paths import obtain_statement_paths
from read_statements.parse_statement import parse_statement
from services.financial_ratios import (
    calculate_financial_ratios,
    calculate_ratio_changes,
)
from services.metadata_store import connect_metadata_db

logger = logging.getLogger(__name__)
_GOLD_RUN_ID = ContextVar("gold_run_id", default=None)

MIGRATIONS = (
    ("007", "007_create_medallion_architecture.sql"),
    ("008", "008_remove_financial_value_ttm.sql"),
    ("009", "009_create_eps_observations.sql"),
    ("010", "010_financial_measurement_context.sql"),
    ("011", "011_price_earnings.sql"),
    ("012", "012_structured_eps_sources.sql"),
    ("013", "013_eps_coverage.sql"),
    ("014", "014_eps_refresh_snapshots.sql"),
)
TABLE_TYPES = {
    "Income Statement": "income_statement",
    "Balance Sheet": "balance_sheet",
    "Statement of Cash Flows": "cash_flow",
    "Statement of Change in Equity": "equity_change",
}
CASH_BALANCE_ITEMS = (
    "dönem başı nakit ve nakit benzerleri",
    "dönem sonu nakit ve nakit benzerleri",
    "dönem başındaki nakit ve nakde eşdeğer varlıklar",
    "dönem sonundaki nakit ve nakde eşdeğer varlıklar",
    "dönem başındaki nakit ve nakit benzerleri mevcudu",
    "dönem sonundaki nakit ve nakit benzerleri mevcudu",
)


def _null_if_missing(value):
    return None if pd.isna(value) else value


def apply_medallion_migration():
    """Apply pending medallion migrations in order, in a single transaction."""
    applied = False
    with connect_metadata_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('bist_finance_medallion_migrations'))")
        migration_table = conn.execute(
            "SELECT to_regclass('ops.schema_migrations') AS table_name"
        ).fetchone()["table_name"]
        for version, filename in MIGRATIONS:
            if migration_table and conn.execute(
                "SELECT 1 FROM ops.schema_migrations WHERE version = %s", (version,)
            ).fetchone():
                continue
            sql = (PROJECT_ROOT / "database" / "migrations" / filename).read_text(encoding="utf-8")
            conn.execute(sql)
            conn.execute("INSERT INTO ops.schema_migrations (version) VALUES (%s)", (version,))
            migration_table = True
            applied = True
            logger.info("Applied medallion migration %s", version)
    return applied


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
        filing_id = _null_if_missing(row["filing_id"])
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
            # 1234 denotes an annual income statement, not every YTD report.
            # Keep the filing's fiscal quarter, including non-calendar fiscal years.
            is_cumulative = quarter == 1234 or (
                type_name in {"cash_flow", "income_statement"} and (quarter == 1 or (end - start).days > 100)
                and row["item"] not in CASH_BALANCE_ITEMS
            )
            if quarter == 1234:
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


def _activate_filing_versions(conn, candidates, run_id):
    """Publish selected filings, retaining source and processing history."""
    # An old Q2/Q3 cash-flow filing may still occupy the erroneous Q4 slot.
    # Remove that pointer only after its actual period has replacement rows.
    conn.execute("""DELETE FROM ops.active_filing_versions a
        USING bronze.statement_filings f
        WHERE a.filing_key=f.filing_key AND a.statement_type='cash_flow'
          AND (a.fiscal_year,a.fiscal_quarter)<>(f.fiscal_year,f.fiscal_quarter)
          AND EXISTS (
            SELECT 1 FROM silver.financial_observations s
            WHERE s.processing_run_id=%s AND s.company_id=a.company_id
              AND s.statement_type='cash_flow' AND s.fiscal_year=f.fiscal_year
              AND s.fiscal_quarter=f.fiscal_quarter)""", (run_id,))
    versions = _choose_active_filing_versions(candidates)
    logger.info("Activating %s selected filing versions", len(versions))
    for row in versions:
        conn.execute("""INSERT INTO ops.active_filing_versions
            (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (company_id,statement_type,fiscal_year,fiscal_quarter)
            DO UPDATE SET filing_key=EXCLUDED.filing_key,
                          processing_run_id=EXCLUDED.processing_run_id,activated_at=now()""",
            (row["company_id"], row["statement_type"], row["fiscal_year"],
             row["fiscal_quarter"], row["filing_key"], row["processing_run_id"]))


def _parse_statement_frame(company, path):
    """Spawn-safe worker: parse a local archive without opening a database connection."""
    return parse_statement(path, company).df.copy()


def _validate_parse_workers(parse_workers):
    if type(parse_workers) is not int or parse_workers < 1:
        raise ValueError("parse_workers must be a positive integer.")


def _parse_statement_frames(paths, parse_workers):
    """Keep source order and at most two tasks per worker in flight."""
    _validate_parse_workers(parse_workers)
    workers = min(parse_workers, len(paths))
    logger.info("Parsing %s statement files with %s worker process(es)", len(paths), workers)
    if workers <= 1:
        for company, path in paths:
            logger.info("Parsing %s", path)
            yield company, path, _parse_statement_frame(company, path)
        return

    # Spawn avoids inheriting database connections or other parent process state.
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as executor:
        pending = deque()
        remaining = iter(paths)

        def submit_next():
            task = next(remaining, None)
            if task is not None:
                company, path = task
                pending.append((company, path, executor.submit(_parse_statement_frame, company, path)))

        try:
            for _ in range(workers * 2):
                submit_next()
            completed = 0
            while pending:
                company, path, future = pending.popleft()
                try:
                    frame = future.result()
                except Exception as exc:
                    raise RuntimeError(f"Failed to parse statement {path}") from exc
                completed += 1
                logger.info("Parsed %s/%s: %s", completed, len(paths), path)
                submit_next()
                yield company, path, frame
        finally:
            for _, _, future in pending:
                future.cancel()


def ingest_statements_postgres(companies, begin_year, begin_quarter, end_year, end_quarter,
                               *, parse_workers=2):
    """Parse local KAP archives into immutable Bronze and canonical Silver rows."""
    _validate_parse_workers(parse_workers)
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

    parsed = list(_parse_statement_frames(paths, parse_workers))

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
            "parse_workers": min(parse_workers, len(paths)),
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
                    if not selected.empty:
                        from services.financial_report_context import (
                            read_report_context,
                            store_context,
                        )

                        end = pd.to_datetime(selected['date'].str.split(' - ').str[-1], format='%d.%m.%Y').max().date()
                        context = read_report_context(path, int(raw['quarter'].iloc[0]), end)
                        for table_name in selected['table'].unique():
                            if table_name in keys:
                                store_context(conn, keys[table_name], run_id, context)
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
            _activate_filing_versions(conn, candidates, run_id)
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
    """Build quarterly financial values and ratios with internal trailing inputs."""
    with connect_metadata_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('bist_finance_price_earnings'))")
        calculation_started = time.perf_counter()
        run_id = _run_start(conn, "gold_financial_calculation", {
            "price_level": [price_level_year, price_level_month],
            "symbols": [company.symbol for company in companies] if companies else None,
        })
        _GOLD_RUN_ID.set(run_id)
        conn.commit()
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('bist_finance_price_earnings'))")
        logger.info(
            "Gold calculation run %s started (price level %s-%02d)",
            run_id, price_level_year, price_level_month,
        )
        from services.financial_values import (
            calculate_financial_values,
            calculate_value_changes,
        )

        stage_started = time.perf_counter()
        logger.info("Gold stage 1/5: calculating values using source periods and measurement bases")
        calculate_financial_values(conn, run_id, price_level_year, price_level_month, companies, CASH_BALANCE_ITEMS)
        logger.info("Gold stage 1/5 complete in %.1f seconds", time.perf_counter() - stage_started)
        conn.execute("ANALYZE gold.financial_values")
        stage_started = time.perf_counter()
        logger.info("Gold stage 2/5: calculating changes across exact adjacent periods")
        calculate_value_changes(conn, run_id)
        logger.info("Gold stage 2/5 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 3/5: calculating financial ratios")
        calculate_financial_ratios(conn, run_id)
        logger.info("Gold stage 3/5 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 4/5: calculating ratio changes")
        calculate_ratio_changes(conn, run_id)
        logger.info("Gold stage 4/5 complete in %.1f seconds", time.perf_counter() - stage_started)

        stage_started = time.perf_counter()
        logger.info("Gold stage 5/5: recording data-quality results and publishing active calculation run")
        conn.execute("""INSERT INTO ops.data_quality_results
            (run_id,check_name,severity,affected_rows,details)
            SELECT %s,'financial_rows_missing_cpi','warning',COUNT(*),
                   jsonb_build_object('price_level_year',%s,'price_level_month',%s)
            FROM gold.financial_values WHERE calculation_run_id=%s AND value_quarter IS NOT NULL
              AND measurement_basis<>'unknown' AND value_real IS NULL""",
            (run_id, price_level_year, price_level_month, run_id))
        for name, condition in (
            ('financial_report_basis_unknown', "measurement_basis='unknown'"),
            ('financial_quarter_unavailable', 'value_quarter IS NULL'),
            ('financial_fx_unavailable', 'value_quarter IS NOT NULL AND value_usd IS NULL'),
        ):
            conn.execute('''INSERT INTO ops.data_quality_results(run_id,check_name,severity,affected_rows)
                SELECT %s,%s,'warning',COUNT(*) FROM gold.financial_values
                WHERE calculation_run_id=%s AND '''+condition, (run_id,name,run_id))
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
        logger.info("Gold stage 5/5 complete in %.1f seconds", time.perf_counter() - stage_started)
    _GOLD_RUN_ID.set(None)
    logger.info(
        "Gold calculation run %s completed in %.1f seconds",
        run_id, time.perf_counter() - calculation_started,
    )
    return run_id


def calculate_gold_financials_postgres(price_level_year, price_level_month, *, companies=None):
    apply_medallion_migration()
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
                              price_level=None, *, parse_workers=2):
    _validate_parse_workers(parse_workers)
    if (begin_year * 4 + begin_quarter) > (end_year * 4 + end_quarter):
        raise ValueError("The begin period must not be later than the end period.")
    target = resolve_postgres_inflation_period(price_level)
    logger.info("Starting local statement ingestion; no downloads will be attempted")
    parsed_count = ingest_statements_postgres(
        companies, begin_year, begin_quarter, end_year, end_quarter,
        parse_workers=parse_workers,
    )
    logger.info("Statement ingestion committed: %s files parsed", parsed_count)
    from services.earnings_per_share import update_eps_postgres

    update_eps_postgres(companies, rebuild_gold=False)
    logger.info("Starting Gold financial calculations")
    run_id = calculate_gold_financials_postgres(*target, companies=companies)
    logger.info("Gold financial calculations committed under run %s", run_id)
    return parsed_count, run_id
