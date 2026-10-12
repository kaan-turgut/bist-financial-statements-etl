import logging
import sqlite3
from pathlib import Path

from database_processing.changes import get_changes
from database_processing.helpers import backup_sqlite, resolve_inflation_period
from database_processing.inflate import inflate_all
from database_processing.postprocess import clean_duplicates, decumulate
from database_processing.ratio_changes import get_ratio_changes
from database_processing.ratios import calculate_ratios
from database_processing.ttm import calculate_ttm
from paths import DATABASE_PATH, FINANCIAL_STATEMENTS_DIR
from read_statements.obtain_paths import (
    obtain_statement_paths,
    statement_period_from_path,
)
from read_statements.read_statements import read_statements

logger = logging.getLogger(__name__)


def expected_periods(begin_year, begin_quarter, end_year, end_quarter):
    start = begin_year * 4 + begin_quarter - 1
    end = end_year * 4 + end_quarter - 1
    return [(period // 4, period % 4 + 1) for period in range(start, end + 1)]


def preflight_statement_archives(companies, begin_year, begin_quarter, end_year, end_quarter):
    requested_periods = set(expected_periods(begin_year, begin_quarter, end_year, end_quarter))
    paths_by_company = {}
    periods_by_company = {}
    missing = {}

    for company in companies:
        paths = obtain_statement_paths(
            FINANCIAL_STATEMENTS_DIR / company.symbol,
            begin_year,
            begin_quarter,
            end_year,
            end_quarter,
        )
        available = {statement_period_from_path(path) for path in paths}
        paths_by_company[company.symbol] = paths
        periods_by_company[company.symbol] = available
        absent = sorted(requested_periods - available)
        if absent:
            missing[company.symbol] = absent

    total_paths = sum(map(len, paths_by_company.values()))
    if total_paths == 0:
        raise FileNotFoundError(
            "No local statement files were found for the selected companies and period. "
            "Nothing was changed in the database."
        )

    if missing:
        examples = [
            f"{symbol} {year} Q{quarter}"
            for symbol, periods in missing.items()
            for year, quarter in periods
        ]
        shown = ", ".join(examples[:20])
        suffix = f" (and {len(examples) - 20} more)" if len(examples) > 20 else ""
        logger.warning(
            "Local statement archives are missing for %s requested company-periods: %s%s. "
            "Existing database rows for those missing periods will be preserved.",
            len(examples), shown, suffix,
        )

    logger.info("Preflight found %s local statement files across %s companies", total_paths, len(companies))
    return paths_by_company, periods_by_company

def clear_company_data(
    conn, companies, begin_year, begin_quarter, end_year, end_quarter, periods_by_company=None
):
    symbols = [company.symbol for company in companies]
    if not symbols:
        return

    placeholders = ",".join("?" for _ in symbols)
    tables = [
        "income_statement",
        "balance_sheet",
        "cash_flow",
        "equity_change",
        "ratios",
    ]

    existing_tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    for table in tables:
        if table not in existing_tables:
            continue
        if periods_by_company is None:
            begin_period = begin_year * 4 + begin_quarter
            end_period = end_year * 4 + end_quarter
            conn.execute(
                f"DELETE FROM {table} WHERE company IN ({placeholders}) "
                "AND (year * 4 + quarter) BETWEEN ? AND ?",
                [*symbols, begin_period, end_period],
            )
            continue

        for symbol in symbols:
            periods = sorted(periods_by_company.get(symbol, set()))
            if not periods:
                continue
            conditions, params = [], []
            for year, quarter in periods:
                quarter_condition = "quarter IN (?, 1234)" if quarter == 4 else "quarter = ?"
                conditions.append(f"(year = ? AND {quarter_condition})")
                params.extend((year, quarter))
            conn.execute(
                f"DELETE FROM {table} WHERE company = ? AND ({' OR '.join(conditions)})",
                [symbol, *params],
            )

    conn.commit()


def update_capital_structure(conn, companies):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capital_structure (
            company TEXT PRIMARY KEY,
            num_shares_outstanding REAL
        )
    """)

    rows = [
        (company.symbol, company.shares_outstanding)
        for company in companies
        if company.shares_outstanding is not None
    ]

    conn.executemany("""
        INSERT INTO capital_structure (company, num_shares_outstanding)
        VALUES (?, ?)
        ON CONFLICT(company) DO UPDATE SET
            num_shares_outstanding = excluded.num_shares_outstanding
    """, rows)
    conn.commit()


def update_database(companies, begin_year, begin_quarter, end_year, end_quarter,
                    price_level=None, db_path=DATABASE_PATH):
    db_path = Path(db_path)
    start_period = begin_year * 4 + begin_quarter
    end_period = end_year * 4 + end_quarter
    if begin_quarter not in range(1, 5) or end_quarter not in range(1, 5):
        raise ValueError("begin_quarter and end_quarter must be between 1 and 4")
    if start_period > end_period:
        raise ValueError("The begin period must not be later than the end period")

    price_level = resolve_inflation_period(price_level, db_path)
    statement_paths_by_company, periods_by_company = preflight_statement_archives(
        companies, begin_year, begin_quarter, end_year, end_quarter
    )
    backup_sqlite(db_path)
    
    with sqlite3.connect(db_path, timeout=30) as conn:

        clear_company_data(
            conn, companies, begin_year, begin_quarter, end_year, end_quarter,
            periods_by_company=periods_by_company,
        )

        read_statements(
            companies, begin_year, begin_quarter, end_year, end_quarter, conn,
            statement_paths_by_company=statement_paths_by_company,
        )

        clean_duplicates(conn)

        decumulate(conn, create_backup=False)

        price_level_year, price_level_month = price_level
        inflate_all(conn, year=price_level_year, month=price_level_month, create_backup=False)

        calculate_ttm(conn)

        get_changes(conn)

        update_capital_structure(conn, companies)

        calculate_ratios(conn)

        get_ratio_changes(conn)
