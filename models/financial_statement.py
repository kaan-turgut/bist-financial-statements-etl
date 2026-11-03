from dataclasses import dataclass

from models.company import Company
from read_statements.helpers import parse_date_year, parse_interval


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

