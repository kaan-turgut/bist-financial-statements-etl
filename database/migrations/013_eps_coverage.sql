-- Estimates are deliberately separate from verified EPS and verified P/E.
CREATE TABLE gold.eps_coverage (
 calculation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
 company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
 fiscal_year integer NOT NULL,
 fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
 estimated_value_quarter numeric(28,12),
 estimated_value_ytd numeric(28,12),
 estimated_value_ttm numeric(28,12),
 assessment jsonb NOT NULL,
 PRIMARY KEY(calculation_run_id,company_id,fiscal_year,fiscal_quarter)
);
CREATE VIEW gold.current_eps_coverage AS
SELECT c.*, e.value_quarter AS verified_value_quarter,e.value_ytd AS verified_value_ytd,e.value_ttm AS verified_value_ttm,
 c.assessment->'quality'->'quarter'->>'status' AS quarter_quality,
 c.assessment->'quality'->'ytd'->>'status' AS ytd_quality,
 c.assessment->'quality'->'ttm'->>'status' AS ttm_quality,
 COALESCE(e.value_quarter,c.estimated_value_quarter) AS best_available_quarter,
 COALESCE(e.value_ytd,c.estimated_value_ytd) AS best_available_ytd,
 COALESCE(e.value_ttm,c.estimated_value_ttm) AS best_available_ttm
FROM gold.eps_coverage c JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=c.company_id AND a.run_id=c.calculation_run_id
LEFT JOIN gold.eps_values e ON e.calculation_run_id=c.calculation_run_id AND e.company_id=c.company_id
 AND e.fiscal_year=c.fiscal_year AND e.fiscal_quarter=c.fiscal_quarter
 AND e.eps_kind='basic' AND e.share_class='ordinary' AND e.earnings_scope='total';

CREATE VIEW gold.eps_review_queue AS
SELECT * FROM gold.current_eps_coverage
WHERE quarter_quality<>'verified' OR ytd_quality<>'verified' OR ttm_quality<>'verified';
