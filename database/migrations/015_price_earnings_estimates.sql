-- Keep verified valuation semantics; expose estimates with explicit quality.
ALTER TABLE gold.price_earnings_values
 ADD COLUMN estimated_eps_ttm numeric(28,12),
 ADD COLUMN estimated_pe_ratio numeric(28,10),
 ADD COLUMN best_available_pe_ratio numeric(28,10)
   GENERATED ALWAYS AS (COALESCE(pe_ratio,estimated_pe_ratio)) STORED,
 ADD COLUMN pe_quality text GENERATED ALWAYS AS (
   CASE WHEN pe_ratio IS NOT NULL THEN 'verified'
        WHEN estimated_pe_ratio IS NOT NULL THEN 'estimated'
        ELSE 'unavailable' END) STORED,
 ADD CHECK (estimated_pe_ratio IS NULL OR
   (estimated_pe_ratio>0 AND estimated_eps_ttm>0 AND original_close_try>0 AND basic_eps_ttm IS NULL));

CREATE OR REPLACE VIEW gold.current_price_earnings AS
SELECT DISTINCT ON (v.company_id,v.fiscal_year,v.fiscal_quarter) v.*
FROM gold.price_earnings_values v JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=v.company_id AND a.run_id=v.financial_run_id
ORDER BY v.company_id,v.fiscal_year,v.fiscal_quarter,v.valuation_run_id DESC;
