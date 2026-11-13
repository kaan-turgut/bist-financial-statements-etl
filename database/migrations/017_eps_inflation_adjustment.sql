ALTER TABLE gold.financial_ratios DROP CONSTRAINT financial_ratios_single_eps_code;
ALTER TABLE gold.financial_ratios ADD CONSTRAINT financial_ratios_single_eps_code
  CHECK (ratio_code IN ('eps','eps_inflation_adj') OR NOT starts_with(ratio_code,'eps'));

ALTER TABLE gold.eps_coverage ADD COLUMN inflation_adjustment jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Keep existing view columns in their original order; append the new evidence.
CREATE OR REPLACE VIEW gold.current_eps_coverage AS
SELECT c.calculation_run_id,c.company_id,c.fiscal_year,c.fiscal_quarter,
 c.estimated_value_quarter,c.estimated_value_ytd,c.estimated_value_ttm,c.assessment,
 e.value_quarter AS verified_value_quarter,e.value_ytd AS verified_value_ytd,e.value_ttm AS verified_value_ttm,
 c.assessment->'quality'->'quarter'->>'status' AS quarter_quality,
 c.assessment->'quality'->'ytd'->>'status' AS ytd_quality,
 c.assessment->'quality'->'ttm'->>'status' AS ttm_quality,
 COALESCE(e.value_quarter,c.estimated_value_quarter) AS best_available_quarter,
 COALESCE(e.value_ytd,c.estimated_value_ytd) AS best_available_ytd,
 COALESCE(e.value_ttm,c.estimated_value_ttm) AS best_available_ttm,
 c.inflation_adjustment
FROM gold.eps_coverage c JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=c.company_id AND a.run_id=c.calculation_run_id
LEFT JOIN gold.eps_values e ON e.calculation_run_id=c.calculation_run_id AND e.company_id=c.company_id
 AND e.fiscal_year=c.fiscal_year AND e.fiscal_quarter=c.fiscal_quarter
 AND e.eps_kind='basic' AND e.share_class='ordinary' AND e.earnings_scope='total';

CREATE OR REPLACE VIEW gold.eps_review_queue AS
SELECT * FROM gold.current_eps_coverage
WHERE quarter_quality<>'verified' OR ytd_quality<>'verified' OR ttm_quality<>'verified';
