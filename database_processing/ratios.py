import pandas as pd


def calculate_ratios(conn):
    # Calculate ratios for all company-year-quarter and write them to the ratios table
    cursor = conn.cursor()

    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'hasılat' AND section = 'profit or loss' THEN value END) AS sales,
                SUM(CASE WHEN item = 'satışların maliyeti' AND section = 'profit or loss' THEN value END) AS cogs
            FROM income_statement
            GROUP BY company, year, quarter
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.sales) AS ttm_sales,
                SUM(ABS(b2.cogs)) AS ttm_cogs
            FROM base b1
            JOIN base b2 
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            (b.sales - ABS(b.cogs)) * 1.0 / b.sales  AS gross_margin_qrt,
            (SUM(b.sales) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) 
            - 
            ABS(SUM(b.cogs) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ))
            ) * 1.0 /
            SUM(b.sales) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS gross_margin_cum,
            (ttm.ttm_sales - ttm.ttm_cogs) * 1.0 / ttm.ttm_sales AS gross_margin_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
            """)

    df_grossmargin = pd.DataFrame(data, columns=["company", "year", "quarter", "gross_margin_qrt", "gross_margin_cum", "gross_margin_ttm"])


    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'esas faaliyet karı (zararı)' AND section = 'profit or loss' THEN value END) AS operational_profit,
                SUM(CASE WHEN item = 'hasılat' AND section = 'profit or loss' THEN value END) AS sales
            FROM income_statement
            GROUP BY company, year, quarter
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.operational_profit) AS ttm_operational_profit,
                SUM(b2.sales) AS ttm_sales
            FROM base b1
            JOIN base b2
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            b.operational_profit * 1.0 / b.sales AS operating_margin_qrt,
            SUM(b.operational_profit) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) * 1.0 /
            SUM(b.sales) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS operating_margin_cum,
            (ttm.ttm_operational_profit) * 1.0 / ttm.ttm_sales AS operating_margin_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
                """)

    df_operatingmargin = pd.DataFrame(data, columns=["company", "year", "quarter", "operating_margin_qrt", "operating_margin_cum", "operating_margin_ttm"])


    data = cursor.execute("""
        WITH base AS (
            SELECT
                i.company,
                i.year,
                i.quarter,
                i.value AS profit,
                b.value AS assets
            FROM income_statement AS i
            JOIN balance_sheet AS b
                ON i.company = b.company 
            AND i.year = b.year 
            AND i.quarter = b.quarter
            WHERE
                i.item = "dönem karı (zararı)"
                AND i.section = "profit or loss"
                AND b.item = "toplam varlıklar"
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.profit) AS ttm_profit,
                b1.assets AS assets  -- use current quarter assets
            FROM base b1
            JOIN base b2
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) 
                BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            b.profit / b.assets AS return_on_assets_qrt,
            SUM(b.profit) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) / b.assets AS return_on_assets_cum,
            ttm.ttm_profit / ttm.assets AS return_on_assets_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
        """)

    df_returnonassets = pd.DataFrame(data, columns=["company", "year", "quarter", "return_on_assets_qrt", "return_on_assets_cum", "return_on_assets_ttm"])


    data = cursor.execute("""
        WITH base AS (
            SELECT
                i.company,
                i.year,
                i.quarter,
                i.value AS profit,
                b.value AS equity
            FROM income_statement AS i
            JOIN balance_sheet AS b
                ON i.company = b.company 
            AND i.year = b.year 
            AND i.quarter = b.quarter
            WHERE
                i.item = "dönem karı (zararı)"
                AND i.section = "profit or loss"
                AND b.item = "toplam özkaynaklar"
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.profit) AS ttm_profit,
                b1.equity AS equity  -- use current quarter equity, do not sum
            FROM base b1
            JOIN base b2
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) 
                BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            b.profit / b.equity AS return_on_equity_qrt,
            SUM(b.profit) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) / b.equity AS return_on_equity_cum,
            ttm.ttm_profit / ttm.equity AS return_on_equity_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
        """)

    df_returnonequity = pd.DataFrame(data, columns=["company", "year", "quarter", "return_on_equity_qrt", "return_on_equity_cum", "return_on_equity_ttm"])


    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'toplam dönen varlıklar' THEN value END) AS current_assets,
                SUM(CASE WHEN item = 'toplam kısa vadeli yükümlülükler' THEN value END) AS short_term_debt
            FROM balance_sheet
            GROUP BY company, year, quarter
        )
        SELECT
            company,
            year,
            quarter,
            current_assets * 1.0 / short_term_debt AS current_ratio_qrt
        FROM base
        ORDER BY company, year, quarter;
            """)

    df_currentratio = pd.DataFrame(data, columns=["company", "year", "quarter", "current_ratio_qrt"])

    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'nakit ve nakit benzerleri' THEN value END) AS cash_and_cash_eq,
                SUM(CASE WHEN item = 'toplam kısa vadeli yükümlülükler' THEN value END) AS short_term_debt
            FROM balance_sheet
            GROUP BY company, year, quarter
        )
        SELECT
            company,
            year,
            quarter,
            cash_and_cash_eq * 1.0 / short_term_debt AS cash_ratio_qrt
        FROM base
        ORDER BY company, year, quarter;
            """)

    df_cashratio = pd.DataFrame(data, columns=["company", "year", "quarter", "cash_ratio_qrt"])

    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'toplam yükümlülükler' THEN value END) AS total_debt,
                SUM(CASE WHEN item = 'toplam varlıklar' THEN value END) AS total_assets
            FROM balance_sheet
            GROUP BY company, year, quarter
        )
        SELECT
            company,
            year,
            quarter,
            total_debt * 1.0 / total_assets AS debt_ratio_qrt
        FROM base
        ORDER BY company, year, quarter;
                """)

    df_debtratio = pd.DataFrame(data, columns=["company", "year", "quarter", "debt_ratio_qrt"])

    data = cursor.execute("""
        WITH base AS (
            SELECT
                company,
                year,
                quarter,
                SUM(CASE WHEN item = 'toplam yükümlülükler' THEN value END) AS total_debt,
                SUM(CASE WHEN item = 'toplam özkaynaklar' THEN value END) AS total_equity
            FROM balance_sheet
            GROUP BY company, year, quarter
        )
        SELECT
            company,
            year,
            quarter,
            total_debt * 1.0 / total_equity AS equity_ratio_qrt
        FROM base
        ORDER BY company, year, quarter;
            """)

    df_debttoequity = pd.DataFrame(data, columns=["company", "year", "quarter", "debt_to_equity_ratio_qrt"])

    data = cursor.execute("""
        WITH base AS (
            SELECT
                i.company,
                i.year,
                i.quarter,
                i.value AS sales,
                b.value AS assets
            FROM income_statement AS i
            JOIN balance_sheet AS b
                ON i.company = b.company 
            AND i.year = b.year 
            AND i.quarter = b.quarter
            WHERE
                i.item = "hasılat"
                AND i.section = "profit or loss"
                AND b.item = "toplam varlıklar"
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.sales) AS ttm_sales,
                b1.assets AS assets  -- do NOT sum assets
            FROM base b1
            JOIN base b2
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) 
                BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            b.sales / b.assets AS asset_turnover_ratio_qrt,
            SUM(b.sales) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) / b.assets AS asset_turnover_ratio_cum,
            ttm.ttm_sales / ttm.assets AS asset_turnover_ratio_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
            """)
    df_assetturnover = pd.DataFrame(data, columns=["company", "year", "quarter", "asset_turnover_ratio_qrt", "asset_turnover_ratio_cum", "asset_turnover_ratio_ttm"])

    data = cursor.execute("""
        WITH base AS (
            SELECT
                i.company,
                i.year,
                i.quarter,
                i.value AS profit,
                c.num_shares_outstanding AS num_shares_outstanding
            FROM income_statement AS i
            JOIN capital_structure AS c
                ON i.company = c.company
            WHERE
                i.item = "dönem karı (zararı)"
                AND i.section = "profit or loss"
        ),
        ttm AS (
            SELECT
                b1.company,
                b1.year,
                b1.quarter,
                SUM(b2.profit) AS ttm_profit,
                b1.num_shares_outstanding AS num_shares_outstanding  -- do NOT sum shares
            FROM base b1
            JOIN base b2
            ON b1.company = b2.company
            AND (b2.year * 4 + b2.quarter) 
                BETWEEN (b1.year * 4 + b1.quarter - 3) AND (b1.year * 4 + b1.quarter)
            GROUP BY b1.company, b1.year, b1.quarter
        )
        SELECT
            b.company,
            b.year,
            b.quarter,
            b.profit / b.num_shares_outstanding AS eps_qrt,
            SUM(b.profit) OVER (
                PARTITION BY b.company, b.year
                ORDER BY b.quarter
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) / b.num_shares_outstanding AS eps_cum,
            ttm.ttm_profit / ttm.num_shares_outstanding AS eps_ttm
        FROM base b
        JOIN ttm ON b.company = ttm.company AND b.year = ttm.year AND b.quarter = ttm.quarter
        ORDER BY b.company, b.year, b.quarter;
        """)

    df_eps = pd.DataFrame(data, columns=["company", "year", "quarter", "eps_qrt", "eps_cum", "eps_ttm"])

    df_ratios = df_grossmargin.merge(df_operatingmargin, on=["company","year","quarter"], how="outer") \
                            .merge(df_returnonassets, on=["company","year","quarter"], how="outer") \
                            .merge(df_returnonequity, on=["company","year","quarter"], how="outer") \
                            .merge(df_currentratio, on=["company","year","quarter"], how="outer") \
                            .merge(df_cashratio, on=["company","year","quarter"], how="outer") \
                            .merge(df_debtratio, on=["company","year","quarter"], how="outer") \
                            .merge(df_debttoequity, on=["company","year","quarter"], how="outer") \
                            .merge(df_assetturnover, on=["company","year","quarter"], how="outer") \
                            .merge(df_eps, on=["company","year","quarter"], how="outer")

    df_ratios.to_sql("ratios", con=conn, if_exists="replace", index=False)

    conn.commit()
