def choose_canonical_rows(df):
    """Choose one preferred filing for each company, period, and statement."""
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
        df[[
            "company", "year", "quarter", "table", "report_kind", "file_path",
            "_report_kind_rank", "_filing_id_sort",
        ]]
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
