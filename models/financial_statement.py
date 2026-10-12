from dataclasses import dataclass

from models.company import Company
from read_statements.helpers import parse_date_year, parse_interval


STATEMENT_METADATA_COLUMNS = {
    "section": "TEXT",
    "line_item_path": "TEXT",
    "source_row_id": "TEXT",
    "source_column_id": "TEXT",
    "column_label": "TEXT",
    "report_kind": "TEXT",
    "file_path": "TEXT",
    "source_filename": "TEXT",
    "filing_id": "INTEGER",
}


def ensure_statement_columns(conn, table):
    existing = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if not existing:
        return

    for column, column_type in STATEMENT_METADATA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


@dataclass(init=False)
class FinancialStatement:

    def __init__(
        self,
        company: Company, year: int, quarter: int, dataframe, file_path: str | None = None):

        self.company = company
        self.year = year
        self.quarter = quarter
        self.file_path = file_path
        self.df = dataframe

        self.income_statement = None
        self.balance_sheet = None
        self.cash_flow = None
        self.equity_change = None

    
    def clean(self):

        df = self.df

        # Calculate interval length
        df["interval_days"] = df["date"].apply(parse_interval)
        # Drop rows where interval > 100 days, where the table is not statement of cash flows or changes in equity, or the quarter is not 4
        df = df[(df["interval_days"].isna()) | 
                                (df["interval_days"] <= 100) | 
                                (df["table"].isin(["Statement of Cash Flows", "Statement of Change in Equity"])) |
                                    (df["quarter"] == 4)].copy()
        
        df.loc[(df["quarter"] == 4) & (df["interval_days"] > 320) & (df["table"] == "Income Statement"), "quarter"] = 1234

        # Parse the year the data row is for
        df.loc[:, "date_year"] = df["date"].apply(parse_date_year)

        # Drop the rows that are given for comparison with past periods (Reporting year != year(Date))
        df = df[df["date_year"] == df["year"]].copy()
        df["value"] = df["value"]*df["reporting unit"]
        df = df.drop(["reporting unit", "date_year", "interval_days"], axis=1, errors="ignore")

        # Set datatypes
        df["year"] = df["year"].astype(int)
        df["quarter"] = df["quarter"].astype(int)
        df["value"] = df["value"].astype(float)
        df[["company", "year", "quarter", "date", "item", "value"]]

        self.df = df

    
    def split(self):

        df = self.df

        # Seperate the dfs with respect to table
        df_income_statement = df[df["table"] == "Income Statement"].drop_duplicates().drop("table", axis=1)
        df_balance_sheet = df[df["table"] == "Balance Sheet"].drop_duplicates().drop("table", axis=1)
        df_cash_flow = df[df["table"] == "Statement of Cash Flows"].drop_duplicates().drop("table", axis=1)
        df_equity_change = df[df["table"] == "Statement of Change in Equity"].drop_duplicates().drop("table", axis=1)

        self.income_statement = df_income_statement
        self.balance_sheet = df_balance_sheet
        self.cash_flow = df_cash_flow
        self.equity_change = df_equity_change


    def save(self, conn):
        for table in ["income_statement", "balance_sheet", "cash_flow", "equity_change"]:
            ensure_statement_columns(conn, table)

        columns = [
            "company",
            "year",
            "quarter",
            "date",
            "section",
            "line_item_path",
            "source_row_id",
            "source_column_id",
            "column_label",
            "item",
            "value",
            "report_kind",
            "file_path",
            "source_filename",
            "filing_id",
        ]

        df = self.income_statement[columns]

        df.to_sql(
            "income_statement",
            conn,
            if_exists="append",
            index=False
        )

        df = self.balance_sheet[columns]

        df.to_sql(
            "balance_sheet",
            conn,
            if_exists="append",
            index=False
        )

        df = self.cash_flow[columns]

        df.to_sql(
            "cash_flow",
            conn,
            if_exists="append",
            index=False
        )

        df = self.equity_change[columns]

        df.to_sql(
            "equity_change",
            conn,
            if_exists="append",
            index=False
        )
