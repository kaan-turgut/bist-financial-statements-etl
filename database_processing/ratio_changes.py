
def get_ratio_changes(conn):

    cursor = conn.cursor()

    ratios = [
        "gross_margin",
        "operating_margin",
        "return_on_assets",
        "return_on_equity",
        "current_ratio",
        "cash_ratio",
        "debt_ratio",
        "debt_to_equity_ratio",
        "asset_turnover_ratio",
        "eps",
    ]

    # ratios that have cumulative values
    cumulative_ratios = {
        "gross_margin",
        "operating_margin",
        "return_on_assets",
        "return_on_equity",
        "asset_turnover_ratio",
        "eps",
    }

    # ratios that have TTM values
    ttm_ratios = {
        "gross_margin",
        "operating_margin",
        "return_on_assets",
        "return_on_equity",
        "asset_turnover_ratio",
        "eps",
    }

    cursor.execute("PRAGMA table_info(ratios)")
    cols = [c[1] for c in cursor.fetchall()]

    for ratio in ratios:

        columns = [
            f"{ratio}_qrt_yoy_pct_chg",
            f"{ratio}_qrt_qoq_pct_chg",
        ]

        if ratio in cumulative_ratios:
            columns.append(f"{ratio}_cum_yoy_pct_chg")

        if ratio in ttm_ratios:
            columns.append(f"{ratio}_ttm_qoq_pct_chg")

        for column in columns:
            if column not in cols:
                cursor.execute(
                    f"ALTER TABLE ratios ADD COLUMN {column} REAL"
                )

        queries = [
            {
                "source": f"{ratio}_qrt",
                "target": f"{ratio}_qrt_yoy_pct_chg",
                "partition": "company, quarter",
                "order": "year",
            },
            {
                "source": f"{ratio}_qrt",
                "target": f"{ratio}_qrt_qoq_pct_chg",
                "partition": "company",
                "order": "year, quarter",
            },
        ]

        if ratio in cumulative_ratios:
            queries.append(
                {
                    "source": f"{ratio}_cum",
                    "target": f"{ratio}_cum_yoy_pct_chg",
                    "partition": "company, quarter",
                    "order": "year",
                }
            )

        if ratio in ttm_ratios:
            queries.append(
                {
                    "source": f"{ratio}_ttm",
                    "target": f"{ratio}_ttm_qoq_pct_chg",
                    "partition": "company",
                    "order": "year, quarter",
                }
            )

        for q in queries:

            sql = f"""
            WITH calc AS (
                SELECT
                    company,
                    year,
                    quarter,
                    (
                        {q["source"]} * 1.0 /
                        LAG({q["source"]}) OVER (
                            PARTITION BY {q["partition"]}
                            ORDER BY {q["order"]}
                        ) - 1
                    ) AS {q["target"]}
                FROM ratios
            )
            UPDATE ratios
            SET {q["target"]} = (
                SELECT c.{q["target"]}
                FROM calc c
                WHERE c.company = ratios.company
                  AND c.year = ratios.year
                  AND c.quarter = ratios.quarter
            );
            """

            cursor.execute(sql)

    conn.commit()
