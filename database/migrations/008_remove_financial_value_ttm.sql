-- TTM inputs are now calculated only inside the financial-ratio pipeline.
-- Recreate the known view explicitly; do not cascade into consumers' views.
DROP VIEW IF EXISTS gold.current_financial_values;

ALTER TABLE gold.financial_values
    DROP COLUMN IF EXISTS value_ttm,
    DROP COLUMN IF EXISTS value_real_ttm,
    DROP COLUMN IF EXISTS value_real_ttm_qoq_change;

CREATE VIEW gold.current_financial_values AS
SELECT g.* FROM gold.financial_values g
JOIN ops.active_company_versions a ON a.dataset_name='financials'
 AND a.company_id=g.company_id AND a.run_id=g.calculation_run_id;
