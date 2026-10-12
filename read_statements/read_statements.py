import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from models.financial_statement import FinancialStatement
from paths import FINANCIAL_STATEMENTS_DIR
from read_statements.obtain_paths import obtain_statement_paths
from read_statements.parse_statement import parse_statement

logger = logging.getLogger(__name__)


def choose_canonical_rows(df):
    if df.empty:
        return df

    df = df.copy()
    report_kind_rank = {
        "Konsolide": 0,
        "Konsolide Olmayan": 1,
        "None": 2,
    }
    df["_report_kind_rank"] = df["report_kind"].map(report_kind_rank).fillna(2)
    df["_filing_id_sort"] = df["filing_id"].fillna(-1)

    source_priority = (
        df[
            [
                "company",
                "year",
                "quarter",
                "table",
                "report_kind",
                "file_path",
                "_report_kind_rank",
                "_filing_id_sort",
            ]
        ]
        .drop_duplicates()
        .sort_values(
            ["company", "year", "quarter", "table", "_report_kind_rank", "_filing_id_sort"],
            ascending=[True, True, True, True, True, False],
        )
        .drop_duplicates(["company", "year", "quarter", "table"], keep="first")
    )

    selected = df.merge(
        source_priority[["company", "year", "quarter", "table", "report_kind", "file_path"]],
        on=["company", "year", "quarter", "table", "report_kind", "file_path"],
        how="inner",
    )

    return selected.drop(columns=["_report_kind_rank", "_filing_id_sort"])


def save_rows(conn, df, companies):
    if df.empty:
        return

    first_company = companies[0]
    statement = FinancialStatement(first_company, None, None, df)
    statement.split()
    statement.save(conn)


def read_statements(companies, begin_year, begin_quarter, end_year, end_quarter, conn,
                    base_folder_path=FINANCIAL_STATEMENTS_DIR, statement_paths_by_company=None):
    statements = []
    with ThreadPoolExecutor(max_workers=4) as executor:  # try 4–8 threads
        futures = []
        for company in companies:
            logger.info("Submitting statement parse tasks for %s", company.symbol)
            if statement_paths_by_company is None:
                full_folder_path = base_folder_path / company.symbol
                statement_paths = obtain_statement_paths(
                    full_folder_path, begin_year, begin_quarter, end_year, end_quarter
                )
            else:
                statement_paths = statement_paths_by_company.get(company.symbol, [])

            for file in statement_paths:
                futures.append(executor.submit(parse_statement, file, company))

        for future in as_completed(futures):
            # parse_statement function creates a FinancialStatement object and returns it
            statement = future.result()
            statement.clean()
            statements.append(statement)

    if not statements:
        return

    rows = pd.concat([statement.df for statement in statements], ignore_index=True)
    rows = choose_canonical_rows(rows)
    save_rows(conn, rows, companies)
