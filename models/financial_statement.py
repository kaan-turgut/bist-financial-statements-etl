from dataclasses import dataclass

from models.company import Company
from read_statements.helpers import parse_interval


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
        # Per-share figures and share counts are not monetary statement amounts.
        # Dedicated EPS ingestion preserves their units and normalization.
        per_share = (df["item"].fillna("") + " " + df["line_item_path"].fillna("")).str.contains(
            r"(?:pay|hisse) başına|ağırlıklı ortalama.*(?:pay|hisse)", case=False, regex=True
        )
        df = df.loc[~per_share].copy()

        # Calculate interval length
        df["interval_days"] = df["date"].apply(parse_interval)
        # Keep both reported quarter and YTD values at the current statement
        # end. Fiscal years can start in the preceding calendar year.
        import pandas as pd

        df["_period_end"] = pd.to_datetime(df["date"].str.split(" - ").str[-1], format="%d.%m.%Y")
        groups = ["company", "year", "quarter", "table"]
        if "file_path" in df:
            groups.append("file_path")
        current_end = df.groupby(groups, dropna=False)["_period_end"].transform("max")
        df = df.loc[df["_period_end"] == current_end].copy()
        df["value"] = df["value"]*df["reporting unit"]
        df = df.drop(["reporting unit", "_period_end", "interval_days"], axis=1, errors="ignore")

        # Set datatypes
        df["year"] = df["year"].astype(int)
        df["quarter"] = df["quarter"].astype(int)
        df["value"] = df["value"].astype(float)
        df[["company", "year", "quarter", "date", "item", "value"]]

        self.df = df

