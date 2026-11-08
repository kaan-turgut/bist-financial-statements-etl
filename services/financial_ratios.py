"""Calculate financial ratios and publish source-verified EPS independently."""

from services.earnings_per_share import calculate_eps_changes, calculate_eps_ratios


def calculate_financial_ratios(conn, run_id):
    """Require four consecutive, non-null quarters for each trailing flow input.

    Trailing flows and opening/closing balances use the common CPI base in
    value_real. EPS is handled separately using period-specific disclosures.
    """
    conn.execute("""WITH facts AS (
        SELECT company_id, fiscal_year, fiscal_quarter,
            fiscal_year*4+fiscal_quarter AS period_number,
            MIN(period_start) FILTER (WHERE statement_type='income_statement') AS flow_start,
            MAX(period_end) FILTER (WHERE statement_type='income_statement') AS flow_end,
            MIN(measurement_basis) FILTER (WHERE statement_type='income_statement') AS flow_basis,
            MAX(period_end) FILTER (WHERE statement_type='balance_sheet') AS balance_end,
            MIN(measurement_basis) FILTER (WHERE statement_type='balance_sheet') AS balance_basis,
            SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_q,
            SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_ytd,
            SUM(value_real) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat') AS sales_real,
            SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_q,
            SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_ytd,
            SUM(value_real) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti') AS cogs_real,
            SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_q,
            SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_ytd,
            SUM(value_real) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)') AS op_real,
            SUM(value_quarter) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_q,
            SUM(value_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_ytd,
            SUM(value_real) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)') AS profit_real,
            MAX(value_real_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='hasılat'
                AND calculation_details->>'ytd_method'='reported' AND (calculation_details->>'ytd_days')::integer IN (365,366)) AS annual_sales,
            MAX(value_real_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='satışların maliyeti'
                AND calculation_details->>'ytd_method'='reported' AND (calculation_details->>'ytd_days')::integer IN (365,366)) AS annual_cogs,
            MAX(value_real_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='esas faaliyet karı (zararı)'
                AND calculation_details->>'ytd_method'='reported' AND (calculation_details->>'ytd_days')::integer IN (365,366)) AS annual_operating,
            MAX(value_real_ytd) FILTER (WHERE statement_type='income_statement' AND starts_with(line_item_path, 'profit or loss >') AND item='dönem karı (zararı)'
                AND calculation_details->>'ytd_method'='reported' AND (calculation_details->>'ytd_days')::integer IN (365,366)) AS annual_profit,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam dönen varlıklar') AS current_assets,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam kısa vadeli yükümlülükler') AS current_liabilities,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='nakit ve nakit benzerleri') AS cash,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam yükümlülükler') AS liabilities,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam varlıklar') AS assets,
            SUM(value_quarter) FILTER (WHERE statement_type='balance_sheet' AND item='toplam özkaynaklar') AS equity,
            SUM(value_real) FILTER (WHERE statement_type='balance_sheet' AND item='toplam varlıklar') AS assets_real,
            SUM(value_real) FILTER (WHERE statement_type='balance_sheet' AND item='toplam özkaynaklar') AS equity_real
        FROM gold.financial_values WHERE calculation_run_id=%s
        GROUP BY company_id, fiscal_year, fiscal_quarter
    ), dated_facts AS (
        SELECT f.*,LAG(flow_end) OVER (PARTITION BY company_id ORDER BY period_number) AS previous_flow_end
        FROM facts f
    ), trailing_inputs AS (
        SELECT f.*,
            CASE WHEN fiscal_quarter=4 AND annual_sales IS NOT NULL THEN annual_sales
                 WHEN COUNT(sales_real) OVER ttm=4 AND MIN(flow_basis) OVER ttm=MAX(flow_basis) OVER ttm AND COUNT(*) FILTER (WHERE flow_start=previous_flow_end+1) OVER edges=3
                 AND (MAX(flow_end) OVER ttm-MIN(flow_start) OVER ttm) BETWEEN 364 AND 365 THEN SUM(sales_real) OVER ttm END AS sales_ttm,
            CASE WHEN fiscal_quarter=4 AND annual_cogs IS NOT NULL THEN annual_cogs
                 WHEN COUNT(cogs_real) OVER ttm=4 AND MIN(flow_basis) OVER ttm=MAX(flow_basis) OVER ttm AND COUNT(*) FILTER (WHERE flow_start=previous_flow_end+1) OVER edges=3
                 AND (MAX(flow_end) OVER ttm-MIN(flow_start) OVER ttm) BETWEEN 364 AND 365 THEN SUM(cogs_real) OVER ttm END AS cogs_ttm,
            CASE WHEN fiscal_quarter=4 AND annual_operating IS NOT NULL THEN annual_operating
                 WHEN COUNT(op_real) OVER ttm=4 AND MIN(flow_basis) OVER ttm=MAX(flow_basis) OVER ttm AND COUNT(*) FILTER (WHERE flow_start=previous_flow_end+1) OVER edges=3
                 AND (MAX(flow_end) OVER ttm-MIN(flow_start) OVER ttm) BETWEEN 364 AND 365 THEN SUM(op_real) OVER ttm END AS op_ttm,
            CASE WHEN fiscal_quarter=4 AND annual_profit IS NOT NULL THEN annual_profit
                 WHEN COUNT(profit_real) OVER ttm=4 AND MIN(flow_basis) OVER ttm=MAX(flow_basis) OVER ttm AND COUNT(*) FILTER (WHERE flow_start=previous_flow_end+1) OVER edges=3
                 AND (MAX(flow_end) OVER ttm-MIN(flow_start) OVER ttm) BETWEEN 364 AND 365 THEN SUM(profit_real) OVER ttm END AS profit_ttm
        FROM dated_facts f
        WINDOW ttm AS (PARTITION BY company_id ORDER BY period_number
                       RANGE BETWEEN 3 PRECEDING AND CURRENT ROW),
               edges AS (PARTITION BY company_id ORDER BY period_number ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
    ), inputs AS (
        SELECT t.*,
            (opening.assets_real+t.assets_real)/2 AS average_assets_real,
            (opening.equity_real+t.equity_real)/2 AS average_equity_real
        FROM trailing_inputs t
        LEFT JOIN facts opening ON opening.company_id=t.company_id
                               AND opening.period_number=t.period_number-4
                               AND t.flow_end-opening.balance_end BETWEEN 350 AND 380
                               AND opening.balance_basis=t.balance_basis AND t.balance_basis=t.flow_basis
    ), ratios AS (
        SELECT f.company_id, f.fiscal_year, f.fiscal_quarter, v.ratio_code,
               v.q_value, v.ytd_value, v.ttm_value
        FROM inputs f
        CROSS JOIN LATERAL (VALUES
          ('gross_margin', (sales_q-ABS(cogs_q))/NULLIF(sales_q,0), (sales_ytd-ABS(cogs_ytd))/NULLIF(sales_ytd,0), (sales_ttm-ABS(cogs_ttm))/NULLIF(sales_ttm,0)),
          ('operating_margin', op_q/NULLIF(sales_q,0), op_ytd/NULLIF(sales_ytd,0), op_ttm/NULLIF(sales_ttm,0)),
          ('return_on_assets', profit_q/NULLIF(assets,0), profit_ytd/NULLIF(assets,0), profit_ttm/NULLIF(average_assets_real,0)),
          ('return_on_equity', profit_q/NULLIF(equity,0), profit_ytd/NULLIF(equity,0), profit_ttm/NULLIF(average_equity_real,0)),
          ('current_ratio', current_assets/NULLIF(current_liabilities,0), NULL::numeric, NULL::numeric),
          ('cash_ratio', cash/NULLIF(current_liabilities,0), NULL::numeric, NULL::numeric),
          ('debt_ratio', liabilities/NULLIF(assets,0), NULL::numeric, NULL::numeric),
          ('debt_to_equity_ratio', liabilities/NULLIF(equity,0), NULL::numeric, NULL::numeric),
          ('asset_turnover_ratio', sales_q/NULLIF(assets,0), sales_ytd/NULLIF(assets,0), sales_ttm/NULLIF(average_assets_real,0))
        ) AS v(ratio_code,q_value,ytd_value,ttm_value)
    )
    INSERT INTO gold.financial_ratios
      (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_quarter,value_ytd,value_ttm)
    SELECT %s,company_id,fiscal_year,fiscal_quarter,ratio_code,q_value,ytd_value,ttm_value FROM ratios
    ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
    DO UPDATE SET value_quarter=EXCLUDED.value_quarter,value_ytd=EXCLUDED.value_ytd,
                  value_ttm=EXCLUDED.value_ttm,calculated_at=now()""", (run_id, run_id))
    calculate_eps_ratios(conn, run_id)
    from services.price_earnings import calculate_price_earnings

    calculate_price_earnings(conn, run_id, financial_run_id=run_id)


def calculate_ratio_changes(conn, run_id):
    """Compare ratios only with the exact preceding quarter or corresponding year."""
    conn.execute("ANALYZE gold.financial_ratios")
    conn.execute("""WITH previous AS MATERIALIZED (
        SELECT *, fiscal_year*4+fiscal_quarter AS period_number,
            LAG(fiscal_year*4+fiscal_quarter) OVER sequential AS previous_period,
            LAG(fiscal_year) OVER annual AS previous_year,
            LAG(value_quarter) OVER annual AS previous_quarter_annual,
            LAG(value_quarter) OVER sequential AS previous_quarter,
            LAG(value_ytd) OVER annual AS previous_ytd,
            LAG(value_ttm) OVER sequential AS previous_ttm
        FROM gold.financial_ratios WHERE calculation_run_id=%s
        WINDOW sequential AS (PARTITION BY company_id,ratio_code ORDER BY fiscal_year,fiscal_quarter),
               annual AS (PARTITION BY company_id,ratio_code,fiscal_quarter ORDER BY fiscal_year)
    ), changes AS (
        SELECT company_id,fiscal_year,fiscal_quarter,ratio_code,
            CASE WHEN previous_year=fiscal_year-1
                 THEN value_quarter/NULLIF(previous_quarter_annual,0)-1 END AS q_yoy,
            CASE WHEN previous_period=period_number-1
                 THEN value_quarter/NULLIF(previous_quarter,0)-1 END AS q_qoq,
            CASE WHEN previous_year=fiscal_year-1
                 THEN value_ytd/NULLIF(previous_ytd,0)-1 END AS ytd_yoy,
            CASE WHEN previous_period=period_number-1
                 THEN value_ttm/NULLIF(previous_ttm,0)-1 END AS ttm_qoq
        FROM previous
    ) UPDATE gold.financial_ratios g SET quarter_yoy_change=c.q_yoy,
         quarter_qoq_change=c.q_qoq,ytd_yoy_change=c.ytd_yoy,ttm_qoq_change=c.ttm_qoq
      FROM changes c WHERE g.calculation_run_id=%s AND
                         (g.company_id,g.fiscal_year,g.fiscal_quarter,g.ratio_code)=
                           (c.company_id,c.fiscal_year,c.fiscal_quarter,c.ratio_code)""", (run_id, run_id))
    calculate_eps_changes(conn, run_id)
